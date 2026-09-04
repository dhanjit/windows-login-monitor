#!/usr/bin/env python3
"""Speak stdio MCP to a built server and check it answers.

Usage:  python build/stdio_smoke.py [path-to-exe-or-server.py]

Run against the frozen exe, this catches the failure the unit tests cannot:
a windowed PyInstaller build has no usable stdout, so every JSON-RPC frame
disappears and the server looks alive while answering nothing.
"""
import json
import subprocess
import sys
from pathlib import Path

TOOLS = {
    "wlm_get_login_log", "wlm_get_recent_logons", "wlm_check_phone_home",
    "wlm_send_phone_alert", "wlm_get_monitor_status",
}


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else "dist/windows-login-monitor-mcp.exe")
    if not target.exists():
        return f"not found: {target}"
    cmd = [str(target)] if target.suffix == ".exe" else [sys.executable, str(target)]

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
    )

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        for _ in range(40):
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == 2:
                names = {t["name"] for t in msg["result"]["tools"]}
                if names != TOOLS:
                    return f"unexpected tools: {sorted(names)}"
                print(f"stdio smoke OK - {len(names)} tools from {target}")
                return 0
        err = proc.stderr.read()[-2000:] if proc.stderr else ""
        return ("no tools/list reply. A windowed build has no usable stdout.\n"
                f"stderr tail:\n{err}")
    finally:
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    result = main()
    if result:
        sys.exit(result)
