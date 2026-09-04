# Build & Release

How to produce the installer, ship a GitHub Release, and submit to winget-pkgs.

## One-time setup

```powershell
winget install --id JRSoftware.InnoSetup
cd ..\mcp-server
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

## Build the installer

From the repo root:

```powershell
.\build\build.ps1 -Version 0.1.0
```

Produces:

| File | What |
|---|---|
| `dist\windows-login-monitor-mcp.exe` | The bundled MCP server (Python + deps frozen by PyInstaller, ~22 MB) |
| `dist\WindowsLoginMonitorMcp-Setup-<ver>.exe` | The Inno Setup installer that drops the exe, prompts for hostname, generates a token, registers the scheduled task, and adds the user to Event Log Readers (~24 MB) |
| (printed) SHA256 of the installer | Paste into the winget manifest |

## What the installer does

1. Asks for the **public hostname** the user's own tunnel/proxy will expose (e.g. `mcp.example.com`) — optional; blank means local-only.
2. Copies `windows-login-monitor-mcp.exe` (the full OAuth 2.1 MCP server, frozen) to `C:\Program Files\WindowsLoginMonitorMcp\`.
3. Generates a 256-bit **owner key**, writes `.env` with `WLM_MCP_OWNER_KEY`, `WLM_MCP_PUBLIC_URL`, host bind settings. `.env` is created with inheritance broken and an ACL of SYSTEM + Administrators + the installing user *before* the key is written to it — files under `Program Files` otherwise inherit read access for `BUILTIN\Users`, i.e. every local account.
4. Adds the installing user to the local **Event Log Readers** group.
5. Registers a scheduled task `LoginMonitorMcp` that starts the exe at logon, restarts on failure.
6. Starts the server now.
7. Deletes any `FIRST-RUN.txt` left by 0.1.0 or earlier, then (optional post-install checkbox) runs `Show-OwnerKey.ps1`, which prints the owner key + connector setup hints to a console.

The key is shown on screen, never written to a second file: through 0.1.0 the installer wrote it into `FIRST-RUN.txt`, which nothing read back and nothing cleaned up ([#2](https://github.com/dhanjit/windows-login-monitor/issues/2)). `Show-OwnerKey.ps1` reads `.env` and can be re-run at any time as the installing user or an admin.

The owner key is the OAuth `/authorize` gate — the operator's secret. Each OAuth client (Claude.ai, Claude Code, etc.) self-registers via [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591) DCR, gets its own scoped access + refresh tokens via the authorization-code + PKCE flow, and refreshes silently.

`winget uninstall Dhanjit.WindowsLoginMonitorMcp` reverses all of that.

## Cut a release

**Pushing the tag is what publishes.** `.github/workflows/release.yml` fires on `v*`, rebuilds the installer on a clean runner from the tagged source, and attaches it to the release.

```powershell
$v = "0.1.0"
git tag "v$v"
git push origin "v$v"          # the Release workflow builds and publishes

# Wait for it to finish, then take the SHA256 from the release body
gh run watch (gh run list --workflow Release --limit 1 --json databaseId -q '.[].databaseId')
gh release edit "v$v" --notes-file notes.md    # only after the workflow is done
```

Do **not** run `gh release create` with a locally built installer. The workflow uses `softprops/action-gh-release`, which overwrites both the asset and the release body — a manual upload races it, and the loser is whichever finished first. That is how v0.1.2 shipped with a manifest hash for a binary that no longer existed: the local build was verified, then replaced by the CI build seconds later.

`.\build\build.ps1 -Version $v` locally is for testing the installer before tagging, not for producing the published artifact.

## Refresh the winget manifest

Before submitting, update `build\manifests\<version>\Dhanjit.WindowsLoginMonitorMcp.installer.yaml`:

- `PackageVersion`: matches the tag
- `InstallerUrl`: the GitHub Releases URL
- `InstallerSha256`: copy from the build script output

Validate locally:

```powershell
winget validate --manifest .\build\manifests\<version>
winget install --manifest .\build\manifests\<version>      # local test install
```

Submit to winget-pkgs:

```powershell
# from a fork of microsoft/winget-pkgs
$dest = "manifests\d\Dhanjit\WindowsLoginMonitorMcp\0.1.0"
mkdir $dest -Force
Copy-Item .\build\manifests\0.1.0\* $dest
git checkout -b add-dhanjit-windows-login-monitor-mcp-0.1.0
git add $dest
git commit -m "New version: Dhanjit.WindowsLoginMonitorMcp 0.1.0"
git push
gh pr create --repo microsoft/winget-pkgs --fill
```

Expect a few days for the automated review bot and a human to approve. Once merged, `winget install Dhanjit.WindowsLoginMonitorMcp` works everywhere.

## Updating an existing version

For 0.1.1, 0.2.0, etc.:

1. `.\build\build.ps1 -Version 0.1.1` — produces a new installer + SHA256.
2. Tag, push, create GitHub Release with the new installer.
3. Copy `build\manifests\0.1.0\` to `build\manifests\0.1.1\` and bump `PackageVersion`, `InstallerUrl`, `InstallerSha256`.
4. Submit another PR (separate version folder in winget-pkgs).

`winget upgrade` picks up the new version automatically once the PR merges.
