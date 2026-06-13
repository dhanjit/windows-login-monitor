"""Claude-accurate OAuth probe: drives the full browser flow (discovery, DCR,
owner-key form, authorize, PKCE token exchange) and confirms the token works
on /mcp. Use it to validate a deployment end-to-end.

Usage:  python diag_oauth.py <base_url> <owner_key>
   e.g. python diag_oauth.py http://127.0.0.1:8765 my-owner-key
The owner key may also come from $WLM_MCP_OWNER_KEY. Never hardcode it here."""
import base64, hashlib, json, os, re, secrets, sys
from urllib.parse import urlparse, parse_qs, urlencode
import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
OWNER_KEY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WLM_MCP_OWNER_KEY", "")
if not OWNER_KEY:
    sys.exit("Owner key required: pass as 2nd arg or set $WLM_MCP_OWNER_KEY")
RESOURCE = f"{BASE}/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"  # what Claude actually uses

def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

with httpx.Client(timeout=20, follow_redirects=False) as c:
    # DCR
    r = c.post(f"{BASE}/register", json={
        "client_name": "diag", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
        "scope": "mcp",
    })
    print(f"[register] {r.status_code}")
    print(f"  response: {json.dumps(r.json(), indent=2)}")
    client = r.json()
    cid = client["client_id"]

    # authorize (with resource)
    verifier = b64(secrets.token_bytes(32))
    challenge = b64(hashlib.sha256(verifier.encode()).digest())
    state = b64(secrets.token_bytes(8))
    params = {
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "scope": "mcp", "resource": RESOURCE,
    }
    auth_url = f"{BASE}/authorize?{urlencode(params)}"
    # Browser-accurate: GET the form, extract its action, POST ONLY owner_key.
    r = c.get(auth_url)
    m = re.search(r'<form method="POST" action="([^"]+)"', r.text)
    if not m:
        print(f"[authorize form] FAIL — no form action found. body head:\n{r.text[:300]}")
        sys.exit(1)
    action = m.group(1).replace("&amp;", "&")
    if not action.startswith("http"):
        action = BASE + action
    print(f"[authorize form] action carries {len(parse_qs(urlparse(action).query))} query params")
    r = c.post(action, data={"owner_key": OWNER_KEY})
    print(f"[authorize POST] {r.status_code}")
    loc = r.headers.get("location", "")
    r = c.get(loc if loc.startswith("http") else f"{BASE}{loc}", cookies=r.cookies)
    cb = r.headers.get("location", "")
    code = parse_qs(urlparse(cb).query).get("code", [""])[0]
    print(f"[authorize] code={code[:10]}...")

    # token (with resource)
    r = c.post(f"{BASE}/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": cid,
        "code_verifier": verifier, "resource": RESOURCE,
    })
    print(f"[token] {r.status_code}")
    print(f"  raw body: {r.text}")
    tok = r.json()
    access = tok["access_token"]

    # use the token on /mcp
    r = c.post(f"{BASE}/mcp",
        json={"jsonrpc":"2.0","id":1,"method":"initialize",
              "params":{"protocolVersion":"2025-06-18","capabilities":{},
                        "clientInfo":{"name":"diag","version":"0"}}},
        headers={"Authorization": f"Bearer {access}",
                 "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    print(f"[mcp initialize w/ token] {r.status_code}")
    if r.status_code != 200:
        print(f"  body: {r.text[:400]}")
        print(f"  www-authenticate: {r.headers.get('www-authenticate')}")
    else:
        print("  -> token ACCEPTED, MCP works")
