"""Origin allowlist tests for the main chat WebSocket at /ws.

A WebSocket upgrade is not preflighted, so the same-origin policy does not
protect /ws. Without an explicit check, a page on any origin can open a socket
that the reverse proxy authenticates from the victim's cookies -- cross-site
WebSocket hijacking. These tests cover the shared primitives in
``atlas.core.websocket_origin`` and the policy wrapper in ``main``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import _websocket_origin_allowed, app
from starlette.websockets import WebSocketDisconnect

from atlas.core.websocket_origin import (
    extract_host,
    origin_is_allowed,
    parse_allowed_hosts,
)
from atlas.modules.config.config_manager import config_manager

# --- parse_allowed_hosts --------------------------------------------------

def test_parse_allowed_hosts_normalizes_and_drops_blanks():
    assert parse_allowed_hosts("  Atlas.Example.COM , , atlas.internal ,") == {
        "atlas.example.com",
        "atlas.internal",
    }


def test_parse_allowed_hosts_handles_empty_values():
    assert parse_allowed_hosts("") == frozenset()
    assert parse_allowed_hosts(None) == frozenset()


def test_parse_allowed_hosts_accepts_full_origins():
    """The setting is named *_ALLOWED_ORIGINS, so operators will paste origins.

    Stored verbatim, "https://atlas.example.com" would be compared against a
    bare hostname, never match, and show up only as continued 1008s.
    """
    assert parse_allowed_hosts("https://atlas.example.com") == {"atlas.example.com"}
    assert parse_allowed_hosts("http://atlas.example.com:8443") == {"atlas.example.com"}
    assert parse_allowed_hosts(
        "https://atlas.example.com, atlas.internal"
    ) == {"atlas.example.com", "atlas.internal"}


def test_full_origin_allowlist_entry_actually_matches():
    allowed = parse_allowed_hosts("https://atlas-alt.example.com")
    assert origin_is_allowed(
        "https://atlas-alt.example.com", allowed, request_host="backend.internal"
    ) is True


# --- extract_host ---------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("atlas.example.com", "atlas.example.com"),
        ("atlas.example.com:8443", "atlas.example.com"),
        ("ATLAS.EXAMPLE.COM", "atlas.example.com"),
        ("https://atlas.example.com:8443", "atlas.example.com"),
        ("[::1]:8000", "::1"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_extract_host(value, expected):
    assert extract_host(value) == expected


# --- origin_is_allowed ----------------------------------------------------

def test_missing_origin_is_not_allowed_by_the_primitive():
    """Policy on a missing header belongs to the caller, not this function."""
    assert origin_is_allowed(None) is False
    assert origin_is_allowed("") is False


def test_loopback_requires_an_explicit_opt_in():
    """Permissiveness must be requested, not inferred from a missing Host.

    Inferring it meant an unparseable Host header produced target=None and
    silently took the same branch as a caller that had opted in.
    """
    assert origin_is_allowed("http://localhost:5173") is False
    assert origin_is_allowed("http://127.0.0.1:8000") is False


def test_loopback_is_allowed_when_the_caller_opts_in():
    """The Agent Portal case: it binds loopback, so any loopback origin is fine."""
    assert origin_is_allowed("http://localhost:5173", trust_loopback=True) is True
    assert origin_is_allowed("http://127.0.0.1:8000", trust_loopback=True) is True
    assert origin_is_allowed("http://[::1]:8000", trust_loopback=True) is True
    assert origin_is_allowed("https://localhost", trust_loopback=True) is True


@pytest.mark.parametrize("bad_host", ["", "   ", "host/x//y", "http://"])
def test_unparseable_host_does_not_become_permissive(bad_host):
    """A malformed Host must not be treated like an opt-in to loopback trust."""
    assert origin_is_allowed("http://localhost:3000", request_host=bad_host) is False


def test_loopback_is_allowed_when_the_target_is_also_loopback():
    assert origin_is_allowed(
        "http://localhost:5173", request_host="localhost:8000"
    ) is True
    assert origin_is_allowed(
        "http://127.0.0.1:5173", request_host="localhost:8000"
    ) is True


def test_loopback_origin_is_rejected_for_a_production_target():
    """A malicious app on the user's own machine must not reach production.

    Otherwise a page at http://localhost:3000 could open the socket at
    atlas.example.com, with the browser supplying the victim's cookies.
    """
    assert origin_is_allowed(
        "http://localhost:3000", request_host="atlas.example.com"
    ) is False
    assert origin_is_allowed(
        "http://127.0.0.1:3000", request_host="atlas.example.com"
    ) is False
    assert origin_is_allowed(
        "http://[::1]:3000", request_host="atlas.example.com"
    ) is False


def test_loopback_origin_still_honours_an_explicit_allowlist():
    """An operator who deliberately lists localhost gets it back."""
    assert origin_is_allowed(
        "http://localhost:3000",
        parse_allowed_hosts("localhost"),
        request_host="atlas.example.com",
    ) is True


def test_same_host_as_request_is_allowed():
    assert origin_is_allowed(
        "https://atlas.example.com", request_host="atlas.example.com"
    ) is True


def test_same_host_ignores_port_differences():
    assert origin_is_allowed(
        "https://atlas.example.com", request_host="atlas.example.com:8443"
    ) is True
    assert origin_is_allowed(
        "https://atlas.example.com:8443", request_host="atlas.example.com"
    ) is True


def test_same_host_comparison_is_case_insensitive():
    assert origin_is_allowed(
        "https://ATLAS.example.com", request_host="atlas.EXAMPLE.com"
    ) is True


def test_different_host_is_rejected():
    assert origin_is_allowed(
        "https://attacker.example.com", request_host="atlas.example.com"
    ) is False


def test_suffix_confusion_is_rejected():
    """A host that merely ends with the target must not match."""
    assert origin_is_allowed(
        "https://atlas.example.com.attacker.com", request_host="atlas.example.com"
    ) is False
    assert origin_is_allowed(
        "https://evilatlas.example.com", request_host="atlas.example.com"
    ) is False


def test_allowlisted_host_is_allowed():
    allowed = parse_allowed_hosts("atlas-alt.example.com")
    assert origin_is_allowed("https://atlas-alt.example.com", allowed) is True


def test_unlisted_host_is_rejected_even_with_a_populated_allowlist():
    allowed = parse_allowed_hosts("atlas-alt.example.com")
    assert origin_is_allowed("https://attacker.example.com", allowed) is False


@pytest.mark.parametrize(
    "origin",
    [
        "file://atlas.example.com",
        "ws://atlas.example.com",
        "javascript:alert(1)",
        "null",
        "not a url",
        "data:text/html,<script>",
    ],
)
def test_non_http_origins_are_rejected(origin):
    assert origin_is_allowed(origin, request_host="atlas.example.com") is False


# --- the /ws policy wrapper ----------------------------------------------

def _settings(**overrides):
    base = {
        "feature_websocket_origin_check_enabled": True,
        "websocket_allowed_origins": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _socket(headers):
    return SimpleNamespace(headers=headers)


def test_absent_origin_is_allowed_for_non_browser_clients():
    """Browsers always send Origin on an upgrade; absence means no browser."""
    assert _websocket_origin_allowed(_socket({}), _settings()) is True


def test_same_origin_browser_request_is_allowed():
    socket = _socket({"origin": "https://atlas.example.com", "host": "atlas.example.com"})
    assert _websocket_origin_allowed(socket, _settings()) is True


def test_cross_origin_browser_request_is_rejected():
    socket = _socket({"origin": "https://attacker.example.com", "host": "atlas.example.com"})
    assert _websocket_origin_allowed(socket, _settings()) is False


def test_local_app_cannot_reach_a_production_socket():
    socket = _socket({"origin": "http://localhost:3000", "host": "atlas.example.com"})
    assert _websocket_origin_allowed(socket, _settings()) is False


def test_local_dev_still_connects():
    socket = _socket({"origin": "http://localhost:5173", "host": "localhost:8000"})
    assert _websocket_origin_allowed(socket, _settings()) is True


def test_allowlist_admits_a_host_the_proxy_rewrote():
    socket = _socket({"origin": "https://atlas.example.com", "host": "backend.internal"})
    settings = _settings(websocket_allowed_origins="atlas.example.com")
    assert _websocket_origin_allowed(socket, settings) is True


def test_feature_flag_disables_the_check():
    socket = _socket({"origin": "https://attacker.example.com", "host": "atlas.example.com"})
    settings = _settings(feature_websocket_origin_check_enabled=False)
    assert _websocket_origin_allowed(socket, settings) is True


def test_settings_are_read_as_direct_attributes():
    """A rename must fail loudly, not silently disable the check.

    Reading through getattr with a default would turn a renamed setting into a
    silently permissive check, which is the worst possible failure mode here.
    """
    socket = _socket({"origin": "https://attacker.example.com", "host": "atlas.example.com"})
    with pytest.raises(AttributeError):
        _websocket_origin_allowed(socket, SimpleNamespace())


def test_both_settings_exist_on_the_real_settings_model():
    """Direct attribute access is only safe if the fields are really there."""
    from atlas.modules.config.settings import AppSettings

    assert "feature_websocket_origin_check_enabled" in AppSettings.model_fields
    assert "websocket_allowed_origins" in AppSettings.model_fields


# --- end to end through the app ------------------------------------------

@pytest.fixture
def mock_app_factory():
    """Mock app factory to avoid initializing the full application."""
    with patch('main.app_factory') as mock_factory:
        mock_config = MagicMock()
        mock_config.app_settings.test_user = config_manager.app_settings.test_user
        mock_config.app_settings.debug_mode = False
        mock_config.app_settings.auth_user_header = 'X-User-Email'
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_config.app_settings.feature_websocket_origin_check_enabled = True
        mock_config.app_settings.websocket_allowed_origins = ''
        mock_factory.get_config_manager.return_value = mock_config

        mock_chat_service = MagicMock()
        mock_chat_service.handle_chat_message = AsyncMock(return_value={})
        mock_chat_service.end_session = AsyncMock()
        mock_chat_service.session_repository.get = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = mock_chat_service

        yield mock_factory


def test_ws_rejects_cross_origin_upgrade(mock_app_factory):
    """The hijacking case: an attacker page with the victim's auth header."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/ws",
            headers={
                "X-User-Email": "victim@example.com",
                "Origin": "https://attacker.example.com",
                "Host": "atlas.example.com",
            },
        ):
            pass

    assert excinfo.value.code == 1008
    # The connection must be refused before a chat session is ever built.
    mock_app_factory.create_chat_service.assert_not_called()


def test_ws_accepts_same_origin_upgrade(mock_app_factory):
    client = TestClient(app)
    with client.websocket_connect(
        "/ws",
        headers={
            "X-User-Email": "alice@example.com",
            "Origin": "http://testserver",
            "Host": "testserver",
        },
    ) as websocket:
        websocket.send_json({"type": "ping"})
        adapter = mock_app_factory.create_chat_service.call_args[0][0]
        assert adapter.user_email == "alice@example.com"
