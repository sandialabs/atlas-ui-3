"""Tests for the built-in atlas_agent_sleep pseudo-tool (issue #779)."""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.policies.tool_authorization import ToolAuthorizationService
from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools import client as mcp_client
from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.sleep_tool import (
    SLEEP_TOOL_NAME,
    TURN_BUDGET_KEY,
    execute_sleep_tool,
)


def _manager() -> MCPToolManager:
    return MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")


def _call(**arguments) -> ToolCall:
    return ToolCall(id="sleep-1", name=SLEEP_TOOL_NAME, arguments=arguments)


def _patch_settings(monkeypatch, max_seconds):
    """Override just the cap on the config_manager the execution path reads.

    Patched after the manager is constructed: ``MCPToolManager.__init__`` reads
    other settings off the same object, so it cannot be a bare stub.
    """
    monkeypatch.setattr(
        mcp_client.config_manager.app_settings,
        "agent_sleep_max_seconds",
        max_seconds,
        raising=False,
    )


def test_get_tools_schema_includes_sleep_tool():
    manager = _manager()
    schemas = manager.get_tools_schema([SLEEP_TOOL_NAME])

    # The legacy name still resolves; it maps onto the consolidated tool (#855).
    assert [s["function"]["name"] for s in schemas] == ["atlas_sleep"]
    assert "seconds" in schemas[0]["function"]["parameters"]["properties"]
    assert manager.get_server_for_tool(SLEEP_TOOL_NAME) == "atlas"
    assert manager.get_server_for_tool("atlas_sleep") == "atlas"


@pytest.mark.asyncio
async def test_sleep_waits_for_requested_duration(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)

    result = await execute_sleep_tool(_call(seconds=5, reason="waiting on job"), max_seconds=7200)

    assert result.success is True
    assert slept == [5.0]
    assert "Slept for 5 seconds" in result.content
    assert "waiting on job" in result.content


@pytest.mark.asyncio
async def test_sleep_clamps_to_maximum(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)

    result = await execute_sleep_tool(_call(seconds=10000), max_seconds=7200)

    assert result.success is True
    assert slept == [7200.0]
    assert "exceeded" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -3, "abc", None, True, float("nan")])
async def test_sleep_rejects_invalid_durations(seconds):
    result = await execute_sleep_tool(_call(seconds=seconds), max_seconds=7200)

    assert result.success is False
    assert "greater than 0" in result.content


@pytest.mark.asyncio
async def test_sleep_aborts_when_the_run_is_cancelled():
    """A stopped run cancels the turn's task; the sleep must not swallow it."""
    task = asyncio.ensure_future(execute_sleep_tool(_call(seconds=30), max_seconds=7200))
    await asyncio.sleep(0)  # let the sleep start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_execute_tool_routes_to_sleep_without_an_mcp_server(monkeypatch):
    manager = _manager()
    _patch_settings(monkeypatch, 7200)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)

    result = await manager.execute_tool(_call(seconds=2), context={"user_email": "u@example.com"})

    assert result.success is True
    assert slept == [2.0]


@pytest.mark.asyncio
async def test_execute_tool_refuses_when_disabled(monkeypatch):
    manager = _manager()
    _patch_settings(monkeypatch, 0)

    result = await manager.execute_tool(_call(seconds=2), context={"user_email": "u@example.com"})

    assert result.success is False
    assert "disabled" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_seconds,expected",
    [(7200, [SLEEP_TOOL_NAME]), (0, [])],
)
async def test_tool_authorization_gates_sleep_on_the_cap(max_seconds, expected):
    class FakeToolManager:
        async def get_authorized_servers(self, user, auth_check_func):
            return []

    service = ToolAuthorizationService(
        tool_manager=FakeToolManager(),
        config_manager=SimpleNamespace(
            app_settings=SimpleNamespace(agent_sleep_max_seconds=max_seconds)
        ),
    )

    assert await service.filter_authorized_tools([SLEEP_TOOL_NAME], "u@example.com") == expected


@pytest.mark.asyncio
async def test_turn_budget_is_spent_across_calls_then_refuses(monkeypatch):
    """The per-call cap bounds nothing on its own -- the model may call again."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)
    context = {TURN_BUDGET_KEY: {}}

    first = await execute_sleep_tool(
        _call(seconds=60), max_seconds=60, context=context, max_turn_seconds=100
    )
    second = await execute_sleep_tool(
        _call(seconds=60), max_seconds=60, context=context, max_turn_seconds=100
    )
    third = await execute_sleep_tool(
        _call(seconds=60), max_seconds=60, context=context, max_turn_seconds=100
    )

    assert first.success is True
    # The second call is clamped to what the turn has left, not to the per-call cap.
    assert second.success is True
    assert slept == [60.0, 40.0]
    # Once the budget is gone the tool refuses, and must not invite another call.
    assert third.success is False
    assert "Do not call" in third.content
    assert "call this tool again" not in third.content


@pytest.mark.asyncio
async def test_clamp_message_stops_inviting_another_call_at_the_budget_edge(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)

    result = await execute_sleep_tool(
        _call(seconds=500),
        max_seconds=300,
        context={TURN_BUDGET_KEY: {}},
        max_turn_seconds=300,
    )

    assert result.success is True
    assert "call this tool again" not in result.content
    assert "Do not call" in result.content


@pytest.mark.asyncio
async def test_separate_turns_get_separate_budgets(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("atlas.modules.mcp_tools.sleep_tool.asyncio.sleep", fake_sleep)

    for _ in range(3):
        # A fresh scratchpad is what the loop builds per turn.
        result = await execute_sleep_tool(
            _call(seconds=100), max_seconds=100, context={TURN_BUDGET_KEY: {}}, max_turn_seconds=100
        )
        assert result.success is True


def test_disabled_tool_is_not_advertised_to_the_model(monkeypatch):
    """Agent mode never ACL-filters, so the schema is the gate that matters."""
    manager = _manager()
    _patch_settings(monkeypatch, 0)

    assert manager.get_tools_schema([SLEEP_TOOL_NAME]) == []


def test_enabled_tool_is_advertised(monkeypatch):
    manager = _manager()
    _patch_settings(monkeypatch, 7200)

    assert [s["function"]["name"] for s in manager.get_tools_schema([SLEEP_TOOL_NAME])] == [
        "atlas_sleep"
    ]
    assert manager.get_server_for_tool(SLEEP_TOOL_NAME) == "atlas"


def test_client_supplied_step_count_is_clamped_to_the_configured_maximum():
    """A client can ask for any step count; the server decides the ceiling."""
    from atlas.application.chat.orchestrator import ChatOrchestrator

    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(agent_max_steps=30)
    )

    assert orchestrator._bounded_agent_steps(5) == 5
    assert orchestrator._bounded_agent_steps(30) == 30
    assert orchestrator._bounded_agent_steps(10_000) == 30
    assert orchestrator._bounded_agent_steps(0) == 1
    assert orchestrator._bounded_agent_steps(-4) == 1
    assert orchestrator._bounded_agent_steps("nonsense") == 10
    assert orchestrator._bounded_agent_steps(None) == 10


def test_step_clamp_falls_back_when_no_config_manager():
    from atlas.application.chat.orchestrator import ChatOrchestrator

    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = None

    assert orchestrator._bounded_agent_steps(10_000) == 10
    assert orchestrator._bounded_agent_steps(3) == 3
