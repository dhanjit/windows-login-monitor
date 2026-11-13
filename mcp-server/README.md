# Windows Login Monitor — MCP Server

A local MCP server that exposes this PC's login monitor to AI agents over
**stdio**: your client starts the executable and talks to it on stdin/stdout.

No port, no password, no background service. Anything that can run this program
could already read the same event log directly, so there is nothing for it to
authenticate. If you want these tools reachable from elsewhere, front them with
a server of your own behind an authenticated edge — see
[dhanjit/blackreach](https://github.com/dhanjit/blackreach).

## Supported Windows

| | Minimum | Tested on |
|---|---|---|
| OS | Windows 10 (build 1809) or Windows 11 — x64 | Windows 11 24H2 (build 26100) |
| PowerShell | 5.1 (built into Windows 10/11) | 5.1.26100.8115 |
| Python (only if running from source; not needed for winget install) | 3.10+ | 3.10.21, 3.11.16, 3.12.12, 3.13.15 |
| MCP SDK | 2.2.0+ | 2.2.0 |

Windows Server 2019/2022 should work but is not part of the test matrix. ARM64 Windows is not supported (PyInstaller bundles for x64 only).

```
┌──────────────────────────────────────┐
│  Your MCP client (Claude Code, ...)  │
└──────────────────┬───────────────────┘
                   │ spawns the process,
                   │ JSON-RPC on stdin/stdout
   ┌───────────────▼────────────────┐
   │ windows-login-monitor-mcp.exe  │
   │   MCPServer over stdio         │
   │   no port, no key, no service  │
   └───────────────┬────────────────┘
                   │
   ┌───────────────┼───────────────┐
   ▼               ▼               ▼
C:\Scripts\    Security event   Phone on LAN /
login.log     log (4624 / 4801)    ntfy.sh
```

## Tools

| Name | Effect | Args |
|---|---|---|
| `wlm_get_login_log` | Tail `C:\Scripts\login.log` | `lines: int = 20` |
| `wlm_get_recent_logons` | Query Security events 4624 + 4801 | `hours: int = 24`, `limit: int = 50` |
| `wlm_check_phone_home` | Ping the configured phone IP | none |
| `wlm_send_phone_alert` | POST a notification to the ntfy topic | `body`, `title`, `priority`, `tags` |
| `wlm_get_monitor_status` | Snapshot: task / audit policy / config / log | none |

## Install

### Option A: download the installer from GitHub Releases

1. Go to [Releases](https://github.com/dhanjit/windows-login-monitor/releases/latest).
2. Download `WindowsLoginMonitorMcp-Setup-<version>.exe` and run it (it requests admin via UAC).

The installer copies the exe, adds you to **Event Log Readers** so the tools can
read the Security log, and prints the registration line. No Python needed — the
server is a self-contained bundled exe.

### Option B: winget (once published)

```powershell
winget install Dhanjit.WindowsLoginMonitorMcp
```

### Option C: from source

```powershell
cd mcp-server
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py     # that is the whole thing
```

Nothing to configure: there is no `.env`, no key and no service. You will want
to be in the local **Event Log Readers** group for `wlm_get_recent_logons`.

## Register it with a client

```powershell
claude mcp add windows-login-monitor -- "$env:ProgramFiles\WindowsLoginMonitorMcp\windows-login-monitor-mcp.exe"
```

Any MCP client works the same way: use that executable as the command, with no
arguments. From source, the command is your Python and `server.py`.

## Verify

```powershell
python build\stdio_smoke.py "$env:ProgramFiles\WindowsLoginMonitorMcp\windows-login-monitor-mcp.exe"
```

Speaks real MCP to the server and lists its tools. Useful mainly to prove a
build works — a windowed build has no usable stdout and would fail here.

## Uninstall

`winget uninstall Dhanjit.WindowsLoginMonitorMcp`, or Apps & features. Remove
the client registration with `claude mcp remove windows-login-monitor`.

## Security notes

- **There is no credential**, because there is nothing listening. The process is
  started by whoever runs it, and the OS decides who that may be.
- `wlm_get_recent_logons` needs Security-log access, which is why the installer
  adds you to **Event Log Readers**. That membership outlives an uninstall of
  this package only if you leave it; `uninstall-helper.ps1` removes it.
- `wlm_send_phone_alert` lets any client that can reach this server push
  notifications to your phone. Intentional, but worth knowing.
- Versions up to 0.1.3 shipped an HTTP server with OAuth 2.1 and an owner key.
  Upgrading removes the service, the `.env` and the key. If you ran **0.1.0**,
  that key was also written to a world-readable `FIRST-RUN.txt`
  ([#2](https://github.com/dhanjit/windows-login-monitor/issues/2)) — treat it
  as disclosed on any machine with other local accounts.
