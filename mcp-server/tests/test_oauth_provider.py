"""Unit tests for SimpleOAuthProvider — all 9 methods, no FastMCP needed."""
import asyncio
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull
from mcp.server.auth.provider import AuthorizationParams

from auth_provider import SimpleOAuthProvider


OWNER = "test-owner-key-32-bytes-base64url"


def make_client(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=None,
        redirect_uris=["http://127.0.0.1:9999/cb"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="mcp",
    )


def make_params() -> AuthorizationParams:
    return AuthorizationParams(
        state="state123",
        scopes=["mcp"],
        code_challenge="dummy-challenge",
        redirect_uri="http://127.0.0.1:9999/cb",
        redirect_uri_provided_explicitly=True,
        resource=None,
    )


@pytest.fixture
def provider(tmp_path):
    return SimpleOAuthProvider(owner_key=OWNER, state_file=tmp_path / "state.json")


def test_constructor_requires_owner_key(tmp_path):
    with pytest.raises(ValueError):
        SimpleOAuthProvider(owner_key="", state_file=tmp_path / "x.json")


async def test_register_and_get_client_roundtrip(provider):
    c = make_client("abc")
    await provider.register_client(c)
    got = await provider.get_client("abc")
    assert got is not None and got.client_id == "abc"
    assert await provider.get_client("nonexistent") is None


async def test_register_persists_clients_across_restarts(tmp_path):
    state = tmp_path / "state.json"
    p1 = SimpleOAuthProvider(owner_key=OWNER, state_file=state)
    await p1.register_client(make_client("persist-me"))
    assert state.exists()

    p2 = SimpleOAuthProvider(owner_key=OWNER, state_file=state)
    assert (await p2.get_client("persist-me")) is not None


async def test_authorize_returns_redirect_with_code_and_state(provider):
    c = make_client()
    await provider.register_client(c)
    url = await provider.authorize(c, make_params())
    assert "code=" in url and "state=state123" in url
    assert url.startswith("http://127.0.0.1:9999/cb")


async def test_exchange_code_issues_access_and_refresh(provider):
    c = make_client()
    await provider.register_client(c)
    redirect = await provider.authorize(c, make_params())
    # pull the code out
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirect).query)["code"][0]

    ac = await provider.load_authorization_code(c, code)
    assert ac is not None
    tok = await provider.exchange_authorization_code(c, ac)
    assert tok.token_type == "Bearer"
    assert tok.access_token and tok.refresh_token
    assert tok.expires_in == 3600

    # code is one-shot — second load is None
    assert (await provider.load_authorization_code(c, code)) is None


async def test_load_access_token_validates_and_expires(provider):
    c = make_client()
    await provider.register_client(c)
    redirect = await provider.authorize(c, make_params())
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirect).query)["code"][0]
    ac = await provider.load_authorization_code(c, code)
    tok = await provider.exchange_authorization_code(c, ac)

    at = await provider.load_access_token(tok.access_token)
    assert at is not None and at.client_id == c.client_id

    # Force-expire it
    provider._access[tok.access_token].expires_at = 0
    assert (await provider.load_access_token(tok.access_token)) is None
    # And cleanup happened
    assert tok.access_token not in provider._access


async def test_refresh_rotates_both_tokens(provider):
    c = make_client()
    await provider.register_client(c)
    redirect = await provider.authorize(c, make_params())
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirect).query)["code"][0]
    ac = await provider.load_authorization_code(c, code)
    first = await provider.exchange_authorization_code(c, ac)

    rt = await provider.load_refresh_token(c, first.refresh_token)
    assert rt is not None
    second = await provider.exchange_refresh_token(c, rt, ["mcp"])

    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    # Old refresh is invalidated
    assert (await provider.load_refresh_token(c, first.refresh_token)) is None


async def test_load_authorization_code_rejects_wrong_client(provider):
    c1 = make_client("c1")
    c2 = make_client("c2")
    await provider.register_client(c1)
    await provider.register_client(c2)
    redirect = await provider.authorize(c1, make_params())
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirect).query)["code"][0]
    # c2 tries to use c1's code -> rejected
    assert (await provider.load_authorization_code(c2, code)) is None


async def test_revoke_token_removes_it(provider):
    c = make_client()
    await provider.register_client(c)
    redirect = await provider.authorize(c, make_params())
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(redirect).query)["code"][0]
    ac = await provider.load_authorization_code(c, code)
    tok = await provider.exchange_authorization_code(c, ac)

    at = await provider.load_access_token(tok.access_token)
    await provider.revoke_token(at)
    assert (await provider.load_access_token(tok.access_token)) is None
