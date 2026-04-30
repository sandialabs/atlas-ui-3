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
        mgr._user_client_cache_in_use_window_seconds = 60
        mgr._user_client_close_timeout_seconds = 5.0
        mgr._user_client_sweeper_task = None
        mgr._user_client_close_tasks = set()
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

    async def _async_release(_, user_email=None):
        assert user_email == "alice@test.com"
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
        ("alice@test.com", "state_server", "conv-1"): time.monotonic(),
        ("bob@test.com", "state_server", "conv-2"): time.monotonic(),
    }
    release_called_with = {}

    async def _async_release(conv_id, user_email=None):
        release_called_with["arg"] = conv_id
        release_called_with["user_email"] = user_email

    manager._session_manager = MagicMock()
    manager._session_manager.release_all = _async_release

    await manager.release_sessions("conv-1", user_email=None)

    assert release_called_with["arg"] == "conv-1"
    assert release_called_with["user_email"] is None
    # When release_sessions is called without user_email, the merged
    # behaviour falls back to conv-id-only eviction so internal callers
    # without auth context (e.g. WebSocket cleanup races) can still tear
    # down per-conversation cache entries. Bob's conv-2 entry under a
    # different conv stays untouched. PR #565 narrowed the *security*
    # scope at the entrypoints (chat/restore validate user ownership);
    # PR #564 retained the user-less internal fallback for cleanup paths.
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_clients
    assert ("alice@test.com", "state_server", "conv-1") not in manager._user_client_last_used
    assert ("bob@test.com", "state_server", "conv-2") in manager._user_clients
    alice_conv1.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_user_client_cache_evicts_lru_and_closes_client(manager):
    manager._user_client_cache_max_entries = 2
    # Disable the in-use window for this test so the LRU enforcer can evict
    # an entry that was just touched. The in-use-protection behaviour is
    # covered separately by test_lru_enforcement_skips_in_use_entries.
    manager._user_client_cache_in_use_window_seconds = 0
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
    now = time.monotonic()
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
    manager._user_client_last_used = {cache_key: time.monotonic()}
    manager._session_manager = MagicMock()
    manager._session_manager._sessions = {}

    await manager.cleanup()

    assert manager._user_clients == {}
    assert manager._user_client_last_used == {}
    client.__aexit__.assert_awaited_once_with(None, None, None)


# --- Review-feedback fixes (PR #564) ---


@pytest.mark.asyncio
async def test_lru_enforcement_skips_in_use_entries(manager):
    """LRU eviction must not tear down a client touched within the in-use
    window, otherwise an active tool call's connection could be ripped
    out from under it (dclaude review #1)."""
    manager._user_client_cache_max_entries = 1
    manager._user_client_cache_in_use_window_seconds = 60

    busy_key = ("alice@test.com", "state_server", "conv-busy")
    other_key = ("alice@test.com", "state_server", "conv-other")
    busy_client = MagicMock()
    busy_client.__aexit__ = AsyncMock(return_value=False)
    other_client = MagicMock()
    other_client.__aexit__ = AsyncMock(return_value=False)

    now = time.monotonic()
    manager._user_clients = {busy_key: busy_client, other_key: other_client}
    manager._user_client_last_used = {busy_key: now, other_key: now}

    evicted = manager._enforce_user_client_cache_limit_locked()

    assert evicted == []
    assert busy_key in manager._user_clients
    assert other_key in manager._user_clients
    busy_client.__aexit__.assert_not_called()
    other_client.__aexit__.assert_not_called()


@pytest.mark.asyncio
async def test_lru_enforcement_evicts_stale_when_some_in_use(manager):
    """Old entries are still evictable even when a newer entry is busy."""
    manager._user_client_cache_max_entries = 1
    manager._user_client_cache_in_use_window_seconds = 60

    stale_key = ("alice@test.com", "state_server", "conv-stale")
    busy_key = ("alice@test.com", "state_server", "conv-busy")
    stale_client = MagicMock()
    stale_client.__aexit__ = AsyncMock(return_value=False)
    busy_client = MagicMock()
    busy_client.__aexit__ = AsyncMock(return_value=False)

    now = time.monotonic()
    manager._user_clients = {stale_key: stale_client, busy_key: busy_client}
    manager._user_client_last_used = {
        stale_key: now - 600,
        busy_key: now,
    }

    evicted = manager._enforce_user_client_cache_limit_locked()

    assert [k for k, _ in evicted] == [stale_key]
    assert stale_key not in manager._user_clients
    assert busy_key in manager._user_clients


@pytest.mark.asyncio
async def test_close_user_client_entry_times_out_on_stuck_aexit(manager):
    """A stuck client.__aexit__ must not block teardown forever (dclaude
    review #2)."""
    manager._user_client_close_timeout_seconds = 0.05
    manager._session_manager = MagicMock()
    manager._session_manager.release = AsyncMock()

    stuck_client = MagicMock()

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(10)

    stuck_client.__aexit__ = AsyncMock(side_effect=_hang)

    cache_key = ("alice@test.com", "state_server", "conv-1")
    start = time.monotonic()
    await manager._close_user_client_entry(cache_key, stuck_client)
    elapsed = time.monotonic() - start

    # Should return well under the 10s hang because of asyncio.wait_for.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_cleanup_drains_inflight_sweeper_close(manager):
    """If the sweeper is cancelled while a close batch is in flight,
    cleanup() must still finish closing the popped clients (codex
    review #2)."""
    cache_key = ("alice@test.com", "state_server", "conv-1")
    client = MagicMock()

    close_started = asyncio.Event()
    close_can_finish = asyncio.Event()

    async def _slow_close(*_args, **_kwargs):
        close_started.set()
        await close_can_finish.wait()
        return False

    client.__aexit__ = AsyncMock(side_effect=_slow_close)
    manager._user_clients = {cache_key: client}
    manager._user_client_last_used = {cache_key: time.monotonic() - 9999}
    manager._user_client_cache_idle_ttl_seconds = 1
    manager._session_manager = MagicMock()
    manager._session_manager.release = AsyncMock()
    manager._session_manager._sessions = {}

    sweep_task = asyncio.create_task(manager._sweep_idle_user_clients_once())
    await close_started.wait()
    sweep_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sweep_task

    # The close batch is still in flight; cleanup() must drain it.
    cleanup_task = asyncio.create_task(manager.cleanup())
    await asyncio.sleep(0.05)
    assert not cleanup_task.done(), "cleanup must wait for the in-flight close"

    close_can_finish.set()
    await asyncio.wait_for(cleanup_task, timeout=1.0)

    client.__aexit__.assert_awaited()
    assert manager._user_client_close_tasks == set()


@pytest.mark.asyncio
async def test_sweeper_starts_after_failed_init(manager):
    """The cache sweeper start path must not be coupled to MCP discovery
    success: even if init throws, the sweeper still needs to run so the
    leak guard does not silently disable in degraded startup (codex
    review #1)."""
    manager._user_client_cache_sweep_interval_seconds = 3600  # don't tick during the test

    await manager.start_user_client_cache_sweeper()
    try:
        assert manager._user_client_sweeper_task is not None
        assert not manager._user_client_sweeper_task.done()
        assert (
            manager._user_client_sweeper_task.get_name()
            == "mcp-user-client-cache-sweeper"
        )
    finally:
        await manager.stop_user_client_cache_sweeper()
        assert manager._user_client_sweeper_task is None
