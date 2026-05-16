# Windows Login Monitor — MCP Server

A local-first MCP server that exposes this PC's login monitor to AI agents.
Speaks **OAuth 2.1** per the [MCP 2025-06-18 Authorization spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization), so any spec-compliant client (Claude.ai, Claude Code, Cursor, etc.) can connect.

**You host it. You expose it.** This package only ships the server — point any tunnel, reverse proxy, or VPN at `http://127.0.0.1:8765` and you're done.

## Supported Windows

| | Minimum | Tested on |
|---|---|---|
| OS | Windows 10 (build 1809) or Windows 11 — x64 | Windows 11 24H2 (build 26100) |
| PowerShell | 5.1 (built into Windows 10/11) | 5.1.26100.8115 |
| Python (only if running from source; not needed for winget install) | 3.10+ | 3.13.13 |
| MCP SDK | 1.27.0+ | 1.27.1 |

Windows Server 2019/2022 should work but is not part of the test matrix. ARM64 Windows is not supported (PyInstaller bundles for x64 only).

```
┌──────────────────────────────────────┐
│  Your stack: tunnel / proxy / VPN    │
│  (Cloudflare Tunnel, ngrok,          │
│   Tailscale Funnel, Caddy + DDNS…)   │
└──────────────────┬───────────────────┘
                   │
            127.0.0.1:8765
                   │
   ┌───────────────▼────────────────┐
   │ windows-login-monitor-mcp.exe  │
   │   FastMCP + OAuth 2.1          │
   │   /.well-known/oauth-*         │
   │   /authorize  /token  /register│
   │   /mcp  (Bearer-protected)     │
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

## How auth works

The server is a **single-owner OAuth authorization server**. The flow:

1. Client (Claude.ai, Cursor, etc.) discovers the server via [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) at `/.well-known/oauth-protected-resource/mcp`.
2. It registers itself via [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591) Dynamic Client Registration — no pre-shared client_id needed.
3. It redirects you to `/authorize`. You see a small form: *"enter the owner key"*.
4. You paste your `WLM_MCP_OWNER_KEY` (generated at install, also in `.env`). The server sets a short-lived session cookie and issues an authorization code.
5. Client exchanges the code for an access + refresh token (PKCE-protected) at `/token`. It then speaks bearer-token MCP and refreshes silently.

The owner key only gates **the human step** at `/authorize` — it's never handed to clients. Each client gets its own opaque, scoped tokens we issue and validate locally. Tokens persist via `auth_state.json` (clients) plus in-memory state (codes / access / refresh).

## Install

### Option A: winget (recommended)

```powershell
winget install Dhanjit.WindowsLoginMonitorMcp
```

The installer prompts for a public hostname (blank = local-only), generates an owner key, registers a logon scheduled task, and adds you to **Event Log Readers**. Done in <30 s.

### Option B: from source

```powershell
cd mcp-server
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Install-McpServer.ps1   # elevated; same end state as winget
```

### Option C: just run it (dev)

```powershell
echo "WLM_MCP_OWNER_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(32))")" > .env
.\.venv\Scripts\python.exe server.py
```

## Expose it (your stack, your choice)

The server binds to `127.0.0.1:8765` by default. To make it reachable from Claude.ai mobile or any remote agent, pick whatever you already have. Set `WLM_MCP_HOST=0.0.0.0` if your tunnel/proxy needs to reach you on the network bridge rather than loopback (e.g. a container).

| Stack | Time-to-running | Why pick it |
|---|---|---|
| **Cloudflare Tunnel** | 5 min | Free, no port-forward, your own DNS. [Setup](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/). Add ingress `service: http://host.docker.internal:8765` (if cloudflared is in Docker) or `http://127.0.0.1:8765` (native). |
| **Tailscale Funnel** | 3 min | One command — `tailscale funnel 8765`. Public HTTPS, uses your tailnet identity. |
| **ngrok** | 1 min | `ngrok http 8765`. Quick & dirty; random subdomain on free tier. |
| **Caddy + DDNS + port forward** | 30 min | Most DIY. Real domain, free Let's Encrypt cert, no third-party tunnel. |
| **Nothing** | 0 min | If you only use Claude Code on the same PC, `claude mcp add` with `http://127.0.0.1:8765/mcp` works without any exposure. |

**Whatever you pick:** put your public hostname in `WLM_MCP_PUBLIC_URL` and `WLM_MCP_ALLOWED_HOSTS` so OAuth metadata advertises the right URLs and the DNS-rebinding check passes.

## Adding to clients

Once the server is reachable at `https://<your-hostname>/mcp`, any OAuth-capable MCP client connects the same way:

### Claude.ai (web/mobile) — requires Pro+ for custom connectors

1. Settings → Connectors → Add custom connector
2. URL: `https://<your-hostname>/mcp`
3. Tap **Connect**. The browser opens the authorize page.
4. Paste your **owner key** → submit. Claude is redirected back with a code; it exchanges it for tokens.

### Claude Code

```powershell
claude mcp add --transport http windows-login-monitor https://<your-hostname>/mcp
```

It opens the authorize page in your browser on first use.

### Cursor / Windsurf / generic MCP client

Use the client's "remote MCP" / "HTTP MCP" config with URL `https://<your-hostname>/mcp`. Any client that follows the MCP 2025-06-18 spec discovers OAuth automatically.

## Verify

```powershell
# Discovery
Invoke-RestMethod https://<your-hostname>/.well-known/oauth-protected-resource/mcp
Invoke-RestMethod https://<your-hostname>/.well-known/oauth-authorization-server

# 401 challenge — should include WWW-Authenticate with resource_metadata URL
curl.exe -i -X POST https://<your-hostname>/mcp `
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{}"

# Full OAuth + MCP roundtrip
.venv\Scripts\python.exe oauth_smoke.py
```

## Configuration (`.env`)

| Key | Purpose | Example |
|---|---|---|
| `WLM_MCP_OWNER_KEY` | **Required.** Operator's secret for the `/authorize` gate | 256-bit random string |
| `WLM_MCP_PUBLIC_URL` | Issuer URL advertised in OAuth metadata | `https://mcp.example.com` |
| `WLM_MCP_HOST` | Bind address (`0.0.0.0` if a Docker tunnel reaches you via the bridge) | `127.0.0.1` |
| `WLM_MCP_PORT` | Listen port | `8765` |
| `WLM_MCP_ALLOWED_HOSTS` | Comma-separated `Host:` headers to accept (besides loopback) | `mcp.example.com` |

`auth_state.json` next to `.env` persists registered OAuth clients across restarts. Safe to delete; clients re-register on next connect.

## Uninstall

`winget uninstall Dhanjit.WindowsLoginMonitorMcp` removes everything the installer created (files, scheduled task, group membership).

## Security notes

- **Owner key = master password.** Treat as such. Rotate by editing `.env` and restarting; existing OAuth tokens become invalid on restart anyway (in-memory).
- Access tokens live 1 hour; refresh tokens 30 days. Both rotate on every refresh.
- Default bind is loopback only — nothing on your LAN can reach the server unless you change `WLM_MCP_HOST`.
- DNS-rebinding protection is on; your public hostname must be in `WLM_MCP_ALLOWED_HOSTS`.
- `wlm_send_phone_alert` lets any authorized client push notifications to your phone. That's intentional, but worth knowing.
- Server runs as your user at Limited run-level (not admin).
