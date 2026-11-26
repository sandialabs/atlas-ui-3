"""Tests for background-task handling in MCPToolManager.call_tool.

FastMCP 4 polls a server-side task (SEP-2663) to completion inside
``Client.call_tool``. Atlas adds the slow-task UI announcement and the
``tasks/cancel`` request on cancellation; both are covered here with stand-in
clients. ``test_mcp_elicitation_round_trip.py`` runs the same paths against a
real task server.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.mcp_task_extension import (
    _ACTIVE_TASK_TRACKER,
    AtlasTasksClientExtension,
    TaskCallTracker,
    track_task_call,
)
from atlas.modules.mcp_tools.session_manager import ManagedSession


@pytest.fixture
def manager():
    """A MCPToolManager with a short slow-task threshold."""
    tm = MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")
    tm._task_timeout = 0.05
    return tm


def _result(text="done"):
    result = MagicMock()
    result.content = [MagicMock(type="text", text=text)]
    result.structured_content = None
    result.data = None
    return result


def _client(*, tasked: bool, duration: float = 0.0):
    """A stand-in client whose call_tool optionally behaves like a tasked call."""
    client = AsyncMock()

    async def call_tool(name, arguments, **kwargs):
        if tasked:
            tracker = _ACTIVE_TASK_TRACKER.get()
            assert tracker is not None, "call_tool must run inside track_task_call"
            tracker.task_created("task-1")
        await asyncio.sleep(duration)
        return _result()

    client.call_tool = AsyncMock(side_effect=call_tool)
    return client


def _use(manager, client):
    manager.clients["srv"] = client
    session = ManagedSession(client)
    session._opened = True
    manager._session_manager.acquire = AsyncMock(return_value=session)


class TestSlowTaskAnnouncement:

    @pytest.mark.asyncio
    async def test_fast_task_is_not_announced(self, manager):
        client = _client(tasked=True)
        _use(manager, client)
        update_cb = AsyncMock()

        result = await manager.call_tool(
            "srv", "tool", {}, conversation_id="conv-1", update_cb=update_cb
        )

        assert result.content[0].text == "done"
        update_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_slow_task_is_announced_and_closed(self, manager):
        client = _client(tasked=True, duration=0.3)
        _use(manager, client)
        update_cb = AsyncMock()

        await manager.call_tool(
            "srv",
            "tool",
            {},
            conversation_id="conv-1",
            update_cb=update_cb,
            meta={"tool_call_id": "tc-1"},
        )

        events = [call.args[0] for call in update_cb.call_args_list]
        assert [e["type"] for e in events] == ["tool_task_started", "tool_task_completed"]
        assert events[0] == {
            "type": "tool_task_started",
            "tool_call_id": "tc-1",
            "tool_name": "tool",
            "server_name": "srv",
        }
        assert events[1]["tool_call_id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_slow_synchronous_call_is_not_announced(self, manager):
        """Only a call the server actually tasked is reported as a task."""
        client = _client(tasked=False, duration=0.3)
        _use(manager, client)
        update_cb = AsyncMock()

        await manager.call_tool("srv", "tool", {}, conversation_id="conv-1", update_cb=update_cb)

        update_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_use_session_is_tracked_too(self, manager):
        """Without a conversation the call still runs inside a tracker."""
        client = _client(tasked=True, duration=0.3)
        manager.clients["srv"] = client
        update_cb = AsyncMock()

        await manager.call_tool("srv", "tool", {}, update_cb=update_cb)

        types = [call.args[0]["type"] for call in update_cb.call_args_list]
        assert types == ["tool_task_started", "tool_task_completed"]

    @pytest.mark.asyncio
    async def test_meta_and_progress_handler_reach_the_client(self, manager):
        client = _client(tasked=False)
        _use(manager, client)
        progress = AsyncMock()

        await manager.call_tool(
            "srv", "tool", {"a": 1}, conversation_id="conv-1",
            progress_handler=progress, meta={"tool_call_id": "tc-1"},
        )

        client.call_tool.assert_awaited_once_with(
            "tool", {"a": 1}, progress_handler=progress, meta={"tool_call_id": "tc-1"}
        )

    @pytest.mark.asyncio
    async def test_call_timeout_is_reported(self, manager, monkeypatch):
        from atlas.modules.mcp_tools import client as client_module

        monkeypatch.setattr(
            client_module.config_manager.app_settings, "mcp_call_timeout", 0.05
        )
        client = _client(tasked=True, duration=5)
        _use(manager, client)
        update_cb = AsyncMock()

        with pytest.raises(TimeoutError, match="timed out"):
            await manager.call_tool("srv", "tool", {}, conversation_id="conv-1", update_cb=update_cb)

    @pytest.mark.asyncio
    async def test_announcement_fires_before_a_shorter_call_timeout(self, manager, monkeypatch):
        """MCP_CALL_TIMEOUT <= MCP_TASK_TIMEOUT must not silence the announcement."""
        from atlas.modules.mcp_tools import client as client_module

        monkeypatch.setattr(
            client_module.config_manager.app_settings, "mcp_call_timeout", 0.4
        )
        manager._task_timeout = 10  # longer than the whole call is allowed to take
        client = _client(tasked=True, duration=5)
        _use(manager, client)
        update_cb = AsyncMock()

        with pytest.raises(TimeoutError, match="timed out"):
            await manager.call_tool(
                "srv", "tool", {}, conversation_id="conv-1",
                meta={"tool_call_id": "call_1"}, update_cb=update_cb,
            )

        types = [c.args[0]["type"] for c in update_cb.call_args_list]
        assert types == ["tool_task_started", "tool_task_completed"]

    @pytest.mark.asyncio
    async def test_cancellation_propagates_and_closes_the_announcement(self, manager):
        client = _client(tasked=True, duration=5)
        _use(manager, client)
        update_cb = AsyncMock()

        call = asyncio.create_task(
            manager.call_tool("srv", "tool", {}, conversation_id="conv-1", update_cb=update_cb)
        )
        async with asyncio.timeout(5):
            while not update_cb.call_count:  # wait for tool_task_started
                await asyncio.sleep(0.01)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        types = [c.args[0]["type"] for c in update_cb.call_args_list]
        assert types == ["tool_task_started", "tool_task_completed"]


class TestTasksExtension:
    """AtlasTasksClientExtension wraps FastMCP's resolver; it never replaces it."""

    @staticmethod
    def _resolver(inner):
        return AtlasTasksClientExtension._tracking(inner)

    @staticmethod
    def _ctx():
        session = SimpleNamespace(send_request=AsyncMock())
        return SimpleNamespace(session=session)

    def test_takes_the_place_of_the_stock_extension(self):
        from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
        from fastmcp_tasks.client import TasksClientExtension

        ours = AtlasTasksClientExtension()
        stock = TasksClientExtension()
        assert ours.identifier == stock.identifier == TASKS_EXTENSION_ID
        assert ours.settings() == stock.settings()
        (ours_claim,), (stock_claim,) = ours.claims(), stock.claims()
        assert ours_claim.result_type == stock_claim.result_type == "task"
        assert ours_claim.model is stock_claim.model
        assert ours_claim.protocol_versions == stock_claim.protocol_versions

    def test_client_uses_one_tasks_extension(self):
        """FastMCP drops its built-in extension when ours declares the same id."""
        from fastmcp import Client, FastMCP

        client = Client(FastMCP("x"), extensions=[AtlasTasksClientExtension()])
        task_claims = [
            claim for claim in client._claim_by_model.values() if claim.result_type == "task"
        ]
        assert len(task_claims) == 1
        assert task_claims[0].resolve.__name__ == "resolve_tracked"

    @pytest.mark.asyncio
    async def test_completed_task_is_tracked_and_not_cancelled(self):
        inner = AsyncMock(return_value="result")
        ctx = self._ctx()
        tracker = TaskCallTracker()

        with track_task_call(tracker):
            out = await self._resolver(inner)(SimpleNamespace(task_id="t-1"), ctx)

        assert out == "result"
        assert tracker.task_id == "t-1" and tracker.created.is_set()
        ctx.session.send_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolves_without_a_tracker(self):
        inner = AsyncMock(return_value="result")
        assert await self._resolver(inner)(SimpleNamespace(task_id="t-1"), self._ctx()) == "result"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [asyncio.CancelledError, TimeoutError])
    async def test_cancel_or_timeout_cancels_the_server_task(self, error):
        inner = AsyncMock(side_effect=error())
        ctx = self._ctx()

        with pytest.raises(error):
            await self._resolver(inner)(SimpleNamespace(task_id="t-9"), ctx)

        (request, _result_type), _ = ctx.session.send_request.call_args
        assert request.method == "tasks/cancel"
        assert request.params.task_id == "t-9"

    @pytest.mark.asyncio
    async def test_tool_error_does_not_cancel(self):
        inner = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = self._ctx()

        with pytest.raises(RuntimeError):
            await self._resolver(inner)(SimpleNamespace(task_id="t-9"), ctx)

        ctx.session.send_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_cancel_request_keeps_the_original_cancellation(self):
        inner = AsyncMock(side_effect=asyncio.CancelledError())
        ctx = self._ctx()
        ctx.session.send_request.side_effect = RuntimeError("connection closed")

        with pytest.raises(asyncio.CancelledError):
            await self._resolver(inner)(SimpleNamespace(task_id="t-9"), ctx)
