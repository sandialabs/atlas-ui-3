"""Tests for per-user HTTP client isolation in MCPToolManager."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.fixture
def manager():
    """Create an MCPToolManager with test config."""
    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.app_settings.app_config_dir = "/tmp/nonexistent"
        mock_cm.app_settings.mcp_call_timeout = 30
        mock_cm.app_settings.log_level = "INFO"
        mock_cm.mcp_config.servers = {}
        mgr = MCPToolManager.__new__(MCPToolManager)
        mgr.config_path = "/tmp/test"
        mgr.servers_config = {}
        mgr.clients = {}
        mgr.available_tools = {}
        mgr.available_prompts = {}
        mgr._failed_servers = {}
        mgr._reconnect_task = None
        mgr._reconnect_running = False
        mgr._default_log_callback = None
        mgr._min_log_level = 20
        mgr._user_clients = {}
        mgr._user_client_last_used = {}
        mgr._user_client_cache_max_entries = 1000
        mgr._user_client_cache_idle_ttl_seconds = 3600
        mgr._user_client_cache_sweep_interval_seconds = 300
        mgr._user_client_sweeper_task = None
        mgr._user_clients_lock = asyncio.Lock()
        mgr._elicitation_routing = {}
        mgr._sampling_routing = {}
        return mgr


# --- _is_http_server tests ---

def test_is_http_server_true_for_http(manager):
    manager.servers_config["my_server"] = {"url": "http://localhost:8010/mcp", "transport": "http"}
    assert manager._is_http_server("my_server") is True


def test_is_http_server_false_for_stdio(manager):
    manager.servers_config["my_server"] = {"command": ["python", "main.py"]}
    assert manager._is_http_server("my_server") is False


def test_is_http_server_false_for_missing(manager):
    assert manager._is_http_server("nonexistent") is False


# --- _get_or_create_user_http_client tests ---

@pytest.mark.asyncio
async def test_get_or_create_user_http_client_creates_client(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        client = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        assert client is mock_instance


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_caches(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        c1 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        c2 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        assert c1 is c2
        assert MockClient.call_count == 1  # Only created once for the same conversation


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_isolates_users(manager):
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        MockClient.side_effect = [MagicMock(), MagicMock()]
        c1 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        c2 = await manager._get_or_create_user_http_client(
            "state_server", "bob@test.com", "conv-1"
        )
        assert c1 is not c2
        assert MockClient.call_count == 2


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_isolates_conversations(manager):
    """Each conversation gets its own Client to keep FastMCP nesting counters separate."""
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        MockClient.side_effect = [MagicMock(), MagicMock()]
        c1 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        c2 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-2"
        )
        assert c1 is not c2
        assert MockClient.call_count == 2


@pytest.mark.asyncio
async def test_get_or_create_user_http_client_rejects_missing_conversation_id(manager):
    """A falsy conversation_id would alias every caller for (user, server)
    into one cache slot — exactly the cross-conversation aliasing bug this
    cache exists to prevent. Surface that as an error rather than silently
    sharing one Client.
    """
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    for bad_value in (None, ""):
        with pytest.raises(ValueError, match="conversation_id is required"):
            await manager._get_or_create_user_http_client(
                "state_server", "alice@test.com", bad_value
            )


# --- release_sessions tests ---


@pytest.mark.asyncio
async def test_release_sessions_evicts_only_target_conversation(manager):
    """release_sessions must evict only entries scoped to the target
    conversation_id, leaving other conversations for the same user
    (and other users) intact.
    """
    other_user_client = MagicMock()
    alice_conv1 = MagicMock()
    alice_conv2 = MagicMock()
    manager._user_clients = {
        ("alice@test.com", "state_server", "conv-1"): alice_conv1,
        ("alice@test.com", "state_server", "conv-2"): alice_conv2,
        ("bob@test.com", "state_server", "conv-1"): other_user_client,
    }

    session_manager_release = MagicMock()

    async def _async_release(_):
        session_manager_release()

    manager._session_manager = MagicMock()
    manager._session_manager.release_all = _async_release

    await manager.release_sessions("conv-1", user_email="alice@test.com")

    # Alice's conv-1 evicted; her conv-2 and Bob's conv-1 survive.
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_clients
    assert ("alice@test.com", "state_server", "conv-2") in manager._user_clients
    assert ("bob@test.com", "state_server", "conv-1") in manager._user_clients
    session_manager_release.assert_called_once()


@pytest.mark.asyncio
async def test_release_sessions_without_user_email_evicts_by_conversation(manager):
    """Internal callers without user context can still evict by conversation."""
    alice_conv1 = MagicMock()
    alice_conv1.__aexit__ = AsyncMock(return_value=False)
    bob_conv2 = MagicMock()
    bob_conv2.__aexit__ = AsyncMock(return_value=False)
    manager._user_clients = {
        ("alice@test.com", "state_server", "conv-1"): alice_conv1,
        ("bob@test.com", "state_server", "conv-2"): bob_conv2,
    }
    manager._user_client_last_used = {
        ("alice@test.com", "state_server", "conv-1"): time.time(),
        ("bob@test.com", "state_server", "conv-2"): time.time(),
    }
    release_called_with = {}

    async def _async_release(conv_id):
        release_called_with["arg"] = conv_id

    manager._session_manager = MagicMock()
    manager._session_manager.release_all = _async_release

    await manager.release_sessions("conv-1", user_email=None)

    assert release_called_with["arg"] == "conv-1"
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_clients
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_client_last_used
    assert ("bob@test.com", "state_server", "conv-2") in manager._user_clients
    alice_conv1.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_user_client_cache_evicts_lru_and_closes_client(manager):
    manager._user_client_cache_max_entries = 2
    manager.servers_config["state_server"] = {
        "url": "http://127.0.0.1:8010/mcp",
        "transport": "http",
    }
    manager._session_manager = MagicMock()
    manager._session_manager.release = AsyncMock()

    with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
        clients = [MagicMock(), MagicMock(), MagicMock()]
        for client in clients:
            client.__aexit__ = AsyncMock(return_value=False)
        MockClient.side_effect = clients

        c1 = await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-1"
        )
        await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-2"
        )
        await manager._get_or_create_user_http_client(
            "state_server", "alice@test.com", "conv-3"
        )

    assert c1 is clients[0]
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_clients
    assert len(manager._user_clients) == 2
    manager._session_manager.release.assert_awaited_once_with("conv-1", "state_server")
    clients[0].__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_idle_sweeper_evicts_stale_clients(manager):
    stale_key = ("alice@test.com", "state_server", "conv-1")
    fresh_key = ("alice@test.com", "state_server", "conv-2")
    stale_client = MagicMock()
    stale_client.__aexit__ = AsyncMock(return_value=False)
    fresh_client = MagicMock()
    fresh_client.__aexit__ = AsyncMock(return_value=False)
    now = time.time()
    manager._user_clients = {
        stale_key: stale_client,
        fresh_key: fresh_client,
    }
    manager._user_client_last_used = {
        stale_key: now - 7200,
        fresh_key: now,
    }
    manager._user_client_cache_idle_ttl_seconds = 3600
    manager._session_manager = MagicMock()
    manager._session_manager.release = AsyncMock()

    evicted = await manager._sweep_idle_user_clients_once()

    assert evicted == 1
    assert stale_key not in manager._user_clients
    assert fresh_key in manager._user_clients
    manager._session_manager.release.assert_awaited_once_with("conv-1", "state_server")
    stale_client.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_cleanup_closes_cached_clients(manager):
    cache_key = ("alice@test.com", "state_server", "conv-1")
    client = MagicMock()
    client.__aexit__ = AsyncMock(return_value=False)
    manager._user_clients = {cache_key: client}
    manager._user_client_last_used = {cache_key: time.time()}
    manager._session_manager = MagicMock()
    manager._session_manager._sessions = {}

    await manager.cleanup()

    assert manager._user_clients == {}
    assert manager._user_client_last_used == {}
    client.__aexit__.assert_awaited_once_with(None, None, None)
