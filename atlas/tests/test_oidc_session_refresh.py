"""Refresh-on-use, refused grants, and concurrent OIDC session invalidation."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from atlas.core.middleware import AuthMiddleware
from atlas.core.oidc import session_refresh
from atlas.core.oidc.client_authentication import ClientCredentials
from atlas.core.oidc.oidc_client import OIDCFlowError, refresh_access_token
from atlas.core.oidc.session import SESSION_COOKIE_KEY, get_session_store
from atlas.core.session_middleware import SessionMiddleware

SETTINGS = SimpleNamespace(oidc_issuer="https://idp.example.gov", feature_oidc_auth_enabled=True)


def refused_grant():
    return OIDCFlowError("Refresh refused", error_code="invalid_grant", status_code=400)


@pytest.fixture
def session():
    store = get_session_store()
    store.clear()
    session_refresh._refresh_locks.clear()
    session_refresh._refresh_failure_cooldowns.clear()
    value = store.create(
        user_id="user@example.gov", access_token="old-access", refresh_token="old-refresh",
        access_token_expires_at=time.time() - 1, max_age_seconds=3600,
    )
    yield value
    store.clear()
    session_refresh._refresh_locks.clear()
    session_refresh._refresh_failure_cooldowns.clear()


@pytest.fixture
def refresh():
    with patch.object(session_refresh, "get_provider_metadata", AsyncMock()), \
            patch.object(session_refresh, "build_client_credentials_from_settings"), \
            patch.object(session_refresh, "refresh_access_token", AsyncMock(return_value={
                "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 300,
            })) as refresh:
        yield refresh


@pytest.fixture
def revoke():
    with patch("atlas.core.oidc.mcp_delegation.revoke_delegated_credentials", AsyncMock()) as revoke:
        yield revoke


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,code", [
    (400, {"error": "invalid_grant"}, "invalid_grant"),
    (503, {"error": "invalid_grant"}, "invalid_grant"),
    (400, {"error": "invalid_client"}, "invalid_client"),
    (502, ["not", "an", "object"], "unknown_error"),
])
async def test_token_endpoint_preserves_structured_error(status, body, code):
    response = httpx.Response(status, json=body)
    with patch("atlas.core.oidc.oidc_client.httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(OIDCFlowError) as error:
            await refresh_access_token(
                token_endpoint="https://idp.example.gov/token", refresh_token="test-refresh",
                credentials=ClientCredentials(),
            )
    assert error.value.error_code == code
    assert error.value.status_code == status


@pytest.mark.asyncio
async def test_refresh_rotates_tokens_without_extending_session(session, refresh):
    expires_at = session.expires_at
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert session.access_token == "new-access"
    assert session.refresh_token == "new-refresh"
    assert session.access_token_expires_at > time.time()
    assert session.expires_at == expires_at
    await session_refresh.get_refreshed_session(session.session_id, SETTINGS)
    refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("token_expiry", [None, 3600])
async def test_no_refresh_before_expiry_or_without_expiry(session, refresh, token_expiry):
    session.access_token_expires_at = time.time() + token_expiry if token_expiry else None
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_refresh_token_keeps_login_but_not_expired_credential(session, refresh):
    session.refresh_token = None
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert await session_refresh.ensure_fresh_access_token(session, SETTINGS) is None
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refused_grant_removes_only_affected_session_and_revokes(session, refresh, revoke):
    other = get_session_store().create(user_id=session.user_id)
    refresh.side_effect = refused_grant()
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    assert get_session_store().get(session.session_id) is None
    assert get_session_store().get(other.session_id) is other
    revoke.assert_awaited_once_with(session.user_id)
    assert await session_refresh.ensure_fresh_access_token(session, SETTINGS) is None
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_stays_removed_if_credential_cleanup_fails(session, refresh, revoke):
    refresh.side_effect = refused_grant()
    revoke.side_effect = RuntimeError("Credential store unavailable")
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    assert get_session_store().get(session.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("Unavailable"),
    OIDCFlowError("Unavailable", error_code="invalid_grant", status_code=503),
    OIDCFlowError("Misconfigured", error_code="invalid_client", status_code=401),
    OIDCFlowError("Token endpoint response is not valid JSON"),
    OIDCFlowError("invalid_grant"),  # Text alone is not a structured refusal.
])
async def test_transient_or_other_error_keeps_session_and_retries(
    session, refresh, revoke, failure, monkeypatch,
):
    # The cooldown would otherwise defer the immediate retry in this test.
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 0)
    refresh.side_effect = failure
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    revoke.assert_not_awaited()
    refresh.side_effect = None
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert session.access_token == "new-access"
    assert refresh.await_count == 2


@pytest.mark.asyncio
async def test_transient_failure_sets_a_short_cooldown(session, refresh, monkeypatch):
    """An outage must not make every queued request retry the IdP in turn."""
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 0.05)
    refresh.side_effect = httpx.ConnectError("Unavailable")
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    # Inside the cooldown the IdP is not contacted again and the session stays.
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert refresh.await_count == 1
    # Once the cooldown expires the IdP is retried and success clears it.
    await asyncio.sleep(0.06)
    refresh.side_effect = None
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert session.access_token == "new-access"
    assert refresh.await_count == 2
    assert session.session_id not in session_refresh._refresh_failure_cooldowns


@pytest.mark.asyncio
@pytest.mark.parametrize("refused", [False, True])
async def test_concurrent_refresh_is_serialized_after_success_or_refusal(session, refresh, revoke, refused):
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed_refresh(**kwargs):
        started.set()
        await finish.wait()
        if refused:
            raise refused_grant()
        return {"access_token": "new-access", "expires_in": 300}

    refresh.side_effect = delayed_refresh
    first = asyncio.create_task(session_refresh.ensure_fresh_access_token(session, SETTINGS))
    await started.wait()
    second = asyncio.create_task(session_refresh.ensure_fresh_access_token(session, SETTINGS))
    await asyncio.sleep(0)
    finish.set()
    results = await asyncio.gather(first, second)
    assert results == ([None, None] if refused else ["new-access", "new-access"])
    refresh.assert_awaited_once()
    assert revoke.await_count == int(refused)
    if not refused:
        assert session.refresh_token == "old-refresh"


@pytest.mark.asyncio
async def test_logout_during_refresh_does_not_authenticate_stale_reference(session, refresh):
    async def logout(**kwargs):
        get_session_store().remove(session.session_id)
        return {"access_token": "new-access", "expires_in": 300}

    refresh.side_effect = logout
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None


@pytest.mark.asyncio
async def test_refused_refresh_fences_off_inflight_and_queued_delegation(session, refresh):
    from atlas.core.oidc.delegation import DelegatedToken, DelegationManager
    from atlas.core.oidc.mcp_delegation import mint_delegated_token_for_server

    started, finish = asyncio.Event(), asyncio.Event()

    async def exchange(request):
        started.set()
        await finish.wait()
        return DelegatedToken(access_token="downstream", expires_at=time.time() + 300)

    provider = SimpleNamespace(name="test", exchange=AsyncMock(side_effect=exchange))
    manager = DelegationManager(provider)
    session.access_token_expires_at = time.time() + 300
    server = {"auth_type": "delegated", "url": "https://tools.example.gov/mcp"}
    with patch("atlas.core.oidc.mcp_delegation.get_delegation_manager_async",
               AsyncMock(return_value=manager)), \
            patch("atlas.core.oidc.delegation.get_delegation_manager", return_value=manager):
        first = asyncio.create_task(mint_delegated_token_for_server(session.user_id, "tools", server))
        await started.wait()
        second = asyncio.create_task(mint_delegated_token_for_server(session.user_id, "tools", server))
        await asyncio.sleep(0)
        session.access_token_expires_at = time.time() - 1
        refresh.side_effect = refused_grant()
        assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
        finish.set()
        assert await asyncio.gather(first, second) == [None, None]
    provider.exchange.assert_awaited_once()
    assert manager.invalidate_user(session.user_id) == 0
    assert not manager._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", ["other-user", "clear"])
async def test_pending_delegation_revocation_scope(invalidate):
    from atlas.core.oidc.delegation import (
        DelegatedToken,
        DelegationError,
        DelegationManager,
        DelegationRequest,
    )

    started, finish = asyncio.Event(), asyncio.Event()
    token = DelegatedToken(access_token="downstream", expires_at=time.time() + 300)

    async def exchange(request):
        started.set()
        await finish.wait()
        return token

    manager = DelegationManager(SimpleNamespace(name="test", exchange=exchange))
    request = DelegationRequest(user_id="user@example.gov", subject_token="test-access")
    task = asyncio.create_task(manager.get_token(request))
    await started.wait()
    if invalidate == "clear":
        manager.clear()
    else:
        manager.invalidate_user("other@example.gov")
    finish.set()
    if invalidate == "clear":
        with pytest.raises(DelegationError, match="revoked"):
            await task
    else:
        assert await task is token
    assert not manager._pending


@pytest.fixture
def client(session):
    app = FastAPI()

    @app.get("/api/whoami")
    @app.get("/workspace")
    async def whoami(request: Request):
        return {"user": request.state.user_email}

    @app.get("/auth/oidc/establish")
    async def establish(request: Request):
        request.session[SESSION_COOKIE_KEY] = session.session_id
        return {}

    app.add_middleware(AuthMiddleware, oidc_enabled=True)
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    with patch("atlas.infrastructure.app_factory.app_factory.get_config_manager",
               return_value=SimpleNamespace(app_settings=SETTINGS)):
        with TestClient(app) as client:
            client.get("/auth/oidc/establish")
            yield client


def test_http_use_refreshes_without_delegation(session, refresh, client):
    assert client.get("/api/whoami").json() == {"user": session.user_id}
    assert session.access_token == "new-access"
    assert client.get("/api/whoami").status_code == 200
    refresh.assert_awaited_once()


@pytest.mark.parametrize("path,status", [("/api/whoami", 401), ("/workspace?tab=1", 302)])
def test_http_refusal_rejects_current_and_next_request(session, refresh, revoke, client, path, status):
    refresh.side_effect = refused_grant()
    response = client.get(path, follow_redirects=False)
    assert response.status_code == status
    if status == 302:
        assert response.headers["location"] == "/auth/oidc/login?next=%2Fworkspace%3Ftab%3D1"
    assert "session" not in client.cookies
    assert client.get(path, follow_redirects=False).status_code == status
    revoke.assert_awaited_once_with(session.user_id)
    refresh.assert_awaited_once()


def test_http_transient_failure_authenticates_and_retries(session, refresh, revoke, client, monkeypatch):
    # The cooldown would otherwise defer the immediate retry in this test.
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 0)
    refresh.side_effect = httpx.ConnectError("Unavailable")
    assert client.get("/api/whoami").status_code == 200
    refresh.side_effect = None
    assert client.get("/api/whoami").status_code == 200
    assert refresh.await_count == 2
    revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_resolver_refreshes_and_rejects_refusal(session, refresh, revoke):
    from main import _resolve_oidc_websocket_user

    socket = SimpleNamespace(session={SESSION_COOKIE_KEY: session.session_id})
    assert await _resolve_oidc_websocket_user(socket, SETTINGS) == session.user_id
    session.access_token_expires_at = time.time() - 1
    refresh.side_effect = refused_grant()
    assert await _resolve_oidc_websocket_user(socket, SETTINGS) is None
    revoke.assert_awaited_once_with(session.user_id)
