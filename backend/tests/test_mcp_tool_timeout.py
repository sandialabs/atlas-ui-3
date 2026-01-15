"""Tests for MCP tool timeout functionality.

Tests that verify the timeout mechanism works correctly when MCP tools
take longer than the configured timeout duration to execute.
"""

import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import patch, AsyncMock, MagicMock

from backend.modules.mcp_tools.client import MCPToolManager
from backend.modules.config import config_manager
from domain.messages.models import ToolCall


@asynccontextmanager
async def async_noop_context():
    """A no-op async context manager for testing."""
    yield


class TestMCPToolTimeout:
    """Tests for MCP tool timeout behavior."""

    @pytest.fixture
    def mock_tool_manager(self):
        """Create a mock tool manager with a simple tool."""
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.clients = {"test_server": AsyncMock()}
        manager.servers_config = {
            "test_server": {"enabled": True}
        }
        manager.available_tools = {
            "test_server": {
                "tools": [
                    MagicMock(
                        name="slow_tool",
                        description="A tool that takes a long time to execute"
                    )
                ]
            }
        }
        # Build the tool index
        manager._tool_index = {
            "test_server_slow_tool": {
                "server": "test_server",
                "tool": MagicMock(name="slow_tool")
            }
        }
        
        # Mock the context managers
        manager._use_log_callback = lambda cb: async_noop_context()
        manager._use_elicitation_context = lambda sn, tc, uc: async_noop_context()
        
        return manager

    @pytest.mark.asyncio
    async def test_tool_timeout_triggers_error(self, mock_tool_manager):
        """Test that a slow tool triggers a timeout error."""
        # Create a tool call
        tool_call = ToolCall(
            id="test_call_1",
            name="test_server_slow_tool",
            arguments={"param": "value"}
        )

        # Mock a slow call_tool that takes 6 seconds (longer than our test timeout)
        async def slow_call_tool(*args, **kwargs):
            await asyncio.sleep(6)
            return {"results": "This should timeout"}

        # Use patch to ensure the timeout setting takes effect
        # Patch it in the client module where it's actually used
        with patch('backend.modules.mcp_tools.client.config_manager.app_settings.mcp_tool_timeout_seconds', 2):
            # Patch call_tool on the manager instance itself
            with patch.object(mock_tool_manager, 'call_tool', side_effect=slow_call_tool):
                result = await mock_tool_manager.execute_tool(tool_call)

        # Verify timeout error was returned
        assert result.success is False
        assert "timed out" in result.content.lower()
        assert "2 seconds" in result.content
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_tool_completes_within_timeout(self, mock_tool_manager):
        """Test that a fast tool completes successfully before timeout."""
        # Create a tool call
        tool_call = ToolCall(
            id="test_call_2",
            name="test_server_slow_tool",
            arguments={"param": "value"}
        )

        # Mock a fast call_tool that completes quickly
        async def fast_call_tool(*args, **kwargs):
            await asyncio.sleep(0.1)
            return MagicMock(
                structured_content={"results": "Success"},
                content=[],
                data=None
            )

        # Set a reasonable timeout (5 seconds)
        with patch('backend.modules.mcp_tools.client.config_manager.app_settings.mcp_tool_timeout_seconds', 5):
            with patch.object(mock_tool_manager, 'call_tool', side_effect=fast_call_tool):
                result = await mock_tool_manager.execute_tool(tool_call)

        # Verify tool completed successfully
        assert result.success is True
        assert "timed out" not in result.content.lower()

    @pytest.mark.asyncio
    async def test_timeout_disabled_allows_long_execution(self, mock_tool_manager):
        """Test that setting timeout to 0 disables timeout enforcement."""
        # Create a tool call
        tool_call = ToolCall(
            id="test_call_3",
            name="test_server_slow_tool",
            arguments={"param": "value"}
        )

        # Mock a moderately slow call_tool (3 seconds)
        async def moderate_call_tool(*args, **kwargs):
            await asyncio.sleep(3)
            return MagicMock(
                structured_content={"results": "Completed without timeout"},
                content=[],
                data=None
            )

        # Disable timeout by setting it to 0
        with patch('backend.modules.mcp_tools.client.config_manager.app_settings.mcp_tool_timeout_seconds', 0):
            with patch.object(mock_tool_manager, 'call_tool', side_effect=moderate_call_tool):
                result = await mock_tool_manager.execute_tool(tool_call)

        # Verify tool completed successfully even though it took 3 seconds
        assert result.success is True
        assert "Completed without timeout" in result.content

    @pytest.mark.asyncio
    async def test_timeout_error_message_includes_config_hint(self, mock_tool_manager):
        """Test that timeout error message includes configuration hint."""
        tool_call = ToolCall(
            id="test_call_4",
            name="test_server_slow_tool",
            arguments={}
        )

        async def slow_call_tool(*args, **kwargs):
            await asyncio.sleep(10)
            return {"results": "Should timeout"}

        with patch('backend.modules.mcp_tools.client.config_manager.app_settings.mcp_tool_timeout_seconds', 1):
            with patch.object(mock_tool_manager, 'call_tool', side_effect=slow_call_tool):
                result = await mock_tool_manager.execute_tool(tool_call)

        # Verify error message includes helpful configuration hint
        assert "MCP_TOOL_TIMEOUT_SECONDS" in result.content
        assert result.success is False
