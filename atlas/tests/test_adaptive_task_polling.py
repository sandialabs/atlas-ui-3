"""Tests for adaptive background task polling in MCPToolManager.call_tool."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.session_manager import ManagedSession


@pytest.fixture
def manager():
    """Create a MCPToolManager with mocked internals for testing call_tool."""
    tm = MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")
    tm._server_task_support = {}
    return tm


def _make_mock_client(*, task_support=True, immediate=True, wait_timeout=False):
    """Create a mock client with configurable ToolTask behavior."""
    mock_client = AsyncMock()

    mock_task = MagicMock()
    mock_task.returned_immediately = immediate
    mock_result = MagicMock()
    mock_result.content = [MagicMock(type="text", text="done")]
    mock_result.structured_content = None
    mock_result.data = None
    mock_task.result = AsyncMock(return_value=mock_result)
    mock_task.cancel = AsyncMock()
    mock_task.on_status_change = MagicMock()

    if wait_timeout:
        mock_task.wait = AsyncMock(side_effect=asyncio.TimeoutError)
    else:
        mock_wait_result = MagicMock()
        mock_wait_result.state = "completed"
        mock_task.wait = AsyncMock(return_value=mock_wait_result)

    mock_client.call_tool = AsyncMock(return_value=mock_task)

    # Simulate task support via initialize_result
    if task_support:
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result
    else:
        mock_client.initialize_result = None

    return mock_client, mock_task


class TestAdaptiveTaskPolling:
    @pytest.mark.asyncio
    async def test_immediate_result_returns_without_ui_notification(self, manager):
        """When task.returned_immediately is True, no UI events sent."""
        mock_client, mock_task = _make_mock_client(immediate=True)
        update_cb = AsyncMock()

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        _result = await manager.call_tool(
            "test-server", "tool_a", {},
            conversation_id="conv-1",
            meta={"tool_call_id": "tc-1"},
            update_cb=update_cb,
        )

        for call in update_cb.call_args_list:
            assert call[0][0].get("type") != "tool_task_started"

        mock_task.result.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_task_support_falls_back_to_blocking(self, manager):
        """Servers without task support use blocking call_tool."""
        mock_client, _ = _make_mock_client(task_support=False)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        _result = await manager.call_tool(
            "test-server", "tool_a", {},
            conversation_id="conv-1",
        )

        call_kwargs = mock_client.call_tool.call_args
        assert call_kwargs.kwargs.get("task") is not True

    @pytest.mark.asyncio
    async def test_cancellation_calls_task_cancel(self, manager):
        """When asyncio cancels the call, ToolTask.cancel() is invoked."""
        mock_client, mock_task = _make_mock_client(immediate=False)
        mock_task.wait = AsyncMock(side_effect=asyncio.CancelledError)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        with pytest.raises(asyncio.CancelledError):
            await manager.call_tool(
                "test-server", "tool_a", {},
                conversation_id="conv-1",
                meta={"tool_call_id": "tc-1"},
            )

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_then_poll_sends_ui_notifications(self, manager):
        """When initial wait times out, switches to polling with UI progress events."""
        mock_client = AsyncMock()

        # Create a mock task where first wait() times out, second wait() succeeds
        mock_task = MagicMock()
        mock_task.returned_immediately = False
        mock_result = MagicMock()
        mock_result.content = [MagicMock(type="text", text="done")]
        mock_result.structured_content = None
        mock_result.data = None
        mock_task.result = AsyncMock(return_value=mock_result)
        mock_task.cancel = AsyncMock()
        mock_task.on_status_change = MagicMock()

        # First wait() raises TimeoutError, second wait() succeeds
        wait_call_count = 0
        async def mock_wait(timeout=None):
            nonlocal wait_call_count
            wait_call_count += 1
            if wait_call_count == 1:
                raise asyncio.TimeoutError()
            return MagicMock(state="completed")

        mock_task.wait = mock_wait
        mock_client.call_tool = AsyncMock(return_value=mock_task)

        # Simulate task support
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["test-server"] = mock_client
        manager.servers_config["test-server"] = {}

        update_cb = AsyncMock()

        _result = await manager.call_tool(
            "test-server", "tool_a", {},
            conversation_id="conv-1",
            meta={"tool_call_id": "tc-1"},
            update_cb=update_cb,
        )

        # Verify UI was notified: tool_task_started and tool_task_completed
        event_types = [call[0][0]["type"] for call in update_cb.call_args_list]
        assert "tool_task_started" in event_types
        assert "tool_task_completed" in event_types

        # Verify on_status_change was registered for progress
        mock_task.on_status_change.assert_called_once()

        mock_task.result.assert_awaited()


class TestTaskForbiddenFallback:
    """A server may advertise task support while individual tools refuse
    task-augmented execution (fastmcp `tasks.mode="forbidden"`). The manager
    must fall back to a synchronous call and cache the decision per tool."""

    @pytest.mark.asyncio
    async def test_task_forbidden_falls_back_to_sync_and_returns_result(self, manager):
        """First call with task=True raises the forbidden McpError; manager
        retries the same tool with a sync call and returns that result."""
        mock_client = AsyncMock()

        # Advertise task support at the server level
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        # Synchronous (non-task) result the fallback should return
        sync_result = MagicMock()
        sync_result.content = [MagicMock(type="text", text="42")]
        sync_result.structured_content = None
        sync_result.data = None

        # First call (task=True) raises; second call (no task) returns sync_result
        call_history = []

        async def fake_call_tool(tool_name, arguments, **kwargs):
            call_history.append(kwargs)
            if kwargs.get("task") is True:
                raise Exception(
                    "FunctionTool 'tool:evaluate@' does not support task-augmented execution"
                )
            return sync_result

        mock_client.call_tool = AsyncMock(side_effect=fake_call_tool)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["srv"] = mock_client
        manager.servers_config["srv"] = {}

        result = await manager.call_tool(
            "srv", "evaluate", {"x": 1},
            conversation_id="conv-1",
            meta={"tool_call_id": "tc-1"},
        )

        assert result is sync_result
        # First attempt used task=True, second attempt dropped it
        assert call_history[0].get("task") is True
        assert call_history[1].get("task") is not True
        # Tool is now remembered as forbidden
        assert ("srv", "evaluate") in manager._tool_task_forbidden

    @pytest.mark.asyncio
    async def test_forbidden_cache_skips_task_mode_on_subsequent_call(self, manager):
        """After the cache is populated, the next call for that (server, tool)
        must not attempt task mode at all."""
        mock_client = AsyncMock()
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        sync_result = MagicMock()
        sync_result.content = [MagicMock(type="text", text="ok")]
        sync_result.structured_content = None
        sync_result.data = None
        mock_client.call_tool = AsyncMock(return_value=sync_result)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["srv"] = mock_client
        manager.servers_config["srv"] = {}
        # Pre-seed the forbidden cache as if a prior call had discovered it
        manager._tool_task_forbidden.add(("srv", "evaluate"))

        await manager.call_tool(
            "srv", "evaluate", {},
            conversation_id="conv-1",
        )

        # Only one underlying call, and it was the sync (no task=True) path
        assert mock_client.call_tool.call_count == 1
        assert mock_client.call_tool.call_args.kwargs.get("task") is not True

    @pytest.mark.asyncio
    async def test_non_forbidden_error_is_not_swallowed(self, manager):
        """Unrelated errors during task-mode call must still propagate."""
        mock_client = AsyncMock()
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("boom"))

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["srv"] = mock_client
        manager.servers_config["srv"] = {}

        with pytest.raises(RuntimeError, match="boom"):
            await manager.call_tool(
                "srv", "evaluate", {},
                conversation_id="conv-1",
            )

        # Did not poison the cache
        assert ("srv", "evaluate") not in manager._tool_task_forbidden

    @pytest.mark.asyncio
    async def test_task_forbidden_as_immediate_error_result_falls_back_to_sync(self, manager):
        """fastmcp's real behavior: when a tool refuses task mode, the
        server-side McpError is wrapped as ToolError and converted to a
        CallToolResult(isError=True). _call_tool_as_task returns this as a
        ToolTask with returned_immediately=True. The manager must detect
        this, cache the decision, and retry without task mode."""
        mock_client = AsyncMock()

        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        # Immediate error result mirroring fastmcp's graceful-degradation path
        forbidden_result = MagicMock()
        forbidden_result.is_error = True
        forbidden_result.content = [MagicMock(
            type="text",
            text=(
                "Error calling tool 'evaluate': "
                "FunctionTool 'tool:evaluate@' does not support task-augmented execution"
            ),
        )]

        forbidden_task = MagicMock()
        forbidden_task.returned_immediately = True
        forbidden_task.result = AsyncMock(return_value=forbidden_result)

        # Sync-mode result the fallback should return
        sync_result = MagicMock()
        sync_result.is_error = False
        sync_result.content = [MagicMock(type="text", text="42")]
        sync_result.structured_content = None
        sync_result.data = None

        call_history = []

        async def fake_call_tool(tool_name, arguments, **kwargs):
            call_history.append(kwargs)
            if kwargs.get("task") is True:
                return forbidden_task
            return sync_result

        mock_client.call_tool = AsyncMock(side_effect=fake_call_tool)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)

        manager.clients["srv"] = mock_client
        manager.servers_config["srv"] = {}

        result = await manager.call_tool(
            "srv", "evaluate", {"x": 1},
            conversation_id="conv-1",
            meta={"tool_call_id": "tc-1"},
        )

        assert result is sync_result
        assert call_history[0].get("task") is True
        assert call_history[1].get("task") is not True
        assert ("srv", "evaluate") in manager._tool_task_forbidden


class TestTaskSupportDiscoverySeed:
    """SEP-1686 lets tools advertise execution.taskSupport in tools/list.
    The manager should pre-seed _tool_task_forbidden from that metadata so
    forbidden tools never get a wasted task=True attempt at runtime."""

    @pytest.mark.asyncio
    async def test_discovery_seeds_forbidden_tools_and_skips_task_capable(self, manager):
        # Four tools: forbidden, optional, required, and one with no
        # execution metadata (per spec, absent → "forbidden")
        forbidden_tool = MagicMock()
        forbidden_tool.name = "fast_lookup"
        forbidden_tool.execution = MagicMock(taskSupport="forbidden")

        optional_tool = MagicMock()
        optional_tool.name = "search_index"
        optional_tool.execution = MagicMock(taskSupport="optional")

        required_tool = MagicMock()
        required_tool.name = "build_project"
        required_tool.execution = MagicMock(taskSupport="required")

        unspecified_tool = MagicMock()
        unspecified_tool.name = "legacy_tool"
        unspecified_tool.execution = None

        mock_client = AsyncMock()
        mock_client.list_tools = AsyncMock(
            return_value=[forbidden_tool, optional_tool, required_tool, unspecified_tool]
        )

        manager.servers_config["srv"] = {}
        await manager._discover_tools_for_server("srv", mock_client)

        assert ("srv", "fast_lookup") in manager._tool_task_forbidden
        assert ("srv", "legacy_tool") in manager._tool_task_forbidden
        assert ("srv", "search_index") not in manager._tool_task_forbidden
        assert ("srv", "build_project") not in manager._tool_task_forbidden

    @pytest.mark.asyncio
    async def test_rediscovery_drops_stale_forbidden_entries(self, manager):
        """A server upgrade that flips a tool from forbidden → optional must
        take effect on the next discovery — the stale forbidden entry is
        dropped, not retained until process restart."""
        v1_tool = MagicMock()
        v1_tool.name = "evolving_tool"
        v1_tool.execution = MagicMock(taskSupport="forbidden")

        mock_client = AsyncMock()
        mock_client.list_tools = AsyncMock(return_value=[v1_tool])

        manager.servers_config["srv"] = {}
        await manager._discover_tools_for_server("srv", mock_client)
        assert ("srv", "evolving_tool") in manager._tool_task_forbidden

        # Seed a forbidden entry from a different server — must survive
        manager._tool_task_forbidden.add(("other_srv", "other_tool"))

        v2_tool = MagicMock()
        v2_tool.name = "evolving_tool"
        v2_tool.execution = MagicMock(taskSupport="optional")
        mock_client.list_tools = AsyncMock(return_value=[v2_tool])

        await manager._discover_tools_for_server("srv", mock_client)

        assert ("srv", "evolving_tool") not in manager._tool_task_forbidden
        assert ("other_srv", "other_tool") in manager._tool_task_forbidden

    @pytest.mark.asyncio
    async def test_seeded_forbidden_tool_skips_task_mode_at_call_time(self, manager):
        """End-to-end: discovery marks a tool as forbidden, then call_tool
        must take the sync path on the very first call."""
        forbidden_tool = MagicMock()
        forbidden_tool.name = "markdown_to_pptx"
        forbidden_tool.execution = MagicMock(taskSupport="forbidden")

        mock_client = AsyncMock()
        mock_client.list_tools = AsyncMock(return_value=[forbidden_tool])

        # Server *does* advertise overall task support — only this tool opts out
        caps = MagicMock()
        caps.tasks = MagicMock()
        init_result = MagicMock()
        init_result.capabilities = caps
        mock_client.initialize_result = init_result

        sync_result = MagicMock()
        sync_result.is_error = False
        sync_result.content = [MagicMock(type="text", text="ok")]
        sync_result.structured_content = None
        sync_result.data = None
        mock_client.call_tool = AsyncMock(return_value=sync_result)

        manager.servers_config["srv"] = {}
        await manager._discover_tools_for_server("srv", mock_client)

        mock_session = MagicMock(spec=ManagedSession)
        mock_session.client = mock_client
        manager._session_manager.acquire = AsyncMock(return_value=mock_session)
        manager.clients["srv"] = mock_client

        await manager.call_tool(
            "srv", "markdown_to_pptx", {},
            conversation_id="conv-1",
        )

        # Single call, sync mode — no wasted task=True round-trip
        assert mock_client.call_tool.call_count == 1
        assert mock_client.call_tool.call_args.kwargs.get("task") is not True
