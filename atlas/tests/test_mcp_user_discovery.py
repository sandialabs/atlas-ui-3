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


class TestTheDiscoveryTimeoutCoversConnectionSetup:
    """An auth-gated server that hangs on ``initialize`` is the shape at issue."""

    @pytest.mark.asyncio
    async def test_a_client_that_hangs_before_list_tools_times_out(self):
        manager = _manager()

        class _HangingClient:
            def __init__(self):
                self.entered = False

            async def __aenter__(self):
                self.entered = True
                await asyncio.Event().wait()  # never opens

            async def __aexit__(self, *exc_info):
                return False

            async def list_tools(self):  # pragma: no cover - never reached
                raise AssertionError("list_tools must not be reached")

        client = _HangingClient()
        manager._get_user_client = AsyncMock(return_value=client)

        with patch.object(MCPToolManager, "_discovery_timeout", return_value=0.01):
            tools = await manager.discover_tools_for_user(USER, SERVER)

        assert tools is None
        assert client.entered
        # A timeout is a per-user failure, not a global one, and it cools down.
        assert manager._user_discovery_failures
        assert manager._failed_servers == {}


class TestTheCachesAreBounded:
    @pytest.mark.asyncio
    async def test_expired_entries_are_evicted_once_past_the_ceiling(self):
        manager = _manager()
        stale = time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        for i in range(mcp_user_discovery._MAX_USER_DISCOVERY_ENTRIES + 1):
            manager._user_available_tools[(f"u{i}@example.gov", SERVER)] = {
                "tools": [_tool("search")],
                "config": {},
                "discovered_at": stale,
            }
        manager._user_discovery_failures[("cold@example.gov", SERVER)] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_RETRY_SECONDS - 1
        )
        manager._user_discovery_failures[("warm@example.gov", SERVER)] = time.time()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))

        await manager.discover_tools_for_user(USER, SERVER)

        # Only the fresh entry -- this run's own -- survives the sweep.
        assert list(manager._user_available_tools) == [(USER, SERVER)]
        # A cool-down that has not expired is still honoured.
        assert list(manager._user_discovery_failures) == [("warm@example.gov", SERVER)]

    @pytest.mark.asyncio
    async def test_a_repeatedly_failing_user_also_triggers_the_sweep(self):
        """The failure path writes to the caches too, so it must prune too."""
        manager = _manager()
        stale = time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        for i in range(mcp_user_discovery._MAX_USER_DISCOVERY_ENTRIES + 1):
            manager._user_available_tools[(f"u{i}@example.gov", SERVER)] = {
                "tools": [_tool("search")],
                "config": {},
                "discovered_at": stale,
            }
        manager._get_user_client = AsyncMock(return_value=None)  # no token

        assert await manager.discover_tools_for_user(USER, SERVER) is None

        assert manager._user_available_tools == {}


class TestSeveralUsersCanOwnOnePromotedCatalogue:
    """Ownership is a set: one owner leaving must not strand the others."""

    async def _promote_for(self, manager, user, tool_name):
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool(tool_name)]))
        await manager.discover_tools_for_user(user, SERVER, force=True)

    @pytest.mark.asyncio
    async def test_the_first_owners_disconnect_still_withdraws_their_claim(self):
        manager = _manager()
        other = "other@example.gov"
        await self._promote_for(manager, USER, "search")
        await self._promote_for(manager, other, "search")

        assert manager.available_tools[SERVER]["discovered_for"] == {USER, other}

        manager.clear_user_tool_cache(USER, SERVER)

        # The second user still holds a catalogue, so it stays published --
        # and published for them alone.
        assert manager.available_tools[SERVER]["discovered_for"] == {other}
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER

    @pytest.mark.asyncio
    async def test_a_survivors_catalogue_is_republished_not_left_stale(self):
        """Otherwise the listing and get_server_for_tool disagree."""
        manager = _manager()
        other = "other@example.gov"
        await self._promote_for(manager, USER, "mine")
        await self._promote_for(manager, other, "theirs")

        # The second promotion published 'theirs'; when that user leaves, the
        # first user's tools must become routable again, not vanish.
        manager.clear_user_tool_cache(other, SERVER)

        assert [t.name for t in manager.available_tools[SERVER]["tools"]] == ["mine"]
        assert manager.get_server_for_tool(f"{SERVER}_mine") == SERVER
        assert manager.get_server_for_tool(f"{SERVER}_theirs") is None
        assert [
            t.name for t in manager.get_visible_tools_for_server(USER, SERVER)
        ] == ["mine"]

    @pytest.mark.asyncio
    async def test_the_last_owner_leaving_empties_the_server(self):
        manager = _manager()
        other = "other@example.gov"
        await self._promote_for(manager, USER, "search")
        await self._promote_for(manager, other, "search")

        manager.clear_user_tool_cache(USER, SERVER)
        manager.clear_user_tool_cache(other, SERVER)

        assert manager.available_tools[SERVER]["tools"] == []
        assert manager.get_server_for_tool(f"{SERVER}_search") is None

    @pytest.mark.asyncio
    async def test_an_expired_survivor_does_not_keep_the_catalogue_alive(self):
        manager = _manager()
        other = "other@example.gov"
        await self._promote_for(manager, USER, "mine")
        await self._promote_for(manager, other, "theirs")
        # The first user's entry is past its TTL: it is no longer evidence
        # that they can still see anything.
        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )

        manager.clear_user_tool_cache(other, SERVER)

        assert manager.available_tools[SERVER]["tools"] == []


class TestAPublicCatalogueIsNotHiddenByAnEmptyProbe:
    @pytest.mark.asyncio
    async def test_an_empty_authenticated_result_falls_back_to_the_sweep(self):
        """A server may gate invocation but answer tools/list to anyone."""
        manager = _manager(
            available_tools={SERVER: {"tools": [_tool("public")], "config": {}}}
        )
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))

        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert [
            t.name for t in manager.get_visible_tools_for_server(USER, SERVER)
        ] == ["public"]

    @pytest.mark.asyncio
    async def test_an_empty_probe_against_a_gated_server_still_shows_nothing(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))

        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert manager.get_visible_tools_for_server(USER, SERVER) == []


class TestAStaleCatalogueIsNotServedForever:
    """A per-user catalogue is a claim about a token that worked once."""

    @pytest.mark.asyncio
    async def test_an_expired_entry_is_not_returned_to_its_owner(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )

        assert manager.get_user_tools_for_server(USER, SERVER) is None

    @pytest.mark.asyncio
    async def test_a_cool_down_does_not_hand_back_a_stale_catalogue(self):
        """The cool-down suppresses the retry, not the expiry."""
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )
        # The token has stopped working.
        manager._get_user_client = AsyncMock(return_value=None)

        assert await manager.discover_tools_for_user(USER, SERVER) is None
        # Cooling down now -- and still not serving what the dead token bought.
        assert await manager.discover_tools_for_user(USER, SERVER) is None
        assert manager.get_user_tools_for_server(USER, SERVER) is None

    @pytest.mark.asyncio
    async def test_a_revoked_token_unpublishes_the_stale_catalogue(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER

        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )
        manager._get_user_client = AsyncMock(return_value=None)
        await manager.discover_tools_for_user(USER, SERVER)

        assert manager.available_tools[SERVER]["tools"] == []
        assert manager.get_server_for_tool(f"{SERVER}_search") is None

    @pytest.mark.asyncio
    async def test_a_fresh_catalogue_survives_one_failed_probe(self):
        """A briefly unreachable server must not cost a live token its tools."""
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)

        manager._get_user_client = AsyncMock(return_value=None)
        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert [t.name for t in manager.get_user_tools_for_server(USER, SERVER)] == [
            "search"
        ]
        assert manager.available_tools[SERVER]["tools"]


class TestAnEmptyRediscoveryUnpublishes:
    @pytest.mark.asyncio
    async def test_a_second_empty_discovery_withdraws_the_first_catalogue(self):
        """Otherwise the old names stay routable with no live owner."""
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER

        # The server now answers this user with nothing -- their grant was cut.
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert manager.available_tools[SERVER]["tools"] == []
        assert manager.get_server_for_tool(f"{SERVER}_search") is None
        assert manager.get_visible_tools_for_server(USER, SERVER) == []

    @pytest.mark.asyncio
    async def test_an_empty_discovery_leaves_another_owners_catalogue(self):
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("theirs")]))
        await manager.discover_tools_for_user(other, SERVER)
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert [t.name for t in manager.available_tools[SERVER]["tools"]] == ["theirs"]
        assert manager.get_server_for_tool(f"{SERVER}_theirs") == SERVER

    @pytest.mark.asyncio
    async def test_an_empty_discovery_does_not_touch_an_anonymous_catalogue(self):
        manager = _manager(
            available_tools={SERVER: {"tools": [_tool("public")], "config": {}}}
        )
        manager._get_user_client = AsyncMock(return_value=_FakeClient([]))

        await manager.discover_tools_for_user(USER, SERVER, force=True)

        assert [t.name for t in manager.available_tools[SERVER]["tools"]] == ["public"]


class TestTaskSupportFollowsTheRepublishedSet:
    @pytest.mark.asyncio
    async def test_the_heirs_tools_are_re_swept(self):
        """_tool_task_forbidden must describe the catalogue actually published."""
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("mine")]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("theirs")]))
        await manager.discover_tools_for_user(other, SERVER, force=True)
        # Stale verdict from the departing owner's sweep.
        manager._tool_task_forbidden = {(SERVER, "theirs"), (SERVER, "mine")}

        manager.clear_user_tool_cache(other, SERVER)

        assert [t.name for t in manager.available_tools[SERVER]["tools"]] == ["mine"]
        # Rebuilt from the heir's tools, so the departed owner's entry is gone.
        assert (SERVER, "theirs") not in manager._tool_task_forbidden


class TestTheIndexIsRebuiltOncePerBulkClear:
    @pytest.mark.asyncio
    async def test_a_full_clear_rebuilds_the_index_once(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        for i in range(5):
            await manager.discover_tools_for_user(f"u{i}@example.gov", SERVER, force=True)

        with patch.object(
            MCPToolManager, "_rebuild_tool_index", autospec=True
        ) as rebuild:
            manager.clear_user_tool_cache()

        assert rebuild.call_count == 1

    @pytest.mark.asyncio
    async def test_a_clear_that_withdraws_nothing_rebuilds_nothing(self):
        manager = _manager()
        manager._user_available_tools[("nobody@example.gov", SERVER)] = {
            "tools": [], "config": {}, "discovered_at": time.time(),
        }

        with patch.object(
            MCPToolManager, "_rebuild_tool_index", autospec=True
        ) as rebuild:
            manager.clear_user_tool_cache()

        assert rebuild.call_count == 0


class TestSchemaMetadataIsScopedToItsOwner:
    """A gated server's descriptions and input schemas are not public."""

    async def _promoted(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(
            return_value=_FakeClient([_tool("search", description="private detail")])
        )
        await manager.discover_tools_for_user(USER, SERVER)
        return manager

    @pytest.mark.asyncio
    async def test_the_owner_gets_the_schema(self):
        manager = await self._promoted()

        schema = manager.get_tools_schema([f"{SERVER}_search"], USER)

        assert [s["function"]["name"] for s in schema] == [f"{SERVER}_search"]
        assert schema[0]["function"]["description"] == "private detail"

    @pytest.mark.asyncio
    async def test_another_user_gets_nothing(self):
        manager = await self._promoted()

        assert manager.get_tools_schema([f"{SERVER}_search"], "other@example.gov") == []

    @pytest.mark.asyncio
    async def test_an_omitted_user_keeps_the_historic_behaviour(self):
        """Internal callers resolving an already-authorized tool still work."""
        manager = await self._promoted()

        assert len(manager.get_tools_schema([f"{SERVER}_search"])) == 1

    @pytest.mark.asyncio
    async def test_an_anonymous_catalogue_is_readable_by_anyone(self):
        manager = _manager(
            available_tools={
                SERVER: {"tools": [_tool("public", description="fine")], "config": {}}
            }
        )

        schema = manager.get_tools_schema([f"{SERVER}_public"], "anyone@example.gov")

        assert [s["function"]["name"] for s in schema] == [f"{SERVER}_public"]

    @pytest.mark.asyncio
    async def test_a_co_owner_gets_the_schema(self):
        manager = await self._promoted()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(other, SERVER, force=True)

        assert len(manager.get_tools_schema([f"{SERVER}_search"], other)) == 1
        assert len(manager.get_tools_schema([f"{SERVER}_search"], USER)) == 1


class TestCoOwnersDoNotClobberEachOther:
    """The shared entry routes for everyone; reads stay per-owner."""

    async def _two_owners(self):
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("mine")]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("theirs")]))
        await manager.discover_tools_for_user(other, SERVER, force=True)
        return manager, other

    @pytest.mark.asyncio
    async def test_both_owners_tools_stay_routable(self):
        manager, other = await self._two_owners()

        assert manager.get_server_for_tool(f"{SERVER}_mine") == SERVER
        assert manager.get_server_for_tool(f"{SERVER}_theirs") == SERVER

    @pytest.mark.asyncio
    async def test_each_owner_is_listed_only_their_own(self):
        manager, other = await self._two_owners()

        assert [
            t.name for t in manager.get_visible_tools_for_server(USER, SERVER)
        ] == ["mine"]
        assert [
            t.name for t in manager.get_visible_tools_for_server(other, SERVER)
        ] == ["theirs"]

    @pytest.mark.asyncio
    async def test_an_owner_cannot_read_the_other_owners_schema(self):
        """Routable is not readable: co-ownership is not shared disclosure."""
        manager, other = await self._two_owners()

        assert manager.get_tools_schema([f"{SERVER}_theirs"], USER) == []
        assert len(manager.get_tools_schema([f"{SERVER}_theirs"], other)) == 1
        assert manager.get_tools_schema([f"{SERVER}_mine"], other) == []
        assert len(manager.get_tools_schema([f"{SERVER}_mine"], USER)) == 1


class TestReadAccessExpiresWithTheEntry:
    @pytest.mark.asyncio
    async def test_an_expired_owner_may_no_longer_read(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        assert len(manager.get_tools_schema([f"{SERVER}_search"], USER)) == 1

        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )

        assert manager.get_tools_schema([f"{SERVER}_search"], USER) == []

    @pytest.mark.asyncio
    async def test_pruning_withdraws_what_it_evicts(self):
        manager = _manager()
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("search")]))
        await manager.discover_tools_for_user(USER, SERVER)
        assert manager.get_server_for_tool(f"{SERVER}_search") == SERVER

        manager._user_available_tools[(USER, SERVER)]["discovered_at"] = (
            time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1
        )
        # Push the cache past the ceiling so the sweep runs.
        for i in range(mcp_user_discovery._MAX_USER_DISCOVERY_ENTRIES + 1):
            manager._user_available_tools[(f"u{i}@example.gov", "other-server")] = {
                "tools": [], "config": {},
                "discovered_at": time.time() - mcp_user_discovery._USER_DISCOVERY_TTL_SECONDS - 1,
            }
        manager._prune_user_discovery_state()

        assert (USER, SERVER) not in manager._user_available_tools
        assert manager.available_tools[SERVER]["tools"] == []
        assert manager.get_server_for_tool(f"{SERVER}_search") is None


class TestTaskSupportCoversEveryPublishedTool:
    @pytest.mark.asyncio
    async def test_a_co_owners_forbidden_tool_stays_forbidden(self):
        """The sweep purges (server, *) and rebuilds, so it must see the union."""
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("mine")]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)
        # The first owner's tool declares no taskSupport, so the sweep marks it.
        manager._tool_task_forbidden.add((SERVER, "mine"))

        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("theirs")]))
        await manager.discover_tools_for_user(other, SERVER, force=True)

        assert (SERVER, "mine") in manager._tool_task_forbidden

    @pytest.mark.asyncio
    async def test_both_owners_tools_are_published(self):
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("mine")]))
        await manager.discover_tools_for_user(USER, SERVER, force=True)
        manager._get_user_client = AsyncMock(return_value=_FakeClient([_tool("theirs")]))
        await manager.discover_tools_for_user(other, SERVER, force=True)

        assert sorted(t.name for t in manager.available_tools[SERVER]["tools"]) == [
            "mine", "theirs"
        ]


class TestEachOwnerGetsTheirOwnToolObject:
    @pytest.mark.asyncio
    async def test_a_same_named_tool_resolves_per_owner(self):
        """The index keeps one object per name; the schema must not leak it."""
        manager = _manager()
        other = "other@example.gov"
        manager._get_user_client = AsyncMock(
            return_value=_FakeClient([_tool("search", description="mine only")])
        )
        await manager.discover_tools_for_user(USER, SERVER, force=True)
        manager._get_user_client = AsyncMock(
            return_value=_FakeClient([_tool("search", description="theirs only")])
        )
        await manager.discover_tools_for_user(other, SERVER, force=True)

        mine = manager.get_tools_schema([f"{SERVER}_search"], USER)
        theirs = manager.get_tools_schema([f"{SERVER}_search"], other)

        assert mine[0]["function"]["description"] == "mine only"
        assert theirs[0]["function"]["description"] == "theirs only"
