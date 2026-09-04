"""Unit tests for the owner-key-gate middleware behavior on /authorize.
Uses Starlette's TestClient — no real network."""
import importlib

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


@pytest.fixture
def app_factory(monkeypatch):
    """Build a tiny Starlette app with just the owner-key middleware in front of
    a dummy /authorize handler. Lets us exercise the gate without booting FastMCP."""
    def _factory(owner_key: str):
        monkeypatch.setenv("WLM_MCP_OWNER_KEY", owner_key)
        monkeypatch.setenv("WLM_MCP_PUBLIC_URL", "http://127.0.0.1:8765")
        # Re-import server so module-level constants pick up the env
        import server
        importlib.reload(server)

        async def fake_authorize(request: Request):
            # If we got here, the gate let us through.
            return JSONResponse({"got_through": True})

        app = Starlette(routes=[Route("/authorize", fake_authorize, methods=["GET", "POST"])])
        app.add_middleware(server.OwnerKeyGateMiddleware)
        return app, server

    return _factory


def test_get_authorize_without_cookie_shows_form(app_factory):
    app, _ = app_factory("right-key")
    client = TestClient(app)
    r = client.get("/authorize?client_id=abc&response_type=code")
    assert r.status_code == 200
    assert "owner_key" in r.text  # the form is rendered
    assert 'name="owner_key"' in r.text


def test_post_with_correct_owner_key_sets_cookie_and_redirects(app_factory):
    app, _ = app_factory("right-key")
    client = TestClient(app, follow_redirects=False)
    # The OAuth params ride the form's action, not a hidden field, so a browser
    # POSTs to /authorize?<query> and the gate redirects back to that same URL.
    r = client.post("/authorize?client_id=abc&response_type=code",
                    data={"owner_key": "right-key"})
    assert r.status_code == 303
    assert r.headers["location"] == "/authorize?client_id=abc&response_type=code"
    # session cookie was set
    assert any(c for c in r.cookies if c == "wlm_session")


def test_post_with_wrong_owner_key_returns_401(app_factory):
    app, _ = app_factory("right-key")
    client = TestClient(app)
    r = client.post("/authorize?client_id=abc", data={"owner_key": "wrong-key"})
    assert r.status_code == 401
    # Re-rendered form keeps the OAuth params in its action - dropping them is
    # what broke strict clients before 509c6c5.
    assert 'action="/authorize?client_id=abc"' in r.text


def test_get_with_valid_session_cookie_passes_through(app_factory):
    app, server_mod = app_factory("right-key")
    client = TestClient(app)
    # Use the server's runtime session token; it's regenerated per-process
    cookies = {"wlm_session": server_mod._SESSION_TOKEN}
    r = client.get("/authorize?client_id=abc", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"got_through": True}


def test_get_with_invalid_session_cookie_falls_back_to_form(app_factory):
    app, _ = app_factory("right-key")
    client = TestClient(app)
    cookies = {"wlm_session": "forged-cookie"}
    r = client.get("/authorize?client_id=abc", cookies=cookies)
    assert r.status_code == 200
    assert "owner_key" in r.text


def test_non_authorize_path_passes_through_without_gate(app_factory):
    app, _ = app_factory("right-key")
    # Add another route
    async def health(request):
        return JSONResponse({"ok": True})
    app.routes.append(Route("/somewhere-else", health))
    client = TestClient(app)
    r = client.get("/somewhere-else")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
