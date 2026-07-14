"""Tests for MCP tool execution-time group authorization.

The request-time filter in ``ToolAuthorizationService`` is the first line of
defense, but agent mode bypassed it and the execution path had no choke point.
These tests verify that ``MCPToolManager.execute_tool`` enforces group ACLs at
the single execution choke point keyed on ``context["user_email"]``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager


def _manager(server_config: dict) -> MCPToolManager:
    """Build a bare MCPToolManager instance for execution-level unit tests."""
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = server_config
    manager._elicitation_routing = {}
    manager._sampling_routing = {}
    manager._server_task_support = {}
    manager._tool_task_forbidden = set()
    return manager


def _tool_index(server_name: str, tool_name: str) -> dict:
    class MockTool:
        def __init__(self, name):
            self.name = name

    return {
        f"{server_name}_{tool_name}": {
            "server": server_name,
            "tool": MockTool(tool_name),
        }
    }


@pytest.mark.asyncio
async def test_execute_tool_allows_unrestricted_server():
    """Tools on servers with no group restriction execute normally."""
    manager = _manager({"public_server": {"enabled": True, "groups": []}})
    manager._tool_index = _tool_index("public_server", "get_time")

    with patch.object(manager, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            structured_content=None,
            data=None,
            is_error=False,
        )
        result = await manager.execute_tool(
            ToolCall(id="call-1", name="public_server_get_time", arguments={}),
            context={"user_email": "user@example.com"},
        )

    assert result.success is True
    mock_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_tool_denies_restricted_server_for_unauthorized_user():
    """Agent mode must not bypass group ACLs for restricted servers."""
    manager = _manager({"admin_server": {"enabled": True, "groups": ["admin"]}})
    manager._tool_index = _tool_index("admin_server", "secret")

    async def not_in_group(user_email: str, group: str) -> bool:
        return False

    with patch("atlas.core.auth.is_user_in_group", not_in_group):
        result = await manager.execute_tool(
            ToolCall(id="call-2", name="admin_server_secret", arguments={}),
            context={"user_email": "user@example.com"},
        )

    assert result.success is False
    assert "not authorized" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_tool_allows_restricted_server_for_authorized_user():
    """Users who are members of the required group can still execute tools."""
    manager = _manager({"admin_server": {"enabled": True, "groups": ["admin"]}})
    manager._tool_index = _tool_index("admin_server", "secret")

    async def is_admin(user_email: str, group: str) -> bool:
        return group == "admin"

    with patch.object(manager, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            structured_content=None,
            data=None,
            is_error=False,
        )
        with patch("atlas.core.auth.is_user_in_group", is_admin):
            result = await manager.execute_tool(
                ToolCall(id="call-3", name="admin_server_secret", arguments={}),
                context={"user_email": "admin@example.com"},
            )

    assert result.success is True
    mock_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_tool_fails_closed_without_user_context():
    """A group-restricted server must deny execution when user_email is absent."""
    manager = _manager({"admin_server": {"enabled": True, "groups": ["admin"]}})
    manager._tool_index = _tool_index("admin_server", "secret")

    result = await manager.execute_tool(
        ToolCall(id="call-4", name="admin_server_secret", arguments={}),
        context={},
    )

    assert result.success is False
    assert "not authorized" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_tool_fails_closed_when_group_check_raises():
    """Any exception during authorization must deny the tool, not leak access."""
    manager = _manager({"admin_server": {"enabled": True, "groups": ["admin"]}})
    manager._tool_index = _tool_index("admin_server", "secret")

    async def boom(*args, **kwargs) -> bool:
        raise RuntimeError("auth endpoint down")

    with patch("atlas.core.auth.is_user_in_group", side_effect=boom):
        result = await manager.execute_tool(
            ToolCall(id="call-5", name="admin_server_secret", arguments={}),
            context={"user_email": "admin@example.com"},
        )

    assert result.success is False
    assert "not authorized" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_tool_denies_disabled_server():
    """Disabled servers must be treated as unauthorized at execution time."""
    manager = _manager({"broken_server": {"enabled": False, "groups": []}})
    manager._tool_index = _tool_index("broken_server", "bad")

    result = await manager.execute_tool(
        ToolCall(id="call-6", name="broken_server_bad", arguments={}),
        context={"user_email": "user@example.com"},
    )

    assert result.success is False
    assert "not authorized" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_tool_allows_allowed_tool_without_context_user_for_open_server():
    """Backwards compatibility: unrestricted servers work without user context.

    This covers unit tests and internal callers that invoke execute_tool
    outside of a real request context for servers with no group restrictions.
    """
    manager = _manager({"open_server": {"enabled": True, "groups": []}})
    manager._tool_index = _tool_index("open_server", "ping")

    with patch.object(manager, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="pong")],
            structured_content=None,
            data=None,
            is_error=False,
        )
        result = await manager.execute_tool(
            ToolCall(id="call-7", name="open_server_ping", arguments={}),
            context={},
        )

    assert result.success is True
    mock_call.assert_awaited_once()
