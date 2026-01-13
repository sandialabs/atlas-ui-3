"""
Tests for MCP elicitation routing functionality.

Tests the dictionary-based routing system that allows elicitation requests
from MCP tools to reach the correct WebSocket connection across async tasks.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from domain.messages.models import ToolCall


class TestElicitationRouting:
    """Test elicitation routing context management."""

    @pytest.fixture
    def mock_tool_call(self):
        """Create a mock ToolCall object."""
        return ToolCall(
            id="test_call_123",
            name="elicitation_demo_get_user_name",
            arguments={}
        )

    @pytest.fixture
    def mock_update_callback(self):
        """Create a mock update callback."""
        return AsyncMock()

    @pytest.fixture
    def manager(self):
        """Create a MCPToolManager instance for testing."""
        from modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_elicitation_context_sets_routing(self, manager, mock_tool_call, mock_update_callback):
        """Test that elicitation context correctly sets routing in dictionary."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING

        # Clear routing before test
        _ELICITATION_ROUTING.clear()

        server_name = "test_server"

        # Use the context manager
        async with manager._use_elicitation_context(server_name, mock_tool_call, mock_update_callback):
            # Inside context: routing should exist
            assert server_name in _ELICITATION_ROUTING
            routing = _ELICITATION_ROUTING[server_name]
            assert routing.server_name == server_name
            assert routing.tool_call == mock_tool_call
            assert routing.update_cb == mock_update_callback

        # After context: routing should be cleaned up
        assert server_name not in _ELICITATION_ROUTING

    @pytest.mark.asyncio
    async def test_elicitation_routing_cleanup_on_error(self, manager, mock_tool_call, mock_update_callback):
        """Test that routing is cleaned up even if error occurs."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING

        _ELICITATION_ROUTING.clear()

        server_name = "test_server"

        # Simulate an error inside the context
        with pytest.raises(RuntimeError):
            async with manager._use_elicitation_context(server_name, mock_tool_call, mock_update_callback):
                assert server_name in _ELICITATION_ROUTING
                raise RuntimeError("Simulated error")

        # Routing should still be cleaned up
        assert server_name not in _ELICITATION_ROUTING

    @pytest.mark.asyncio
    async def test_multiple_servers_routing(self, manager, mock_update_callback):
        """Test that multiple servers can have separate routing contexts."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING

        _ELICITATION_ROUTING.clear()

        tool_call_1 = ToolCall(id="call_1", name="tool_1", arguments={})
        tool_call_2 = ToolCall(id="call_2", name="tool_2", arguments={})

        # Create contexts for two different servers
        async with manager._use_elicitation_context("server_1", tool_call_1, mock_update_callback):
            async with manager._use_elicitation_context("server_2", tool_call_2, mock_update_callback):
                # Both should exist simultaneously
                assert "server_1" in _ELICITATION_ROUTING
                assert "server_2" in _ELICITATION_ROUTING
                assert _ELICITATION_ROUTING["server_1"].tool_call == tool_call_1
                assert _ELICITATION_ROUTING["server_2"].tool_call == tool_call_2

            # server_2 cleaned up, server_1 still exists
            assert "server_1" in _ELICITATION_ROUTING
            assert "server_2" not in _ELICITATION_ROUTING

        # Both cleaned up
        assert "server_1" not in _ELICITATION_ROUTING
        assert "server_2" not in _ELICITATION_ROUTING

    @pytest.mark.asyncio
    async def test_elicitation_with_none_callback(self, manager, mock_tool_call):
        """Test elicitation context with None callback (should still work)."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING

        _ELICITATION_ROUTING.clear()

        server_name = "test_server"

        # Use context with None callback
        async with manager._use_elicitation_context(server_name, mock_tool_call, None):
            routing = _ELICITATION_ROUTING[server_name]
            assert routing.update_cb is None

        assert server_name not in _ELICITATION_ROUTING


class TestElicitationHandler:
    """Test per-server elicitation handler creation."""

    @pytest.fixture
    def manager(self):
        """Create a MCPToolManager instance for testing."""
        from modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_handler_creation_captures_server_name(self, manager):
        """Test that handler closure captures the correct server_name."""

        # Create handlers for different servers
        handler_1 = manager._create_elicitation_handler("server_1")
        handler_2 = manager._create_elicitation_handler("server_2")

        # Handlers should be different functions (different closures)
        assert handler_1 != handler_2
        assert callable(handler_1)
        assert callable(handler_2)

    @pytest.mark.asyncio
    async def test_handler_returns_cancel_when_no_routing(self, manager):
        """Test that handler returns cancel when routing not found."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING
        from fastmcp.client.elicitation import ElicitResult

        _ELICITATION_ROUTING.clear()

        handler = manager._create_elicitation_handler("test_server")

        # Call handler with no routing set
        result = await handler("Test message", str, None, None)

        assert isinstance(result, ElicitResult)
        assert result.action == "cancel"
        assert result.content is None

    @pytest.mark.asyncio
    async def test_handler_returns_cancel_when_no_update_cb(self, manager):
        """Test that handler returns cancel when update_cb is None."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING, _ElicitationRoutingContext
        from domain.messages.models import ToolCall
        from fastmcp.client.elicitation import ElicitResult

        _ELICITATION_ROUTING.clear()

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})

        # Set routing with None callback
        _ELICITATION_ROUTING[server_name] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=None
        )

        handler = manager._create_elicitation_handler(server_name)
        result = await handler("Test message", str, None, None)

        assert isinstance(result, ElicitResult)
        assert result.action == "cancel"
        assert result.content is None


class TestElicitationIntegration:
    """Integration tests for elicitation workflow."""

    @pytest.fixture
    def manager(self):
        """Create a MCPToolManager instance for testing."""
        from modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_elicitation_request_sent_to_callback(self, manager):
        """Test that elicitation request is sent to update callback."""
        from modules.mcp_tools.client import _ELICITATION_ROUTING, _ElicitationRoutingContext
        from domain.messages.models import ToolCall

        _ELICITATION_ROUTING.clear()

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})
        mock_callback = AsyncMock()

        # Set routing with mock callback
        _ELICITATION_ROUTING[server_name] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=mock_callback
        )

        handler = manager._create_elicitation_handler(server_name)

        # Mock elicitation manager
        with patch('application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
            mock_elicit_mgr = Mock()
            mock_request = AsyncMock()
            mock_request.wait_for_response = AsyncMock(return_value={
                "action": "accept",
                "data": "test_value"
            })
            mock_elicit_mgr.create_elicitation_request = Mock(return_value=mock_request)
            mock_elicit_mgr.cleanup_request = Mock()
            mock_get_mgr.return_value = mock_elicit_mgr

            result = await handler("What's your name?", str, None, None)

            # Verify callback was called with elicitation_request
            mock_callback.assert_called_once()
            call_args = mock_callback.call_args[0][0]
            assert call_args["type"] == "elicitation_request"
            assert call_args["message"] == "What's your name?"
            assert call_args["tool_call_id"] == "call_123"

            # Verify result
            assert result.action == "accept"
            assert result.content == {"value": "test_value"}

    @pytest.mark.asyncio
    async def test_elicitation_accept_no_data_returns_empty_object(self, manager):
        """Test approval-only elicitation returns empty object on accept.

        FastMCP validation for response_type=None expects an empty response object.
        Some UIs send placeholder payloads like {'none': ''}; we must not forward them.
        """
        from modules.mcp_tools.client import _ELICITATION_ROUTING, _ElicitationRoutingContext
        from domain.messages.models import ToolCall

        _ELICITATION_ROUTING.clear()

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})
        mock_callback = AsyncMock()

        _ELICITATION_ROUTING[server_name] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=mock_callback,
        )

        handler = manager._create_elicitation_handler(server_name)

        with patch('application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
            mock_elicit_mgr = Mock()
            mock_request = AsyncMock()
            mock_request.wait_for_response = AsyncMock(return_value={
                "action": "accept",
                "data": {"none": ""},
            })
            mock_elicit_mgr.create_elicitation_request = Mock(return_value=mock_request)
            mock_elicit_mgr.cleanup_request = Mock()
            mock_get_mgr.return_value = mock_elicit_mgr

            result = await handler(
                "Are you sure you want to delete this item?",
                None,
                None,
                None,
            )

            assert result.action == "accept"
            assert result.content == {}
