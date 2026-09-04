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
| `dist\WindowsLoginMonitorMcp-Setup-<ver>.exe` | The Inno Setup installer: drops the exe, adds the user to Event Log Readers, clears out anything a pre-0.1.4 install left (~24 MB) |
| (printed) SHA256 of the installer | Paste into the winget manifest |

## What the installer does

1. Copies `windows-login-monitor-mcp.exe` to `C:\Program Files\WindowsLoginMonitorMcp\`.
2. Adds the installing user to the local **Event Log Readers** group, which is
   what `wlm_get_recent_logons` needs to read the Security log.
3. Removes anything a pre-0.1.4 install left behind: the `LoginMonitorMcp`
   scheduled task, `.env` (which held the owner key), and `FIRST-RUN.txt`.
4. Prints the line that registers the server with an MCP client.

That is the whole install. The server speaks stdio — the client starts it — so
there is no key to generate, no service to register and no port to open. Those
existed up to 0.1.3, when this package also shipped an HTTP server with its own
OAuth; the machine's remote MCP door now belongs to
[dhanjit/blackreach](https://github.com/dhanjit/blackreach) instead.

`winget uninstall Dhanjit.WindowsLoginMonitorMcp` reverses all of it.

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

1. `.\build\build.ps1 -Version 0.1.1` locally if you want to test the installer first.
2. Tag and push; the Release workflow builds and publishes. Take the SHA256 from the release body it writes, and only edit the notes once it has finished.
3. Copy `build\manifests\0.1.0\` to `build\manifests\0.1.1\` and bump `PackageVersion`, `InstallerUrl`, `InstallerSha256`.
4. Submit another PR (separate version folder in winget-pkgs).

`winget upgrade` picks up the new version automatically once the PR merges.
