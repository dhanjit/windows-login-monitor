"""End-to-end OAuth 2.1 + MCP smoke test against the local server."""
import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
OWNER_KEY = os.environ["WLM_MCP_OWNER_KEY"]
BASE = "http://127.0.0.1:8765"


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(text)


def main() -> int:
    with httpx.Client(timeout=15.0, follow_redirects=False) as c:
        # 1. Protected-resource metadata (RFC 9728 — per-resource path).
        # Real clients learn this URL from the 401's WWW-Authenticate header.
        r = c.get(f"{BASE}/.well-known/oauth-protected-resource/mcp")
        print(f"[1] /.well-known/oauth-protected-resource/mcp {r.status_code}")
        assert r.status_code == 200, r.text
        rs_meta = r.json()
        assert "authorization_servers" in rs_meta, rs_meta
        as_url = rs_meta["authorization_servers"][0]

        # 2. Authorization-server metadata
        r = c.get(as_url.rstrip("/") + "/.well-known/oauth-authorization-server")
        print(f"[2] /.well-known/oauth-authorization-server {r.status_code}")
        assert r.status_code == 200, r.text
        as_meta = r.json()
        for k in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            assert k in as_meta, f"missing {k} in AS metadata"

        # 3. /mcp without auth -> 401 with WWW-Authenticate pointing at the resource metadata
        r = c.post(f"{BASE}/mcp",
                   json={"jsonrpc":"2.0","id":1,"method":"initialize",
                         "params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}},
                   headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
        print(f"[3] /mcp without auth -> {r.status_code}  WWW-Authenticate: {r.headers.get('www-authenticate')}")
        assert r.status_code == 401

        # 4. Dynamic Client Registration
        reg_body = {
            "client_name": "oauth-smoke",
            "redirect_uris": ["http://127.0.0.1:9999/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "mcp",
        }
        r = c.post(as_meta["registration_endpoint"], json=reg_body)
        print(f"[4] DCR {r.status_code}")
        assert r.status_code in (200, 201), r.text
        client = r.json()
        client_id = client["client_id"]
        print(f"    client_id = {client_id}")

        # 5. /authorize: first GET shows the owner-key form, then POST submits it.
        code_verifier = b64url(secrets.token_bytes(32))
        code_challenge = b64url(hashlib.sha256(code_verifier.encode()).digest())
        state = b64url(secrets.token_bytes(8))
        auth_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:9999/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "mcp",
        }
        auth_url = f"{BASE}/authorize?{urlencode(auth_params)}"
        r = c.get(auth_url)
        print(f"[5a] GET /authorize -> {r.status_code} (expect 200 + form)")
        assert r.status_code == 200 and "owner_key" in r.text

        r = c.post(f"{BASE}/authorize",
                   data={"owner_key": OWNER_KEY, "original_url": auth_url})
        print(f"[5b] POST /authorize (correct key) -> {r.status_code}")
        assert r.status_code == 303, r.text
        # Cookie from the 303 carries the session
        session_cookies = r.cookies
        location = r.headers["location"]
        assert location.startswith(BASE), location

        # 5c. Follow the redirect with the cookie — SDK handler runs, returns redirect to callback?code=...
        r = c.get(location, cookies=session_cookies)
        print(f"[5c] follow auth redirect -> {r.status_code}")
        assert r.status_code in (302, 303), r.text
        cb = r.headers["location"]
        qs = parse_qs(urlparse(cb).query)
        assert "code" in qs and qs["state"][0] == state, cb
        code = qs["code"][0]
        print(f"    code = {code[:8]}...  state OK")

        # 6. Token exchange
        r = c.post(as_meta["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:9999/callback",
            "client_id": client_id,
            "code_verifier": code_verifier,
        })
        print(f"[6] /token -> {r.status_code}")
        assert r.status_code == 200, r.text
        tok = r.json()
        access = tok["access_token"]
        refresh = tok.get("refresh_token")
        print(f"    access={access[:8]}... refresh={(refresh or '')[:8]}... expires_in={tok.get('expires_in')}")

        # 7. /mcp with access token
        H = {"Authorization": f"Bearer {access}",
             "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        r = c.post(f"{BASE}/mcp",
                   json={"jsonrpc":"2.0","id":1,"method":"initialize",
                         "params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}},
                   headers=H)
        print(f"[7] /mcp initialize (with Bearer) -> {r.status_code}")
        assert r.status_code == 200, r.text
        d = parse_sse(r.text)
        print(f"    server: {d['result']['serverInfo']}")
        H["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        c.post(f"{BASE}/mcp", json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)

        # 8. tools/list
        r = c.post(f"{BASE}/mcp", json={"jsonrpc":"2.0","id":2,"method":"tools/list"}, headers=H)
        names = [t["name"] for t in parse_sse(r.text)["result"]["tools"]]
        print(f"[8] tools/list -> {names}")
        assert "wlm_get_monitor_status" in names

        # 9. Refresh-token round trip
        r = c.post(as_meta["token_endpoint"], data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        })
        print(f"[9] refresh -> {r.status_code}")
        assert r.status_code == 200, r.text
        tok2 = r.json()
        assert tok2["access_token"] != access  # rotated
        print(f"    new access={tok2['access_token'][:8]}...")

        # 10. Old access token still works until it expires, but new one works too
        H["Authorization"] = f"Bearer {tok2['access_token']}"
        # New token gets a new session
        r = c.post(f"{BASE}/mcp",
                   json={"jsonrpc":"2.0","id":1,"method":"initialize",
                         "params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}},
                   headers={k:v for k,v in H.items() if k != "Mcp-Session-Id"})
        print(f"[10] /mcp with refreshed token -> {r.status_code}")
        assert r.status_code == 200

        # 11. Wrong owner key gets rejected — use a fresh client so no stale session cookie.
        with httpx.Client(timeout=15.0, follow_redirects=False) as c2:
            r = c2.post(f"{BASE}/authorize", data={"owner_key": "WRONG", "original_url": auth_url})
        print(f"[11] /authorize with WRONG key (fresh client) -> {r.status_code} (expect 401)")
        assert r.status_code == 401, r.text

    print("\nOAUTH E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
