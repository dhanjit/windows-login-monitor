"""stdio transport: the client spawns the server, so there is no listener and
no owner key. Drives a real subprocess over stdin/stdout — the point is that it
starts and answers with no credential configured, which the HTTP path refuses
to do."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server.py"


def _env_without_key() -> dict:
    env = dict(os.environ)
    # Empty, not absent: python-dotenv will not override a variable that is
    # already set, so a stray .env cannot smuggle a key into this test.
    env["WLM_MCP_OWNER_KEY"] = ""
    env["WLM_MCP_TOKEN"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _rpc(proc, msg: dict) -> None:
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _read_reply(proc, want_id: int, timeout_lines: int = 40) -> dict:
    """Read newline-delimited JSON until the reply with this id shows up."""
    for _ in range(timeout_lines):
        line = proc.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout before replying")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"non-JSON on stdout, stdio stream is dirty: {line!r}") from exc
        if msg.get("id") == want_id:
            return msg
    raise AssertionError(f"no reply with id={want_id}")


@pytest.fixture
def stdio_server():
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_env_without_key(), text=True, encoding="utf-8", bufsize=1,
        cwd=str(SERVER.parent),
    )
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_starts_and_initializes_without_an_owner_key(stdio_server):
    _rpc(stdio_server, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "stdio-test", "version": "0"}},
    })
    reply = _read_reply(stdio_server, 1)
    assert "result" in reply, reply
    assert reply["result"]["serverInfo"]["name"] == "windows_login_monitor_mcp"


def test_lists_the_same_tools_over_stdio(stdio_server):
    _rpc(stdio_server, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "stdio-test", "version": "0"}},
    })
    _read_reply(stdio_server, 1)
    _rpc(stdio_server, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    _rpc(stdio_server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in _read_reply(stdio_server, 2)["result"]["tools"]}
    assert names == {
        "wlm_get_login_log", "wlm_get_recent_logons", "wlm_check_phone_home",
        "wlm_send_phone_alert", "wlm_get_monitor_status",
    }
