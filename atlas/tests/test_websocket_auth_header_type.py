"""The configured auth header type must be honoured on WebSockets too.

HTTP middleware branched on AUTH_USER_HEADER_TYPE and cryptographically
verified ``aws-alb-jwt``, but both WebSocket endpoints called
``get_user_from_header`` unconditionally -- which only strips whitespace. In
an ALB-JWT deployment that made any non-empty header value a valid identity on
the socket while the same value was rejected on HTTP.

These tests pin the shared resolver and both WebSocket call sites.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.core.auth import (
    resolve_user_from_auth_header,
    resolve_user_from_auth_header_async,
)

ALB_ARN = "arn:aws:elasticloadbalancing:us-east-1:1234:loadbalancer/app/x/y"


# --- the shared resolver --------------------------------------------------

def test_plain_header_type_trusts_the_value():
    assert resolve_user_from_auth_header(
        " alice@example.com ", header_type="email-string"
    ) == "alice@example.com"


def test_missing_header_resolves_to_none():
    assert resolve_user_from_auth_header(None, header_type="email-string") is None
    assert resolve_user_from_auth_header("", header_type="aws-alb-jwt") is None


def test_jwt_mode_rejects_an_unsigned_value():
    """The core of the bypass: a bare email must not authenticate in JWT mode."""
    assert resolve_user_from_auth_header(
        "attacker@example.com",
        header_type="aws-alb-jwt",
        expected_alb_arn=ALB_ARN,
    ) is None


@pytest.mark.parametrize(
    "value",
    [
        "attacker@example.com",
        "not.a.jwt",
        "eyJhbGciOiJFUzI1NiJ9.eyJlbWFpbCI6ImF0dGFja2VyQGV4YW1wbGUuY29tIn0.",
        "Bearer eyJhbGciOiJub25lIn0.eyJlbWFpbCI6ImFAYi5jIn0.",
    ],
)
def test_jwt_mode_rejects_forged_and_malformed_tokens(value):
    assert resolve_user_from_auth_header(
        value, header_type="aws-alb-jwt", expected_alb_arn=ALB_ARN
    ) is None


def test_jwt_mode_delegates_to_the_verifier():
    """A token only authenticates via the real ALB verification path."""
    with patch("atlas.core.auth.get_user_from_aws_alb_jwt", return_value="ok@example.com") as verifier:
        result = resolve_user_from_auth_header(
            "some.jwt.value",
            header_type="aws-alb-jwt",
            expected_alb_arn=ALB_ARN,
            aws_region="us-west-2",
        )
    assert result == "ok@example.com"
    verifier.assert_called_once_with("some.jwt.value", ALB_ARN, "us-west-2")


# --- the async resolver ---------------------------------------------------

@pytest.mark.asyncio
async def test_async_resolver_matches_the_sync_one_for_plain_headers():
    assert await resolve_user_from_auth_header_async(
        " alice@example.com ", header_type="email-string"
    ) == "alice@example.com"
    assert await resolve_user_from_auth_header_async(
        None, header_type="email-string"
    ) is None


@pytest.mark.asyncio
async def test_async_resolver_rejects_unsigned_values_in_jwt_mode():
    assert await resolve_user_from_auth_header_async(
        "attacker@example.com", header_type="aws-alb-jwt", expected_alb_arn=ALB_ARN
    ) is None


@pytest.mark.asyncio
async def test_jwt_verification_runs_off_the_event_loop():
    """The key fetch is a blocking 5s httpx call; it must not block the loop.

    Asserting the verifier runs on a different thread is the observable
    consequence of using asyncio.to_thread, and is what stops one cache miss
    stalling every other in-flight connection.
    """
    import threading

    loop_thread = threading.get_ident()
    seen = {}

    def _verifier(token, arn, region):
        seen["thread"] = threading.get_ident()
        return "alice@example.com"

    with patch("atlas.core.auth.get_user_from_aws_alb_jwt", _verifier):
        result = await resolve_user_from_auth_header_async(
            "a.jwt", header_type="aws-alb-jwt", expected_alb_arn=ALB_ARN
        )

    assert result == "alice@example.com"
    assert seen["thread"] != loop_thread


@pytest.mark.asyncio
async def test_plain_headers_do_not_pay_for_a_thread_hop():
    """No I/O on this path, so pushing it through a thread would cost more."""
    import threading

    loop_thread = threading.get_ident()
    with patch("atlas.core.auth.get_user_from_aws_alb_jwt") as verifier:
        await resolve_user_from_auth_header_async(
            "alice@example.com", header_type="email-string"
        )
    verifier.assert_not_called()
    assert threading.get_ident() == loop_thread


# --- ALB key cache --------------------------------------------------------

def test_failed_key_fetches_are_negatively_cached():
    """Otherwise a fresh `kid` per request forces one outbound call per upgrade."""
    import httpx

    from atlas.core import auth as auth_module

    auth_module._alb_key_cache.clear()
    calls = {"n": 0}

    def _fail(*args, **kwargs):
        calls["n"] += 1
        raise httpx.RequestError("no network")

    with patch("atlas.core.auth.httpx.get", _fail):
        for _ in range(5):
            assert auth_module._get_alb_public_key("abc123", "us-east-1") is None

    assert calls["n"] == 1, "failed fetch was retried instead of negatively cached"
    auth_module._alb_key_cache.clear()


def test_alb_key_cache_is_bounded():
    """`kid` comes from an unverified header, so the cache is attacker-influenced."""
    import httpx

    from atlas.core import auth as auth_module

    auth_module._alb_key_cache.clear()
    with patch("atlas.core.auth.httpx.get", side_effect=httpx.RequestError("x")):
        for i in range(auth_module._ALB_CACHE_MAX_ENTRIES + 50):
            auth_module._get_alb_public_key(f"kid{i}", "us-east-1")

    assert len(auth_module._alb_key_cache) <= auth_module._ALB_CACHE_MAX_ENTRIES
    auth_module._alb_key_cache.clear()


# --- the chat socket ------------------------------------------------------

def _jwt_config():
    config = MagicMock()
    config.app_settings.debug_mode = False
    config.app_settings.auth_user_header = "X-User-Email"
    config.app_settings.auth_user_header_type = "aws-alb-jwt"
    config.app_settings.auth_aws_expected_alb_arn = ALB_ARN
    config.app_settings.auth_aws_region = "us-east-1"
    config.app_settings.feature_proxy_secret_enabled = False
    config.app_settings.feature_websocket_origin_check_enabled = True
    config.app_settings.websocket_allowed_origins = ""
    config.app_settings.test_user = "test@test.com"
    return config


@pytest.fixture
def jwt_mode_factory():
    with patch("main.app_factory") as factory:
        factory.get_config_manager.return_value = _jwt_config()
        service = MagicMock()
        service.handle_chat_message = AsyncMock(return_value={})
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        factory.create_chat_service.return_value = service
        yield factory


def test_chat_socket_rejects_unsigned_header_in_jwt_mode(jwt_mode_factory):
    """Previously this opened an authenticated socket as the named user."""
    from main import app

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/ws", headers={"X-User-Email": "attacker@example.com"}
        ):
            pass

    assert excinfo.value.code == 1008
    jwt_mode_factory.create_chat_service.assert_not_called()


def test_chat_socket_accepts_a_verified_token_in_jwt_mode(jwt_mode_factory):
    from main import app

    with patch("atlas.core.auth.get_user_from_aws_alb_jwt", return_value="alice@example.com"):
        client = TestClient(app)
        with client.websocket_connect(
            "/ws", headers={"X-User-Email": "a.valid.jwt"}
        ) as websocket:
            websocket.send_json({"type": "ping"})
            adapter = jwt_mode_factory.create_chat_service.call_args[0][0]
            assert adapter.user_email == "alice@example.com"


# --- the agent portal socket ---------------------------------------------

@pytest.mark.asyncio
async def test_agent_portal_ws_rejects_unsigned_header_in_jwt_mode():
    from atlas.routes import agent_portal_routes as ap

    with patch.object(ap.app_factory, "get_config_manager", return_value=_jwt_config()):
        socket = SimpleNamespace(
            headers={"X-User-Email": "attacker@example.com"},
            query_params={},
        )
        assert await ap._authenticate_ws(socket) is None


@pytest.mark.asyncio
async def test_agent_portal_ws_accepts_a_verified_token_in_jwt_mode():
    from atlas.routes import agent_portal_routes as ap

    with patch.object(ap.app_factory, "get_config_manager", return_value=_jwt_config()), \
         patch("atlas.core.auth.get_user_from_aws_alb_jwt", return_value="alice@example.com"):
        socket = SimpleNamespace(
            headers={"X-User-Email": "a.valid.jwt"},
            query_params={},
        )
        assert await ap._authenticate_ws(socket) == "alice@example.com"


@pytest.mark.asyncio
async def test_plain_mode_still_works_on_both_sockets():
    """The default email-string deployment is unaffected by the change."""
    from atlas.routes import agent_portal_routes as ap

    config = _jwt_config()
    config.app_settings.auth_user_header_type = "email-string"
    with patch.object(ap.app_factory, "get_config_manager", return_value=config):
        socket = SimpleNamespace(
            headers={"X-User-Email": "alice@example.com"},
            query_params={},
        )
        assert await ap._authenticate_ws(socket) == "alice@example.com"
