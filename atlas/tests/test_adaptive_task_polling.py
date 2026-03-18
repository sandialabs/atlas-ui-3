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
    mock_task.result = mock_result
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
        mock_task.result = mock_result
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
