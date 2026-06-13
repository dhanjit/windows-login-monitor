#!/usr/bin/env python3
"""MCP server exposing the Windows login monitor to agents over HTTP.

Tools:
  - wlm_get_login_log        Tail C:\\Scripts\\login.log
  - wlm_get_recent_logons    Query Security event log (4624 / 4801)
  - wlm_check_phone_home     Ping the configured phone IP on the LAN
  - wlm_send_phone_alert     POST a notification to the configured ntfy topic
  - wlm_get_monitor_status   Scheduled task + audit policy + config + log summary

Hosting model: local-first. Default bind is 127.0.0.1:8765. To make this
reachable from a remote agent (Claude.ai, Cursor, etc.), point your own
tunnel / reverse proxy / VPN at it (Cloudflare Tunnel, Tailscale Funnel,
ngrok, Caddy + DDNS, ...). Set WLM_MCP_PUBLIC_URL + WLM_MCP_ALLOWED_HOSTS
to whatever hostname your stack exposes.

Auth: OAuth 2.1 per the MCP 2025-06-18 Authorization spec.
  - Dynamic Client Registration (RFC 7591) is open: any client can register.
  - /authorize is gated by a single owner key (WLM_MCP_OWNER_KEY) so only the
    operator can complete the authorization step.
  - After that, each client gets its own opaque access + refresh tokens.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import secrets
import uvicorn
from dotenv import load_dotenv
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth_provider import SimpleOAuthProvider

def _install_dir() -> Path:
    """Directory holding the .env file. For PyInstaller-frozen exes this is the
    folder containing the exe (sys.executable); for source runs it's the folder
    holding this script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _state_dir() -> Path:
    """User-writable directory for mutable state (auth_state.json, logs).
    Program Files is read-only for non-admins; use %LOCALAPPDATA% instead."""
    base = os.environ.get("WLM_MCP_STATE_DIR") or os.environ.get("LOCALAPPDATA")
    if not base:
        # Fallback for unusual envs (e.g. SYSTEM) — keep next to the install dir
        return _install_dir()
    d = Path(base) / "WindowsLoginMonitorMcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


load_dotenv(_install_dir() / ".env")

LOGIN_ALERT_SCRIPT = Path(r"C:\Scripts\LoginAlert.ps1")
LOGIN_LOG_FILE = Path(r"C:\Scripts\login.log")
TASK_NAME = "LoginAlert"
NTFY_BASE = "https://ntfy.sh"
HOST = os.environ.get("WLM_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("WLM_MCP_PORT", "8765"))
# Owner key: the operator's secret for the /authorize step. Renamed from
# WLM_MCP_TOKEN; old name still accepted for backwards-compat.
OWNER_KEY = (
    os.environ.get("WLM_MCP_OWNER_KEY", "").strip()
    or os.environ.get("WLM_MCP_TOKEN", "").strip()
)
# Public issuer URL — what clients see in metadata. For local testing this is
# http://127.0.0.1:8765; behind a tunnel it must be the public hostname.
PUBLIC_URL = os.environ.get("WLM_MCP_PUBLIC_URL", f"http://{HOST}:{PORT}").rstrip("/")

# Hosts FastMCP's DNS-rebinding middleware will accept on the Host header.
# Bearer-token auth is what actually keeps strangers out; this list just stops
# DNS-rebind attacks from browsers that haven't authenticated.
# Wildcard form "host:*" allows any port. Add public hostnames (e.g.
# "blackreach.dhanjit.me") via WLM_MCP_ALLOWED_HOSTS as a comma-separated list.
ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "host.docker.internal:*"] + [
    h.strip() for h in os.environ.get("WLM_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
]


def load_config() -> dict:
    """Parse C:\\Scripts\\LoginAlert.ps1 for $PhoneIP and $NtfyTopic."""
    if not LOGIN_ALERT_SCRIPT.exists():
        return {"error": f"LoginAlert.ps1 not found at {LOGIN_ALERT_SCRIPT}. Run Setup-LoginMonitor.ps1 first."}
    text = LOGIN_ALERT_SCRIPT.read_text(encoding="utf-8", errors="replace")
    phone_ip = re.search(r'\$PhoneIP\s*=\s*"([^"]+)"', text)
    topic = re.search(r'\$NtfyTopic\s*=\s*"([^"]+)"', text)
    return {
        "phone_ip": phone_ip.group(1) if phone_ip else None,
        "ntfy_topic": topic.group(1) if topic else None,
    }


def run_powershell(snippet: str, timeout: float = 30.0) -> str:
    """Execute a PowerShell snippet, return stdout. Raises on non-zero exit."""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", snippet,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PowerShell exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


class HealthzMiddleware(BaseHTTPMiddleware):
    """Unauthenticated /healthz probe for the cloudflared / Docker side."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"ok": True})
        return await call_next(request)


class MetadataPatchMiddleware(BaseHTTPMiddleware):
    """Patch the SDK's OAuth metadata so strict clients (Claude.ai) accept it.

    The MCP SDK's build_metadata() hardcodes token_endpoint_auth_methods_supported
    to the client_secret_* methods only — it never advertises 'none', even though
    the server fully supports public clients (DCR with token_endpoint_auth_method
    'none' + PKCE). A public client like Claude.ai rejects an AS that doesn't
    advertise 'none'. We also drop the trailing slash pydantic adds to the issuer.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/.well-known/oauth-"):
            return response

        body = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body += chunk
        try:
            meta = json.loads(body)
        except Exception:
            from starlette.responses import Response as _Resp
            return _Resp(content=body, status_code=response.status_code,
                         media_type=response.media_type)

        if path == "/.well-known/oauth-authorization-server":
            for key in ("token_endpoint_auth_methods_supported",
                        "revocation_endpoint_auth_methods_supported"):
                if meta.get(key) is not None:
                    meta[key] = list(dict.fromkeys(list(meta[key]) + ["none"]))
            if isinstance(meta.get("issuer"), str):
                meta["issuer"] = meta["issuer"].rstrip("/")
        elif path.startswith("/.well-known/oauth-protected-resource"):
            if isinstance(meta.get("authorization_servers"), list):
                meta["authorization_servers"] = [
                    s.rstrip("/") if isinstance(s, str) else s
                    for s in meta["authorization_servers"]
                ]

        return JSONResponse(meta, status_code=response.status_code)


# Session token cookie that proves the owner has authenticated at /authorize.
# Generated fresh at server start; restarting invalidates all sessions.
_SESSION_COOKIE_NAME = "wlm_session"
_SESSION_TOKEN = secrets.token_urlsafe(24)
_SESSION_TTL = 60 * 30  # 30 minutes — enough to complete the auth dance


_AUTH_FORM_HTML = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Windows Login Monitor MCP - Authorize</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 28rem; margin: 4rem auto; padding: 0 1rem; color: #111; }
  h1   { font-size: 1.2rem; margin: 0 0 0.4rem; }
  p    { color: #555; line-height: 1.45; }
  input[type=password] { width: 100%; padding: 0.6rem; font-size: 1rem; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
  button { width: 100%; padding: 0.7rem; font-size: 1rem; background: #111; color: #fff; border: 0; border-radius: 6px; margin-top: 0.6rem; cursor: pointer; }
  .err { color: #b00020; margin-top: 0.5rem; }
  code { background: #f4f4f5; padding: 0.1rem 0.3rem; border-radius: 4px; font-size: 0.85em; }
</style></head><body>
<h1>Authorize <code>__CLIENT__</code></h1>
<p>This client is requesting access to <strong>Windows Login Monitor MCP</strong>. Enter the owner key from <code>.env</code> to approve.</p>
<form method="POST" action="__ACTION__">
  <input type="password" name="owner_key" placeholder="Owner key" autocomplete="off" autofocus>
  __ERR__
  <button type="submit">Authorize</button>
</form>
</body></html>"""


class OwnerKeyGateMiddleware(BaseHTTPMiddleware):
    """Before the SDK's OAuth /authorize handler runs, require the operator to
    prove possession of the owner key.

    The owner-key form POSTs to /authorize WITH the original OAuth query string
    in its `action` attribute, so the POST request itself carries client_id,
    code_challenge, etc. On a correct key we set a short-lived session cookie
    and 303-redirect to that same path+query — the follow-up GET then sails
    through to the SDK handler with all params intact. Relative URLs keep this
    correct behind a TLS-terminating proxy (Cloudflare)."""

    AUTHORIZE_PATH = "/authorize"

    async def dispatch(self, request: Request, call_next):
        if request.url.path != self.AUTHORIZE_PATH:
            return await call_next(request)

        # Already-authed session: pass through to the SDK handler.
        if request.cookies.get(_SESSION_COOKIE_NAME) == _SESSION_TOKEN:
            return await call_next(request)

        # Relative path+query — scheme-agnostic, carries the OAuth params.
        rel = request.url.path + (f"?{request.url.query}" if request.url.query else "")

        if request.method == "POST":
            form = await request.form()
            provided = (form.get("owner_key") or "").strip()
            if OWNER_KEY and secrets.compare_digest(provided, OWNER_KEY):
                resp = RedirectResponse(rel, status_code=303)
                resp.set_cookie(
                    _SESSION_COOKIE_NAME, _SESSION_TOKEN,
                    max_age=_SESSION_TTL, httponly=True, secure=False, samesite="lax",
                )
                return resp
            # Bad key: re-show form with an error.
            return HTMLResponse(_render_form(rel, error="Wrong owner key."), status_code=401)

        # GET without cookie: show the form.
        return HTMLResponse(_render_form(rel), status_code=200)


def _render_form(action_url: str, error: str | None = None) -> str:
    """Render the owner-key form. `action_url` (path+query of the /authorize
    request) becomes the form's action verbatim, so the OAuth params ride the
    POST — no hidden fields, no JS, no escaping games."""
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(action_url).query)
    client_id = (qs.get("client_id", [""])[0])[:60] or "(unknown client)"
    err_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return (
        _AUTH_FORM_HTML
        .replace("__CLIENT__", html.escape(client_id))
        .replace("__ACTION__", html.escape(action_url, quote=True))
        .replace("__ERR__", err_html)
    )


_state_file = _state_dir() / "auth_state.json"
oauth_provider = SimpleOAuthProvider(owner_key=OWNER_KEY, state_file=_state_file) if OWNER_KEY else None

mcp_kwargs: dict = dict(
    transport_security=TransportSecuritySettings(
        allowed_hosts=ALLOWED_HOSTS,
        allowed_origins=ALLOWED_HOSTS,
    ),
)
if oauth_provider:
    mcp_kwargs["auth_server_provider"] = oauth_provider
    mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AnyHttpUrl(PUBLIC_URL),
        resource_server_url=AnyHttpUrl(f"{PUBLIC_URL}/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["mcp"],
    )

mcp = FastMCP("windows_login_monitor_mcp", **mcp_kwargs)


@mcp.tool(
    annotations={
        "title": "Get login monitor log",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def wlm_get_login_log(lines: int = 20) -> str:
    """Return the trailing N lines from C:\\Scripts\\login.log.

    The login log is appended by LoginAlert.ps1 on every logon and unlock event.
    Each line records: timestamp | user=NAME | phoneHome=True|False
    Additional lines starting with 'NTFY SENT' or 'NTFY FAILED' record whether
    a phone notification was actually attempted on that event.

    Args:
        lines: Number of trailing lines to return (default 20, max 1000).

    Returns:
        Plain text, one log entry per line, oldest-first. Returns a placeholder
        message if the log file does not exist yet (no events recorded).
    """
    lines = max(1, min(int(lines), 1000))
    if not LOGIN_LOG_FILE.exists():
        return f"No log file at {LOGIN_LOG_FILE} yet. (LoginAlert.ps1 creates it on first run.)"
    text = LOGIN_LOG_FILE.read_text(encoding="utf-8", errors="replace")
    tail = text.splitlines()[-lines:]
    return "\n".join(tail) if tail else "(log is empty)"


@mcp.tool(
    annotations={
        "title": "Get recent Windows logon events",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def wlm_get_recent_logons(hours: int = 24, limit: int = 50) -> str:
    """Query the Windows Security event log for recent logon (4624) and unlock (4801) events.

    Requires the server process to have Security-log read access — either as
    Administrator/SYSTEM or by being a member of the local 'Event Log Readers'
    group. Install-McpServer.ps1 adds the current user to that group.

    Args:
        hours: Look-back window in hours (default 24, max 168 = 1 week).
        limit: Maximum number of events to return (default 50, max 500).

    Returns:
        JSON array of events, newest first. Each entry: time (ISO 8601),
        event_id (4624 logon or 4801 unlock), logon_type (1 System / 2
        Interactive / 3 Network / 7 Unlock / 10 RemoteInteractive / ...),
        user (DOMAIN\\NAME), workstation (source workstation, often blank).

        On permission error, returns a JSON object {"error": "..."} explaining
        what to do.
    """
    hours = max(1, min(int(hours), 168))
    limit = max(1, min(int(limit), 500))
    ps = f"""
$cutoff = (Get-Date).AddHours(-{hours})
try {{
    $events = Get-WinEvent -FilterHashtable @{{LogName='Security'; Id=4624,4801; StartTime=$cutoff}} -MaxEvents {limit} -ErrorAction Stop
}} catch {{
    if ($_.Exception.Message -like '*No events were found*') {{ "[]"; exit 0 }}
    @{{ error = "Failed to read Security log: $($_.Exception.Message). Run server as Administrator/SYSTEM or add user to 'Event Log Readers'." }} | ConvertTo-Json -Compress
    exit 0
}}
$out = foreach ($e in $events) {{
    $xml = [xml]$e.ToXml()
    $data = @{{}}
    foreach ($d in $xml.Event.EventData.Data) {{ $data[$d.Name] = $d.'#text' }}
    [pscustomobject]@{{
        time        = $e.TimeCreated.ToString("o")
        event_id    = $e.Id
        logon_type  = $data.LogonType
        user        = "$($data.TargetDomainName)\\$($data.TargetUserName)"
        workstation = $data.WorkstationName
    }}
}}
if ($null -eq $out) {{ "[]" }} else {{ ,@($out) | ConvertTo-Json -Compress -Depth 4 }}
"""
    try:
        out = run_powershell(ps, timeout=20).strip()
    except Exception as e:
        return json.dumps({"error": f"PowerShell call failed: {e}"})
    return out or "[]"


@mcp.tool(
    annotations={
        "title": "Check whether the phone is on the home LAN",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
def wlm_check_phone_home() -> str:
    """Ping the configured phone IP and report whether it responded.

    Same check LoginAlert.ps1 does at logon time to decide whether to suppress
    alerts. Useful for an agent to ask "is the user home?" before sending
    a notification or before claiming the PC is unattended.

    Returns:
        JSON object: {"phone_ip": "...", "phone_home": true|false,
        "checked_at": "<ISO 8601 UTC>"}. On config error:
        {"phone_home": null, "error": "..."}.
    """
    cfg = load_config()
    if "error" in cfg:
        return json.dumps({"phone_home": None, "error": cfg["error"]})
    ip = cfg.get("phone_ip")
    if not ip:
        return json.dumps({"phone_home": None, "error": "PhoneIP not configured in LoginAlert.ps1"})
    ps = f'$r = Test-Connection -ComputerName "{ip}" -Count 2 -Quiet -ErrorAction SilentlyContinue; "$r"'
    try:
        out = run_powershell(ps, timeout=10).strip()
    except Exception as e:
        return json.dumps({"phone_ip": ip, "phone_home": None, "error": str(e)})
    return json.dumps({
        "phone_ip": ip,
        "phone_home": out.lower() == "true",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })


@mcp.tool(
    annotations={
        "title": "Send a push notification to my phone",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def wlm_send_phone_alert(
    body: str,
    title: str = "PC Alert",
    priority: str = "default",
    tags: Optional[list[str]] = None,
) -> str:
    """POST a notification to the configured ntfy topic — pushes it to the phone.

    Use this to push any message to the user's phone, not just login alerts:
    build finished, deploy completed, error needs attention, "you left a process
    running at home", etc.

    Args:
        body: Notification body (the main message). Max 4000 chars.
        title: Notification title (default 'PC Alert').
        priority: One of 'min', 'low', 'default', 'high', 'max'. Use 'high' for
            things needing immediate attention, 'low' for FYI.
        tags: Optional list of ntfy tag names (e.g. ['warning', 'computer']);
            they render as emoji on the phone.

    Returns:
        JSON object: {"sent": true, "topic": "...", "id": "..."} on success,
        {"sent": false, "error": "..."} on failure.
    """
    if priority not in {"min", "low", "default", "high", "max"}:
        return json.dumps({"sent": False, "error": f"invalid priority {priority!r}; must be min|low|default|high|max"})
    if not body or not body.strip():
        return json.dumps({"sent": False, "error": "body cannot be empty"})
    body = body[:4000]

    cfg = load_config()
    if "error" in cfg:
        return json.dumps({"sent": False, "error": cfg["error"]})
    topic = cfg.get("ntfy_topic")
    if not topic:
        return json.dumps({"sent": False, "error": "NtfyTopic not configured in LoginAlert.ps1"})

    headers = {"Title": title[:200], "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(t for t in tags if t)[:200]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{NTFY_BASE}/{topic}", content=body.encode("utf-8"), headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return json.dumps({"sent": False, "error": f"ntfy HTTP {e.response.status_code}: {e.response.text[:200]}"})
    except Exception as e:
        return json.dumps({"sent": False, "error": f"{type(e).__name__}: {e}"})

    return json.dumps({"sent": True, "topic": topic, "id": data.get("id")})


@mcp.tool(
    annotations={
        "title": "Get login-monitor status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def wlm_get_monitor_status() -> str:
    """Report whether the login monitor is set up correctly on this PC.

    Use when troubleshooting "why am I not getting alerts" — shows which
    piece is broken (missing task, audit policy off, empty log, etc.).

    Returns:
        JSON object with keys:
          config: {phone_ip, ntfy_topic, ntfy_url}
          scheduled_task: {registered, state, principal, triggers,
                           last_run_time, last_result, next_run_time}
          audit_policy: {subcategory, setting, enabled}
          log: {exists, size_bytes, line_count, last_line}
    """
    cfg = load_config()
    if cfg.get("ntfy_topic"):
        cfg["ntfy_url"] = f"{NTFY_BASE}/{cfg['ntfy_topic']}"

    ps_task = f"""
$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if ($t) {{
    $info = $t | Get-ScheduledTaskInfo
    @{{
        registered     = $true
        state          = "$($t.State)"
        principal      = $t.Principal.UserId
        triggers       = @($t.Triggers | ForEach-Object {{ $_.CimClass.CimClassName }})
        last_run_time  = $info.LastRunTime.ToString("o")
        last_result    = $info.LastTaskResult
        next_run_time  = $info.NextRunTime.ToString("o")
    }} | ConvertTo-Json -Compress
}} else {{
    @{{ registered = $false }} | ConvertTo-Json -Compress
}}
"""
    try:
        task_json = run_powershell(ps_task, timeout=10).strip()
        task_info = json.loads(task_json) if task_json else {"registered": False}
    except Exception as e:
        task_info = {"error": str(e)}

    audit_val: Optional[str]
    audit_enabled: Optional[bool]
    try:
        raw = run_powershell('auditpol /get /subcategory:"Other Logon/Logoff Events" /r', timeout=5)
        # Last non-empty CSV row's "Inclusion Setting" column
        rows = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("Machine Name")]
        audit_val = rows[-1].split(",")[-2] if rows else None
        audit_enabled = audit_val is not None and "Success" in audit_val
    except Exception:
        audit_val = "unknown (auditpol /get requires admin)"
        audit_enabled = None

    log_info: dict = {"exists": False}
    if LOGIN_LOG_FILE.exists():
        text = LOGIN_LOG_FILE.read_text(encoding="utf-8", errors="replace")
        lns = text.splitlines()
        log_info = {
            "exists": True,
            "size_bytes": LOGIN_LOG_FILE.stat().st_size,
            "line_count": len(lns),
            "last_line": lns[-1] if lns else None,
        }

    return json.dumps({
        "config": cfg,
        "scheduled_task": task_info,
        "audit_policy": {
            "subcategory": "Other Logon/Logoff Events",
            "setting": audit_val,
            "enabled": audit_enabled,
        },
        "log": log_info,
    }, indent=2)


def build_app() -> Starlette:
    # Add middleware directly to the FastMCP Starlette app so its lifespan
    # (which initializes the streamable-HTTP session manager) still runs.
    app = mcp.streamable_http_app()
    app.add_middleware(MetadataPatchMiddleware)
    app.add_middleware(OwnerKeyGateMiddleware)
    app.add_middleware(HealthzMiddleware)
    return app


def main() -> None:
    # The frozen exe is built windowless (no console), so stdout/stderr have
    # nowhere to go. Route them to a log file in the user-writable state dir
    # before anything prints — keeps uvicorn logs and tracebacks recoverable.
    if getattr(sys, "frozen", False):
        try:
            fh = open(_state_dir() / "server.log", "a", buffering=1,
                      encoding="utf-8", errors="replace")
            sys.stdout = fh
            sys.stderr = fh
        except OSError:
            pass
    if not OWNER_KEY:
        raise SystemExit(
            "ERROR: WLM_MCP_OWNER_KEY (or legacy WLM_MCP_TOKEN) is empty. "
            "Set it in .env or env vars before starting."
        )
    print(f"[wlm-mcp] starting on http://{HOST}:{PORT}/mcp  (issuer={PUBLIC_URL})", flush=True)
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
