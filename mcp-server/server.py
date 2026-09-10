#!/usr/bin/env python3
r"""MCP server exposing the Windows login monitor to agents over stdio.

Tools:
  - wlm_get_login_log        Tail C:\Scripts\login.log
  - wlm_get_recent_logons    Query Security event log (4624 / 4801)
  - wlm_check_phone_home     Ping the configured phone IP on the LAN
  - wlm_send_phone_alert     POST a notification to the configured ntfy topic
  - wlm_get_monitor_status   Scheduled task + audit policy + config + log summary

Transport: stdio only. The MCP client starts this process and speaks JSON-RPC
over stdin/stdout, so there is no port, no credential and no service. Anything
that can run this program could already read the same event log directly.

Remote access is somebody else's job: put a server that fronts this one behind
your own authenticated edge. See dhanjit/blackreach."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

LOGIN_ALERT_SCRIPT = Path(r"C:\Scripts\LoginAlert.ps1")
LOGIN_LOG_FILE = Path(r"C:\Scripts\login.log")
TASK_NAME = "LoginAlert"
NTFY_BASE = "https://ntfy.sh"


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


mcp = MCPServer("windows_login_monitor_mcp")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get login monitor log",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
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
    annotations=ToolAnnotations(
        title="Get recent Windows logon events",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def wlm_get_recent_logons(hours: int = 24, limit: int = 50) -> str:
    """Query the Windows Security event log for recent logon (4624) and unlock (4801) events.

    Requires the server process to have Security-log read access — either as
    Administrator/SYSTEM or by being a member of the local 'Event Log Readers'
    group. The installer adds the installing user to that group.

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
    annotations=ToolAnnotations(
        title="Check whether the phone is on the home LAN",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    )
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
    annotations=ToolAnnotations(
        title="Send a push notification to my phone",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    )
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
        async with httpx2.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{NTFY_BASE}/{topic}", content=body.encode("utf-8"), headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx2.HTTPStatusError as e:
        return json.dumps({"sent": False, "error": f"ntfy HTTP {e.response.status_code}: {e.response.text[:200]}"})
    except Exception as e:
        return json.dumps({"sent": False, "error": f"{type(e).__name__}: {e}"})

    return json.dumps({"sent": True, "topic": topic, "id": data.get("id")})


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get login-monitor status",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
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


def main() -> None:
    """Serve over stdio. The client spawns this process and owns both ends of
    the pipe, so there is nothing to bind, nothing to authenticate and nothing
    to leave running. A `--stdio` argument is accepted and ignored: it is what
    older registrations pass."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
