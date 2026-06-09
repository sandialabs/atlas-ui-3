"""Tests for Wormhole subtoken capture and forwarding to MCP servers.

Covers:
- WormholeTokenStore (per-user, session-scoped, in-memory storage)
- capture_subtoken_from_headers (feature flag + header name handling)
- MCPToolManager._current_wormhole_subtoken / _build_wormhole_headers
- _get_or_create_user_http_client forwarding the X-Token header and rebuilding
  the cached client when the subtoken rotates.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.wormhole_token_store import (
    WormholeTokenStore,
    capture_subtoken_from_headers,
    get_wormhole_store,
)

# --------------------------------------------------------------------------
# WormholeTokenStore
# --------------------------------------------------------------------------

def test_store_set_get_normalizes_email():
    store = WormholeTokenStore()
    store.set_subtoken("Alice@Example.com", "subtok-123456789")
    # Lookups are case-insensitive (normalized email).
    assert store.get_subtoken("alice@example.com") == "subtok-123456789"
    assert store.get_subtoken("ALICE@EXAMPLE.COM") == "subtok-123456789"


def test_store_rotation_overwrites():
    store = WormholeTokenStore()
    store.set_subtoken("a@b.com", "old-token-value")
    store.set_subtoken("a@b.com", "new-token-value")
    assert store.get_subtoken("a@b.com") == "new-token-value"


def test_store_empty_subtoken_clears():
    store = WormholeTokenStore()
    store.set_subtoken("a@b.com", "token-value-here")
    store.set_subtoken("a@b.com", None)
    assert store.get_subtoken("a@b.com") is None


def test_store_clear_and_isolation():
    store = WormholeTokenStore()
    store.set_subtoken("a@b.com", "token-a-value")
    store.set_subtoken("c@d.com", "token-c-value")
    store.clear("a@b.com")
    assert store.get_subtoken("a@b.com") is None
    # Other users are unaffected.
    assert store.get_subtoken("c@d.com") == "token-c-value"


def test_store_missing_user_returns_none():
    store = WormholeTokenStore()
    assert store.get_subtoken("nobody@nowhere.com") is None
    assert store.get_subtoken(None) is None


def test_get_wormhole_store_is_singleton():
    assert get_wormhole_store() is get_wormhole_store()


# --------------------------------------------------------------------------
# capture_subtoken_from_headers
# --------------------------------------------------------------------------

def _patch_settings(**overrides):
    settings = SimpleNamespace(
        feature_wormhole_enabled=True,
        wormhole_subtoken_header="x-subtoken",
        wormhole_forward_header="X-Token",
    )
    for k, v in overrides.items():
        setattr(settings, k, v)
    # capture_subtoken_from_headers does a lazy `from atlas.modules.config import
    # config_manager`; replace that package attribute with a stand-in exposing
    # the settings we want. (app_settings is a property without a setter, so we
    # cannot patch it on the real instance.)
    return patch(
        "atlas.modules.config.config_manager",
        SimpleNamespace(app_settings=settings),
    )


def test_capture_stores_subtoken_when_enabled():
    get_wormhole_store().clear("user@x.com")
    with _patch_settings():
        result = capture_subtoken_from_headers(
            {"x-subtoken": "captured-token-value"}, "user@x.com"
        )
    assert result == "captured-token-value"
    assert get_wormhole_store().get_subtoken("user@x.com") == "captured-token-value"


def test_capture_noop_when_feature_disabled():
    get_wormhole_store().clear("user2@x.com")
    with _patch_settings(feature_wormhole_enabled=False):
        result = capture_subtoken_from_headers(
            {"x-subtoken": "should-not-store"}, "user2@x.com"
        )
    assert result is None
    assert get_wormhole_store().get_subtoken("user2@x.com") is None


def test_capture_noop_when_header_absent():
    get_wormhole_store().clear("user3@x.com")
    with _patch_settings():
        result = capture_subtoken_from_headers({"other-header": "x"}, "user3@x.com")
    assert result is None
    assert get_wormhole_store().get_subtoken("user3@x.com") is None


def test_capture_case_insensitive_header_lookup_plain_dict():
    get_wormhole_store().clear("user4@x.com")
    with _patch_settings():
        # A plain dict (as used in tests) is not case-insensitive; the helper
        # must still find the header regardless of casing.
        result = capture_subtoken_from_headers(
            {"X-SubToken": "mixed-case-value"}, "user4@x.com"
        )
    assert result == "mixed-case-value"


def test_capture_custom_header_name():
    get_wormhole_store().clear("user5@x.com")
    with _patch_settings(wormhole_subtoken_header="x-custom-sub"):
        result = capture_subtoken_from_headers(
            {"x-custom-sub": "custom-header-value"}, "user5@x.com"
        )
    assert result == "custom-header-value"


def test_capture_clears_stale_subtoken_when_header_absent():
    # A previously stored subtoken must be cleared (write-through) when a later
    # request for the same user arrives without the header, so the stale value
    # is never forwarded on a subsequent MCP call.
    get_wormhole_store().set_subtoken("user6@x.com", "old-stored-value")
    with _patch_settings():
        result = capture_subtoken_from_headers({"unrelated": "x"}, "user6@x.com")
    assert result is None
    assert get_wormhole_store().get_subtoken("user6@x.com") is None


# --------------------------------------------------------------------------
# MCPToolManager helpers + client creation
# --------------------------------------------------------------------------

@pytest.fixture
def manager():
    """MCPToolManager instance with cache state, bypassing __init__/config load."""
    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.app_settings = SimpleNamespace(
            feature_wormhole_enabled=True,
            wormhole_forward_header="X-Token",
        )
        mgr = MCPToolManager.__new__(MCPToolManager)
        mgr.config_path = "/tmp/test"
        mgr.servers_config = {}
        mgr.clients = {}
        mgr.available_tools = {}
        mgr.available_prompts = {}
        mgr._user_clients = {}
        mgr._user_client_last_used = {}
        mgr._wormhole_client_subtokens = {}
        mgr._user_client_cache_max_entries = 1000
        mgr._user_client_cache_idle_ttl_seconds = 3600
        mgr._user_client_cache_sweep_interval_seconds = 300
        mgr._user_client_cache_in_use_window_seconds = 60
        mgr._user_client_close_timeout_seconds = 5.0
        mgr._user_client_sweeper_task = None
        mgr._user_client_close_tasks = set()
        mgr._user_clients_lock = asyncio.Lock()
        mgr._elicitation_routing = {}
        mgr._sampling_routing = {}
        yield mgr


def test_is_wormhole_server(manager):
    manager.servers_config["wh"] = {"url": "http://x/mcp", "transport": "http", "wormhole": True}
    manager.servers_config["plain"] = {"url": "http://x/mcp", "transport": "http"}
    assert manager._is_wormhole_server("wh") is True
    assert manager._is_wormhole_server("plain") is False
    assert manager._is_wormhole_server("missing") is False


def test_current_subtoken_returns_value_for_wormhole_server(manager):
    manager.servers_config["wh"] = {"url": "http://x/mcp", "wormhole": True}
    get_wormhole_store().set_subtoken("u@x.com", "live-subtoken-value")
    assert manager._current_wormhole_subtoken("wh", "u@x.com") == "live-subtoken-value"


def test_current_subtoken_none_for_non_wormhole_server(manager):
    manager.servers_config["plain"] = {"url": "http://x/mcp"}
    get_wormhole_store().set_subtoken("u@x.com", "live-subtoken-value")
    assert manager._current_wormhole_subtoken("plain", "u@x.com") is None


def test_current_subtoken_none_when_feature_disabled(manager):
    manager.servers_config["wh"] = {"url": "http://x/mcp", "wormhole": True}
    get_wormhole_store().set_subtoken("u@x.com", "live-subtoken-value")
    manager_cm = SimpleNamespace(feature_wormhole_enabled=False, wormhole_forward_header="X-Token")
    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.app_settings = manager_cm
        assert manager._current_wormhole_subtoken("wh", "u@x.com") is None


def test_build_wormhole_headers(manager):
    manager.servers_config["wh"] = {"url": "http://x/mcp", "wormhole": True}
    get_wormhole_store().set_subtoken("u@x.com", "header-subtoken-value")
    headers = manager._build_wormhole_headers("wh", "u@x.com")
    assert headers == {"X-Token": "header-subtoken-value"}


def test_build_wormhole_headers_empty_without_subtoken(manager):
    manager.servers_config["wh"] = {"url": "http://x/mcp", "wormhole": True}
    get_wormhole_store().clear("nouser@x.com")
    assert manager._build_wormhole_headers("wh", "nouser@x.com") == {}


@pytest.mark.asyncio
async def test_http_client_forwards_subtoken_header(manager):
    manager.servers_config["wh"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
        "wormhole": True,
    }
    get_wormhole_store().set_subtoken("alice@test.com", "alice-subtoken-value")

    with patch("atlas.modules.mcp_tools.client.Client") as MockClient, patch(
        "atlas.modules.mcp_tools.client.StreamableHttpTransport"
    ) as MockTransport:
        MockClient.return_value = MagicMock()
        await manager._get_or_create_user_http_client("wh", "alice@test.com", "conv-1")

        MockTransport.assert_called_once()
        _, kwargs = MockTransport.call_args
        assert kwargs["headers"] == {"X-Token": "alice-subtoken-value"}


@pytest.mark.asyncio
async def test_http_client_no_transport_when_not_wormhole(manager):
    manager.servers_config["plain"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient, patch(
        "atlas.modules.mcp_tools.client.StreamableHttpTransport"
    ) as MockTransport:
        MockClient.return_value = MagicMock()
        await manager._get_or_create_user_http_client("plain", "alice@test.com", "conv-1")
        MockTransport.assert_not_called()


@pytest.mark.asyncio
async def test_http_client_rebuilt_on_subtoken_rotation(manager):
    manager.servers_config["wh"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
        "wormhole": True,
    }
    get_wormhole_store().set_subtoken("alice@test.com", "first-subtoken-value")

    with patch("atlas.modules.mcp_tools.client.Client") as MockClient, patch(
        "atlas.modules.mcp_tools.client.StreamableHttpTransport"
    ), patch.object(manager, "_close_user_client_entries", AsyncMock(return_value=None)):
        MockClient.side_effect = [MagicMock(name="c1"), MagicMock(name="c2")]
        c1 = await manager._get_or_create_user_http_client("wh", "alice@test.com", "conv-1")
        # Same subtoken -> cached client reused.
        c1b = await manager._get_or_create_user_http_client("wh", "alice@test.com", "conv-1")
        assert c1 is c1b
        assert MockClient.call_count == 1

        # Rotate the subtoken -> stale client rebuilt.
        get_wormhole_store().set_subtoken("alice@test.com", "rotated-subtoken-value")
        c2 = await manager._get_or_create_user_http_client("wh", "alice@test.com", "conv-1")
        assert c2 is not c1
        assert MockClient.call_count == 2


@pytest.mark.asyncio
async def test_user_auth_client_rebuilt_on_subtoken_rotation(manager):
    """A server with both auth_type and wormhole rebuilds its cached client when
    the subtoken rotates, even though the primary auth token stays valid."""
    manager.servers_config["wh_auth"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
        "auth_type": "api_key",
        "auth_header": "X-API-Key",
        "wormhole": True,
    }
    get_wormhole_store().set_subtoken("alice@test.com", "sub-token-1")

    stored = SimpleNamespace(token_value="api-key-value")
    fake_storage = MagicMock()
    fake_storage.get_valid_token.return_value = stored

    with patch(
        "atlas.modules.mcp_tools.token_storage.get_token_storage",
        return_value=fake_storage,
    ), patch("atlas.modules.mcp_tools.client.Client") as MockClient, patch(
        "atlas.modules.mcp_tools.client.StreamableHttpTransport"
    ) as MockTransport, patch.object(
        manager, "_close_user_client_entries", AsyncMock(return_value=None)
    ), patch.object(
        manager, "_close_user_client_entry", AsyncMock(return_value=None)
    ):
        MockClient.side_effect = [MagicMock(name="c1"), MagicMock(name="c2")]

        c1 = await manager._get_user_client("wh_auth", "alice@test.com", "conv-1")
        # Same subtoken + valid token -> cached client reused.
        c1b = await manager._get_user_client("wh_auth", "alice@test.com", "conv-1")
        assert c1 is c1b
        assert MockClient.call_count == 1
        _, kwargs = MockTransport.call_args
        assert kwargs["headers"]["X-API-Key"] == "api-key-value"
        assert kwargs["headers"]["X-Token"] == "sub-token-1"

        # Rotate subtoken while the API key stays valid -> client rebuilt.
        get_wormhole_store().set_subtoken("alice@test.com", "sub-token-2")
        c2 = await manager._get_user_client("wh_auth", "alice@test.com", "conv-1")
        assert c2 is not c1
        assert MockClient.call_count == 2
        _, kwargs2 = MockTransport.call_args
        assert kwargs2["headers"]["X-Token"] == "sub-token-2"
