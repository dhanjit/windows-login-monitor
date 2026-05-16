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
3. Generates a 256-bit **owner key**, writes `.env` with `WLM_MCP_OWNER_KEY`, `WLM_MCP_PUBLIC_URL`, host bind settings.
4. Adds the installing user to the local **Event Log Readers** group.
5. Registers a scheduled task `LoginMonitorMcp` that starts the exe at logon, restarts on failure.
6. Starts the server now.
7. Opens `FIRST-RUN.txt` showing the owner key + connector setup hints.

The owner key is the OAuth `/authorize` gate — the operator's secret. Each OAuth client (Claude.ai, Claude Code, etc.) self-registers via [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591) DCR, gets its own scoped access + refresh tokens via the authorization-code + PKCE flow, and refreshes silently.

`winget uninstall Dhanjit.WindowsLoginMonitorMcp` reverses all of that.

## Cut a release

```powershell
$v = "0.1.0"
.\build\build.ps1 -Version $v

# Tag, push, and create the GitHub Release with the installer attached
git tag "v$v"
git push origin "v$v"
gh release create "v$v" "dist\WindowsLoginMonitorMcp-Setup-$v.exe" --notes "..."
```

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
