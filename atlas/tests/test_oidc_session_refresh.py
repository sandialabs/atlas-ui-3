"""Refresh-on-use, refused grants, and concurrent OIDC session invalidation."""

import asyncio
import importlib
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


ENTRA_ERROR = {
    "error": "invalid_request",
    "error_description": "AADSTS9002313: Invalid request. refresh_token=secret-echo",
    "error_codes": [9002313],
    "timestamp": "2026-10-08 20:59:38Z",
    "trace_id": "0000aaaa-11bb-cccc-dd22-eeeeee333333",
    "correlation_id": "aaaa0000-bb11-2222-33cc-444444dddddd",
}


async def _token_error(status, body):
    response = httpx.Response(status, json=body)
    with patch("atlas.core.oidc.oidc_client.httpx.AsyncClient") as client:
        client.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
        with pytest.raises(OIDCFlowError) as error:
            await refresh_access_token(
                token_endpoint="https://idp.example.gov/token", refresh_token="test-refresh",
                credentials=ClientCredentials(),
            )
    return error.value


@pytest.mark.asyncio
async def test_token_error_surfaces_entra_support_identifiers():
    """The AADSTS code and trace/correlation IDs name why Entra refused."""
    error = await _token_error(400, ENTRA_ERROR)
    assert error.error_code == "invalid_request"
    assert error.diagnostics == {
        "error_codes": [9002313],
        "trace_id": ENTRA_ERROR["trace_id"],
        "correlation_id": ENTRA_ERROR["correlation_id"],
    }
    message = str(error)
    assert "AADSTS9002313" in message
    assert ENTRA_ERROR["trace_id"] in message
    assert ENTRA_ERROR["correlation_id"] in message
    # The free-text description can echo request data and is never surfaced.
    assert "secret-echo" not in message
    assert "Invalid request" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": "invalid_request", "error_codes": ["9002313"], "trace_id": "x\nforged log line"},
    {"error": "invalid_request", "error_codes": [True, -1, 10**12], "correlation_id": 42},
    {"error": "invalid_request", "trace_id": "a" * 200},
    {"error": "invalid_request", "trace_id": "deadbeef\n", "correlation_id": "aaaa0000-bb11\n"},
])
async def test_token_error_drops_unexpected_diagnostic_values(body):
    error = await _token_error(400, body)
    assert error.diagnostics == {}
    assert str(error) == "Token endpoint returned 400 (invalid_request)"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["bad\r\ncode", "invalid_request\n", 'q"uote', "x" * 65, 7, ""])
async def test_token_error_code_must_be_an_rfc6749_error_value(raw):
    error = await _token_error(400, {"error": raw})
    assert error.error_code == "unknown_error"


@pytest.mark.asyncio
async def test_rejected_refresh_request_logs_identifiers_and_keeps_session(
    session, refresh, revoke, caplog,
):
    """Entra's invalid_request is not a refused grant: keep the session, but
    log what an IdP admin needs and how the user recovers."""
    refresh.side_effect = OIDCFlowError(
        "Token endpoint returned 400 (invalid_request) [AADSTS9002313; trace_id=abc12345]",
        error_code="invalid_request", status_code=400,
    )
    with caplog.at_level("WARNING", logger="atlas.core.oidc.session_refresh"):
        assert await session_refresh.ensure_fresh_access_token(session, SETTINGS) is None
    assert get_session_store().get(session.session_id) is session
    revoke.assert_not_awaited()
    assert "AADSTS9002313" in caplog.text
    assert "signing in again starts a fresh session" in caplog.text


@pytest.mark.asyncio
async def test_new_sign_in_recovers_delegation_while_old_session_is_rejected(
    session, refresh, monkeypatch,
):
    """A fresh login works without a restart even while the idle session's
    refresh keeps being rejected (the reported workaround)."""
    from atlas.core.oidc import mcp_delegation

    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 0)
    refresh.side_effect = OIDCFlowError(
        "Token endpoint returned 400 (invalid_request)", error_code="invalid_request", status_code=400,
    )
    # The package re-exports the instance under the submodule's name, so a
    # dotted-string patch would resolve to the module, not the instance.
    factory = importlib.import_module("atlas.infrastructure.app_factory").app_factory
    monkeypatch.setattr(factory, "get_config_manager", lambda: SimpleNamespace(app_settings=SETTINGS))
    assert await mcp_delegation.resolve_subject_token(session.user_id) is None
    get_session_store().create(
        user_id=session.user_id, access_token="fresh-login", refresh_token="fresh-refresh",
        access_token_expires_at=time.time() + 3600, max_age_seconds=3600,
    )
    assert await mcp_delegation.resolve_subject_token(session.user_id) == "fresh-login"


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
async def test_refused_grant_removes_session_but_spares_other_sessions(session, refresh, revoke):
    """A stale tab's refusal must not wipe a newer session's delegated credentials."""
    other = get_session_store().create(
        user_id=session.user_id, access_token="other-access", refresh_token="other-refresh",
        access_token_expires_at=time.time() + 600,
    )
    refresh.side_effect = refused_grant()
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    assert get_session_store().get(session.session_id) is None
    assert get_session_store().get(other.session_id) is other
    revoke.assert_not_awaited()
    assert await session_refresh.ensure_fresh_access_token(session, SETTINGS) is None
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refusal_does_not_wait_for_a_peer_that_cannot_discover_it(session, refresh, revoke):
    """A session that can never be refused must not keep the cleanup waiting."""
    helpless = get_session_store().create(user_id=session.user_id)
    refresh.side_effect = refused_grant()
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    assert get_session_store().get(helpless.session_id) is helpless
    revoke.assert_awaited_once_with(session.user_id)


@pytest.mark.asyncio
async def test_refused_grant_on_the_last_session_revokes_user_wide(session, refresh, revoke):
    refresh.side_effect = refused_grant()
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    assert get_session_store().get(session.session_id) is None
    revoke.assert_awaited_once_with(session.user_id)


@pytest.mark.asyncio
async def test_last_refused_session_revokes_after_its_peers_ended(session, refresh, revoke):
    other = get_session_store().create(
        user_id=session.user_id, access_token="other-access", refresh_token="other-refresh",
        access_token_expires_at=time.time() - 1,
    )
    refresh.side_effect = refused_grant()
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is None
    revoke.assert_not_awaited()
    assert await session_refresh.get_refreshed_session(other.session_id, SETTINGS) is None
    revoke.assert_awaited_once_with(session.user_id)


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
async def test_cooldown_keeps_serving_a_token_that_has_not_expired(session, refresh, monkeypatch):
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 30)
    session.access_token_expires_at = time.time() + 30  # inside the 60 s margin
    refresh.side_effect = httpx.ConnectError("Unavailable")
    assert await session_refresh.get_refreshed_session(session.session_id, SETTINGS) is session
    assert refresh.await_count == 1
    # Inside the cooldown the still-valid token is served instead of failing closed.
    assert await session_refresh.ensure_fresh_access_token(session, SETTINGS) == "old-access"
    assert refresh.await_count == 1


@pytest.mark.asyncio
async def test_expired_cooldown_entries_are_pruned_on_lookup(session, refresh, monkeypatch):
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 0.05)
    refresh.side_effect = httpx.ConnectError("Unavailable")
    await session_refresh.ensure_fresh_access_token(session, SETTINGS)
    assert session.session_id in session_refresh._refresh_failure_cooldowns
    await asyncio.sleep(0.06)
    assert not session_refresh._refresh_in_cooldown(session.session_id)
    assert session.session_id not in session_refresh._refresh_failure_cooldowns


def test_forget_refresh_state_drops_the_lock_and_cooldown(session, monkeypatch):
    monkeypatch.setattr(session_refresh, "REFRESH_FAILURE_COOLDOWN_SECONDS", 30)
    session_refresh._set_refresh_cooldown(session.session_id)
    session_refresh.forget_refresh_state(session.session_id)
    assert session.session_id not in session_refresh._refresh_failure_cooldowns
    assert session_refresh._refresh_locks.get(session.session_id) is None
    session_refresh.forget_refresh_state(None)  # must tolerate no session id


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


@pytest.mark.asyncio
async def test_frame_guard_sends_session_ended_then_raises_disconnect(session, refresh, revoke):
    from fastapi import WebSocketDisconnect
    from main import _enforce_oidc_frame_session

    sent, closed = [], []

    async def send_json(payload):
        sent.append(payload)

    async def close(**kwargs):
        closed.append(kwargs)

    socket = SimpleNamespace(
        session={SESSION_COOKIE_KEY: session.session_id},
        send_json=send_json,
        close=close,
    )
    await _enforce_oidc_frame_session(socket, SETTINGS, session.user_id)
    assert sent == [] and closed == []

    session.access_token_expires_at = time.time() - 1
    refresh.side_effect = refused_grant()
    with pytest.raises(WebSocketDisconnect) as disconnect:
        await _enforce_oidc_frame_session(socket, SETTINGS, session.user_id)
    assert disconnect.value.code == 4401
    assert sent == [{
        "type": "session_ended",
        "reason": "OIDC session ended. Please sign in again.",
    }]
    # The socket itself is closed here, and the raised WebSocketDisconnect
    # hands the endpoint over to its disconnect cleanup.
    assert closed == [{"code": 4401, "reason": "OIDC session ended. Please sign in again."}]
