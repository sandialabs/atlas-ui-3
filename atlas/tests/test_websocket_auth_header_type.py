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

from atlas.core.auth import resolve_user_from_auth_header

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

def test_agent_portal_ws_rejects_unsigned_header_in_jwt_mode():
    from atlas.routes import agent_portal_routes as ap

    with patch.object(ap.app_factory, "get_config_manager", return_value=_jwt_config()):
        socket = SimpleNamespace(
            headers={"X-User-Email": "attacker@example.com"},
            query_params={},
        )
        assert ap._authenticate_ws(socket) is None


def test_agent_portal_ws_accepts_a_verified_token_in_jwt_mode():
    from atlas.routes import agent_portal_routes as ap

    with patch.object(ap.app_factory, "get_config_manager", return_value=_jwt_config()), \
         patch("atlas.core.auth.get_user_from_aws_alb_jwt", return_value="alice@example.com"):
        socket = SimpleNamespace(
            headers={"X-User-Email": "a.valid.jwt"},
            query_params={},
        )
        assert ap._authenticate_ws(socket) == "alice@example.com"


def test_plain_mode_still_works_on_both_sockets():
    """The default email-string deployment is unaffected by the change."""
    from atlas.routes import agent_portal_routes as ap

    config = _jwt_config()
    config.app_settings.auth_user_header_type = "email-string"
    with patch.object(ap.app_factory, "get_config_manager", return_value=config):
        socket = SimpleNamespace(
            headers={"X-User-Email": "alice@example.com"},
            query_params={},
        )
        assert ap._authenticate_ws(socket) == "alice@example.com"
