"""Tests for per-user MCP tool discovery (issue #912).

A server that gates ``tools/list`` behind authorization answers the anonymous
startup sweep with a 401 and is recorded with an empty tool list. These tests
cover the second discovery path: run with the user's own client, cached per
(user, server), and published into the shared inventory when -- and only when
-- the anonymous sweep found nothing.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from atlas.modules.mcp_tools import mcp_user_discovery
from atlas.modules.mcp_tools.client import MCPToolManager

SERVER = "remote-mcp"
USER = "user@example.gov"


def _tool(name, description="", schema=None):
    """A stand-in for a discovered MCP Tool object."""
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
        execution=None,
    )


class _FakeClient:
    """Async-context-manager client whose list_tools() is scripted."""

    def __init__(self, tools=None, error=None):
        self._tools = tools or []
        self._error = error
        self.list_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def list_tools(self):
        self.list_calls += 1
        if self._error is not None:
            raise self._error
        return list(self._tools)


def _manager(servers_config=None, available_tools=None):
    """A manager with only the state the discovery paths touch."""
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = servers_config if servers_config is not None else {
        SERVER: {"url": "https://mcp.example.com/mcp", "auth_type": "oauth"},
        "open-server": {"url": "https://open.example.com/mcp"},
    }
    manager.available_tools = available_tools if available_tools is not None else {}
    manager.available_prompts = {}
    manager._failed_servers = {}
    manager._tool_task_forbidden = set()
    manager._tool_index = {}
    manager._user_available_tools = {}
    manager._user_discovery_failures = {}
    return manager


@pytest.fixture(autouse=True)
def _fast_discovery_timeout():
    """Avoid depending on the real settings object for the timeout."""
    with patch.object(MCPToolManager, "_discovery_timeout", return_value=5):
        yield


class TestDiscoverToolsForUser:
    @pytest.mark.asyncio
    async def test_discovers_and_caches_tools_with_the_users_client(self):
        manager = _manager()
        client = _FakeClient([_tool("search"), _tool("fetch")])
        manager._get_user_client = AsyncMock(return_value=client)

        tools = await manager.discover_tools_for_user(USER, SERVER)

        assert [t.name for t in tools] == ["search", "fetch"]
        assert manager.get_user_tools_for_server(USER, SERVER) == tools
        # conversation_id is None: discovery must not borrow a conversation's
        # persistent session.
        manager._get_user_client.assert_awaited_once_with(SERVER, USER, None)

    @pytest.mark.asyncio
    async def test_cached_result_is_reused_within_the_ttl(self):
        manager = _manager()
        client = _FakeClient([_tool("search")])
        manager._get_user_client = AsyncMock(return_value=client)

        await manager.discover_tools_for_user(USER, SERVER)
        await manager.discover_tools_for_user(USER, SERVER)

        assert client.list_calls == 1

    @pytest.mark.asyncio
    async def test_concurrent_callers_share_one_discovery(self):
        """/api/config is polled; a slow server must not get a client per request."""
        manager = _manager()
        client = _FakeClient([_tool("search")])
        started = asyncio.Event()

        async def slow_client(server_name, user_email, conversation_id):
            started.set()
            await asyncio.sleep(0)
            return client

        manager._get_user_client = slow_client

        results = await asyncio.gather(
            manager.discover_tools_for_user(USER, SERVER),
            manager.discover_tools_for_user(USER, SERVER),
        )

        assert client.list_calls == 1
        assert all(r is not None for r in results)

    @pytest.mark.asyncio
    async def test_force_bypasses_the_cache(self):
        manager = _manager()
        client = _FakeClient([_tool("search")])
        manager._get_user_client = AsyncMock(return_value=client)

        await manager.discover_tools_for_user(USER, SERVER)
        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert client.list_calls == 2

    @pytest.mark.asyncio
    async def test_lookup_is_case_insensitive_in_the_user_email(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER.upper(), SERVER)

        assert manager.get_user_tools_for_server(USER, SERVER) is not None

    @pytest.mark.asyncio
    async def test_no_token_returns_none_and_cools_down(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=None)

        assert await manager.discover_tools_for_user(USER, SERVER) is None
        assert await manager.discover_tools_for_user(USER, SERVER) is None

        # The second call must not have gone back to the client factory: an
        # unauthenticated user hitting /api/config repeatedly would otherwise
        # re-probe on every request.
        assert manager._get_user_client.await_count == 1

    @pytest.mark.asyncio
    async def test_cool_down_expires(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=None)

        await manager.discover_tools_for_user(USER, SERVER)
        key = (USER, SERVER)
        manager._user_discovery_failures[key] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_RETRY_SECONDS - 1
        )
        await manager.discover_tools_for_user(USER, SERVER)

        assert manager._get_user_client.await_count == 2

    @pytest.mark.asyncio
    async def test_server_error_does_not_mark_the_server_globally_failed(self):
        """One user's expired token must not poison the shared failure state."""
        manager = _manager()
        manager._get_user_client = AsyncMock(
            return_value=_FakeClient(error=RuntimeError("401 Unauthorized"))
        )

        assert await manager.discover_tools_for_user(USER, SERVER) is None
        assert manager._failed_servers == {}

    @pytest.mark.asyncio
    async def test_skips_servers_that_do_not_require_user_auth(self):
        manager = _manager()
        manager._get_user_client = AsyncMock()

        assert await manager.discover_tools_for_user(USER, "open-server") is None
        manager._get_user_client.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_there_is_no_user(self):
        manager = _manager()
        manager._get_user_client = AsyncMock()

        assert await manager.discover_tools_for_user("", SERVER) is None
        manager._get_user_client.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_support_metadata_is_recorded(self):
        manager = _manager()
        forbidden = _tool("legacy")
        optional = _tool("modern")
        optional.execution = SimpleNamespace(taskSupport="optional")
        manager._get_user_client = AsyncMock(
            return_value=_FakeClient([forbidden, optional])
        )

        await manager.discover_tools_for_user(USER, SERVER)

        assert (SERVER, "legacy") in manager._tool_task_forbidden
        assert (SERVER, "modern") not in manager._tool_task_forbidden


class TestPromotionIntoTheSharedInventory:
    @pytest.mark.asyncio
    async def test_tools_become_schedulable_and_routable(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER, SERVER)

        assert [t.name for t in manager.available_tools[SERVER]["tools"]] == ["search"]
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER
        schema = manager.get_tools_schema([f"{SERVER}_search"])
        assert schema and schema[0]["function"]["name"] == f"{SERVER}_search"

    @pytest.mark.asyncio
    async def test_a_successful_anonymous_sweep_is_never_overwritten(self):
        existing = [_tool("public")]
        manager = _manager(
            available_tools={SERVER: {"tools": existing, "config": {}}}
        )
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("private")]))

        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert manager.available_tools[SERVER]["tools"] == existing
        # The per-user view is still recorded, it just does not replace the
        # operator-visible catalogue.
        assert [t.name for t in manager.get_user_tools_for_server(USER, SERVER)] == [
            "private"
        ]

    @pytest.mark.asyncio
    async def test_the_global_failure_record_is_left_alone(self):
        """One user's success does not prove the process-level client works.

        Clearing it would drop the server out of reconnect tracking while
        reporting it healthy to operators.
        """
        manager = _manager()
        manager._record_server_failure(SERVER, "401 Unauthorized")
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER, SERVER)

        assert SERVER in manager._failed_servers

    @pytest.mark.asyncio
    async def test_an_empty_catalogue_is_not_promoted(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))

        await manager.discover_tools_for_user(USER, SERVER)

        assert SERVER not in manager.available_tools


class TestDiscoverToolsForUserServers:
    @pytest.mark.asyncio
    async def test_only_probes_auth_servers_with_an_empty_catalogue(self):
        manager = _manager(
            available_tools={"already": {"tools": [_tool("x")], "config": {}}}
        )
        manager.servers_config = {
            SERVER: {"auth_type": "oauth"},
            "already": {"auth_type": "oauth"},
            "open-server": {},
        }
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        found = await manager.discover_tools_for_user_servers(
            USER, [SERVER, "already", "open-server"]
        )

        assert list(found) == [SERVER]
        manager._get_user_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_broken_server_does_not_sink_the_others(self):
        manager = _manager()
        manager.servers_config = {
            "good": {"auth_type": "oauth"},
            "bad": {"auth_type": "oauth"},
        }

        async def get_client(server_name, user_email, conversation_id):
            if server_name == "bad":
                raise RuntimeError("boom")
            return _FakeClient([_tool("search")])

        manager._get_user_client = get_client

        found = await manager.discover_tools_for_user_servers(USER, ["good", "bad"])

        assert list(found) == ["good"]

    @pytest.mark.asyncio
    async def test_no_candidates_does_no_work(self):
        manager = _manager()
        manager._get_user_client = AsyncMock()

        assert await manager.discover_tools_for_user_servers(USER, ["open-server"]) == {}
        manager._get_user_client.assert_not_awaited()


class TestCacheInvalidation:
    @pytest.mark.asyncio
    async def test_clearing_one_user_and_server_leaves_the_rest(self):
        manager = _manager()
        manager.servers_config[SERVER + "-2"] = {"auth_type": "oauth"}
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER, SERVER)
        await manager.discover_tools_for_user(USER, SERVER + "-2")
        await manager.discover_tools_for_user("other@example.gov", SERVER)

        manager.clear_user_tool_cache(USER, SERVER)

        assert manager.get_user_tools_for_server(USER, SERVER) is None
        assert manager.get_user_tools_for_server(USER, SERVER + "-2") is not None
        assert manager.get_user_tools_for_server("other@example.gov", SERVER) is not None

    @pytest.mark.asyncio
    async def test_clearing_everything(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        manager.clear_user_tool_cache()

        assert manager.get_user_tools_for_server(USER, SERVER) is None

    @pytest.mark.asyncio
    async def test_full_rediscovery_drops_the_per_user_caches(self):
        """A config reload resets available_tools, so promoted views are stale."""
        manager = _manager()
        manager.clients = {}
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        await manager.discover_tools()

        assert manager.get_user_tools_for_server(USER, SERVER) is None


class TestOneUsersViewIsNotAnothers:
    """A gated server hid its metadata from anonymous callers on purpose."""

    @pytest.mark.asyncio
    async def test_a_promoted_catalogue_is_not_shown_to_another_user(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        assert [t.name for t in manager.get_visible_tools_for_server(USER, SERVER)] == [
            "search"
        ]
        assert manager.get_visible_tools_for_server("other@example.gov", SERVER) == []

    @pytest.mark.asyncio
    async def test_an_anonymous_catalogue_is_shown_to_everyone(self):
        manager = _manager(
            available_tools={SERVER: {"tools": [_tool("public")], "config": {}}}
        )

        assert [
            t.name for t in manager.get_visible_tools_for_server("anyone@example.gov", SERVER)
        ] == ["public"]

    @pytest.mark.asyncio
    async def test_disconnecting_withdraws_the_published_catalogue(self):
        """A revoked user's tool names must not stay routable for everyone."""
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER

        manager.clear_user_tool_cache(USER, SERVER)

        assert manager.available_tools[SERVER]["tools"] == []
        assert manager.get_server_for_tool(f"{SERVER}_search") is None

    @pytest.mark.asyncio
    async def test_another_users_catalogue_is_left_published(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        manager.clear_user_tool_cache("other@example.gov", SERVER)

        assert manager.available_tools[SERVER]["tools"]

    @pytest.mark.asyncio
    async def test_a_promoted_server_is_still_a_discovery_candidate(self):
        """Otherwise the TTL never refreshes for exactly these servers."""
        manager = _manager()
        client = _FakeClient([_tool("search")])
        manager._get_user_client = AsyncMock(return_value=client)
        await manager.discover_tools_for_user(USER, SERVER)

        # A second user has their own discovery run rather than inheriting the
        # first user's promoted view.
        await manager.discover_tools_for_user_servers("other@example.gov", [SERVER])

        assert client.list_calls == 2

    @pytest.mark.asyncio
    async def test_an_anonymously_discovered_server_is_never_a_candidate(self):
        manager = _manager(
            available_tools={SERVER: {"tools": [_tool("public")], "config": {}}}
        )
        manager._get_user_client = AsyncMock()

        await manager.discover_tools_for_user_servers(USER, [SERVER])

        manager._get_user_client.assert_not_awaited()


class TestTaskSupportMetadataIsNotClobbered:
    @pytest.mark.asyncio
    async def test_a_declined_promotion_leaves_the_sweeps_entries_alone(self):
        """A narrower per-user view must not flip tools to task-allowed."""
        manager = _manager(
            available_tools={SERVER: {"tools": [_tool("public")], "config": {}}}
        )
        manager._tool_task_forbidden = {(SERVER, "public"), (SERVER, "other")}
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert (SERVER, "public") in manager._tool_task_forbidden
        assert (SERVER, "other") in manager._tool_task_forbidden


class TestTheCallerCanBoundTheWait:
    @pytest.mark.asyncio
    async def test_a_slow_server_does_not_stall_the_caller(self):
        manager = _manager()
        release = asyncio.Event()

        async def slow_client(server_name, user_email, conversation_id):
            await release.wait()
            return _FakeClient([_tool("search")])

        manager._get_user_client = slow_client

        found = await manager.discover_tools_for_user_servers(
            USER, [SERVER], wait_timeout=0.01
        )
        assert found == {}

        # The discovery was shielded, not cancelled: it finishes in the
        # background and its result is there for the next request.
        release.set()
        for _ in range(50):
            await asyncio.sleep(0)
            if manager.get_user_tools_for_server(USER, SERVER):
                break
        assert [t.name for t in manager.get_user_tools_for_server(USER, SERVER)] == [
            "search"
        ]
