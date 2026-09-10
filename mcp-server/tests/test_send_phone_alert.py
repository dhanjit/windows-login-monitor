"""wlm_send_phone_alert against a stub ntfy, over a real socket.

SDK 2.x dropped httpx for httpx2, and this tool is the only place the server
speaks HTTP. Mocking the client would test the mock; a loopback server tests
that httpx2 actually sends the body and headers ntfy needs and that the
error path still reads .response off httpx2's HTTPStatusError.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import server

received: list[dict] = []


class _StubNtfy(BaseHTTPRequestHandler):
    #: set per-test; the status the stub answers with.
    status = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        length = int(self.headers.get("Content-Length", 0))
        received.append({
            "path": self.path,
            "body": self.rfile.read(length).decode("utf-8"),
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })
        payload = json.dumps({"id": "stub-msg-id"}).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass  # keep pytest output clean


@pytest.fixture
def ntfy(monkeypatch):
    """Run a stub ntfy on a loopback port and point the server at it."""
    received.clear()
    _StubNtfy.status = 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubNtfy)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "NTFY_BASE", f"http://127.0.0.1:{httpd.server_port}")
    monkeypatch.setattr(server, "load_config",
                        lambda: {"phone_ip": "192.168.1.50", "ntfy_topic": "test-topic"})
    try:
        yield _StubNtfy
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_posts_body_and_headers_to_the_topic(ntfy):
    out = json.loads(await server.wlm_send_phone_alert(
        "build finished", title="CI", priority="high", tags=["warning", "computer"]))
    assert out == {"sent": True, "topic": "test-topic", "id": "stub-msg-id"}

    assert len(received) == 1
    req = received[0]
    assert req["path"] == "/test-topic"
    assert req["body"] == "build finished"
    assert req["headers"]["title"] == "CI"
    assert req["headers"]["priority"] == "high"
    assert req["headers"]["tags"] == "warning,computer"


async def test_reports_the_status_code_when_ntfy_rejects(ntfy):
    ntfy.status = 500
    out = json.loads(await server.wlm_send_phone_alert("nope"))
    assert out["sent"] is False
    assert "500" in out["error"]


async def test_rejects_a_bad_priority_without_calling_out(ntfy):
    out = json.loads(await server.wlm_send_phone_alert("x", priority="urgent"))
    assert out["sent"] is False
    assert "invalid priority" in out["error"]
    assert received == []


async def test_rejects_an_empty_body_without_calling_out(ntfy):
    out = json.loads(await server.wlm_send_phone_alert("   "))
    assert out["sent"] is False
    assert received == []
