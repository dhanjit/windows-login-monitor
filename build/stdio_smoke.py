#!/usr/bin/env python3
"""Speak stdio MCP to a built server and check it answers.

Usage:  python build/stdio_smoke.py [path-to-exe-or-server.py]

Run against the frozen exe, this catches the failure the unit tests cannot:
a windowed PyInstaller build has no usable stdout, so every JSON-RPC frame
disappears and the server looks alive while answering nothing.

It also calls one read-only tool. Listing proves the decorators registered;
calling proves dispatch works, which under SDK 2.x runs a sync handler on a
worker thread rather than the event loop. The reply is checked, never printed
- the login log names real accounts.

Both pipes are drained by threads and every read has a deadline. Blocking
readline() plus an undrained stderr is a deadlock: SDK 2.x renders a full rich
traceback to stderr when a tool raises, and once that fills the pipe buffer the
server blocks writing it and never answers on stdout. The smoke would then hang
until the CI job timed out instead of failing in a few seconds.
"""
import collections
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOLS = {
    "wlm_get_login_log", "wlm_get_recent_logons", "wlm_check_phone_home",
    "wlm_send_phone_alert", "wlm_get_monitor_status",
}

#: Whole conversation budget. A local file read and a tools/list take well
#: under a second; anything near this is a hang, not slowness.
DEADLINE_SECONDS = 60


def _pump(stream, put) -> None:
    """Move a pipe into memory so the child never blocks writing to it."""
    try:
        for line in stream:
            put(line)
    except (ValueError, OSError):
        pass  # pipe closed under us on kill()


def main() -> int | str:
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else "dist/windows-login-monitor-mcp.exe")
    if not target.exists():
        return f"not found: {target}"
    cmd = [str(target)] if target.suffix == ".exe" else [sys.executable, str(target)]

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
    )

    out: queue.Queue = queue.Queue()
    err: collections.deque = collections.deque(maxlen=400)
    threading.Thread(target=_pump, args=(proc.stdout, out.put), daemon=True).start()
    threading.Thread(target=_pump, args=(proc.stderr, err.append), daemon=True).start()

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def stderr_tail() -> str:
        return "".join(err)[-2000:]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "wlm_get_login_log", "arguments": {"lines": 1}}})

        replies: dict = {}
        deadline = time.monotonic() + DEADLINE_SECONDS
        while {2, 3} - replies.keys() and time.monotonic() < deadline:
            try:
                line = out.get(timeout=0.5).strip()
            except queue.Empty:
                if proc.poll() is not None:
                    break  # server died; no point waiting out the deadline
                continue
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") in (2, 3):
                replies[msg["id"]] = msg

        if 2 not in replies:
            return ("no tools/list reply. A windowed build has no usable stdout.\n"
                    f"stderr tail:\n{stderr_tail()}")
        names = {t["name"] for t in replies[2]["result"]["tools"]}
        if names != TOOLS:
            return f"unexpected tools: {sorted(names)}"

        if 3 not in replies:
            return ("tools/list answered but tools/call did not - dispatch is broken.\n"
                    f"stderr tail:\n{stderr_tail()}")
        call = replies[3]
        if "error" in call:
            return f"tools/call wlm_get_login_log failed: {call['error']}"
        if call["result"].get("isError"):
            return (f"tools/call wlm_get_login_log returned isError.\n"
                    f"stderr tail:\n{stderr_tail()}")

        print(f"stdio smoke OK - {len(names)} tools listed and one called, from {target}")
        return 0
    finally:
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    result = main()
    if result:
        sys.exit(result)
