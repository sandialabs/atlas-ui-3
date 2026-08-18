"""Tests for the built-in atlas_agent_sleep pseudo-tool (issue #779)."""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.policies.tool_authorization import ToolAuthorizationService
from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools import client as mcp_client
from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.sleep_tool import SLEEP_TOOL_NAME, execute_sleep_tool


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

    assert [s["function"]["name"] for s in schemas] == [SLEEP_TOOL_NAME]
    assert "seconds" in schemas[0]["function"]["parameters"]["properties"]
    assert manager.get_server_for_tool(SLEEP_TOOL_NAME) == "atlas_agent"


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
    assert "exceeded the maximum" in result.content


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
