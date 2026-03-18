"""
Tests for MCP elicitation routing functionality.

Tests the dictionary-based routing system that allows elicitation requests
from MCP tools to reach the correct WebSocket connection across async tasks.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from atlas.domain.messages.models import ToolCall


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
        from atlas.modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_elicitation_context_sets_routing(self, manager, mock_tool_call, mock_update_callback):
        """Test that elicitation context correctly sets routing in dictionary."""
        server_name = "test_server"
        routing_key = (server_name, mock_tool_call.id)

        # Use the context manager
        async with manager._use_elicitation_context(server_name, mock_tool_call, mock_update_callback):
            # Inside context: routing should exist with composite key
            assert routing_key in manager._elicitation_routing
            routing = manager._elicitation_routing[routing_key]
            assert routing.server_name == server_name
            assert routing.tool_call == mock_tool_call
            assert routing.update_cb == mock_update_callback

        # After context: routing should be cleaned up
        assert routing_key not in manager._elicitation_routing

    @pytest.mark.asyncio
    async def test_elicitation_routing_cleanup_on_error(self, manager, mock_tool_call, mock_update_callback):
        """Test that routing is cleaned up even if error occurs."""
        server_name = "test_server"
        routing_key = (server_name, mock_tool_call.id)

        # Simulate an error inside the context
        with pytest.raises(RuntimeError):
            async with manager._use_elicitation_context(server_name, mock_tool_call, mock_update_callback):
                assert routing_key in manager._elicitation_routing
                raise RuntimeError("Simulated error")

        # Routing should still be cleaned up
        assert routing_key not in manager._elicitation_routing

    @pytest.mark.asyncio
    async def test_multiple_servers_routing(self, manager, mock_update_callback):
        """Test that multiple servers can have separate routing contexts."""
        tool_call_1 = ToolCall(id="call_1", name="tool_1", arguments={})
        tool_call_2 = ToolCall(id="call_2", name="tool_2", arguments={})
        routing_key_1 = ("server_1", "call_1")
        routing_key_2 = ("server_2", "call_2")

        # Create contexts for two different servers
        async with manager._use_elicitation_context("server_1", tool_call_1, mock_update_callback):
            async with manager._use_elicitation_context("server_2", tool_call_2, mock_update_callback):
                # Both should exist simultaneously
                assert routing_key_1 in manager._elicitation_routing
                assert routing_key_2 in manager._elicitation_routing
                assert manager._elicitation_routing[routing_key_1].tool_call == tool_call_1
                assert manager._elicitation_routing[routing_key_2].tool_call == tool_call_2

            # server_2 cleaned up, server_1 still exists
            assert routing_key_1 in manager._elicitation_routing
            assert routing_key_2 not in manager._elicitation_routing

        # Both cleaned up
        assert routing_key_1 not in manager._elicitation_routing
        assert routing_key_2 not in manager._elicitation_routing

    @pytest.mark.asyncio
    async def test_elicitation_with_none_callback(self, manager, mock_tool_call):
        """Test elicitation context with None callback (should still work)."""
        server_name = "test_server"
        routing_key = (server_name, mock_tool_call.id)

        # Use context with None callback
        async with manager._use_elicitation_context(server_name, mock_tool_call, None):
            routing = manager._elicitation_routing[routing_key]
            assert routing.update_cb is None

        assert routing_key not in manager._elicitation_routing

    @pytest.mark.asyncio
    async def test_concurrent_same_server_routing(self, manager):
        """Two concurrent tool calls to the same server route correctly."""
        tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
        tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
            async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
                assert ("server-x", "call_1") in manager._elicitation_routing
                assert ("server-x", "call_2") in manager._elicitation_routing
                assert manager._elicitation_routing[("server-x", "call_1")].update_cb is cb1
                assert manager._elicitation_routing[("server-x", "call_2")].update_cb is cb2


class TestElicitationHandler:
    """Test per-server elicitation handler creation."""

    @pytest.fixture
    def manager(self):
        """Create a MCPToolManager instance for testing."""
        from atlas.modules.mcp_tools.client import MCPToolManager
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
        from fastmcp.client.elicitation import ElicitResult

        handler = manager._create_elicitation_handler("test_server")

        # Call handler with no routing set
        result = await handler("Test message", str, None, None)

        assert isinstance(result, ElicitResult)
        assert result.action == "cancel"
        assert result.content is None

    @pytest.mark.asyncio
    async def test_handler_returns_cancel_when_no_update_cb(self, manager):
        """Test that handler returns cancel when update_cb is None."""
        from fastmcp.client.elicitation import ElicitResult

        from atlas.domain.messages.models import ToolCall
        from atlas.modules.mcp_tools.client import _ElicitationRoutingContext

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})
        routing_key = (server_name, tool_call.id)

        # Set routing with None callback using composite key
        manager._elicitation_routing[routing_key] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=None
        )

        handler = manager._create_elicitation_handler(server_name)
        result = await handler("Test message", str, None, None)

        assert isinstance(result, ElicitResult)
        assert result.action == "cancel"
        assert result.content is None

    @pytest.mark.asyncio
    async def test_handler_routes_via_meta_tool_call_id(self, manager):
        """Handler uses _context.meta.model_extra to find correct routing."""
        tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
        tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        handler = manager._create_elicitation_handler("server-x")

        async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
            async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
                mock_context = MagicMock()
                mock_meta = MagicMock()
                mock_meta.model_extra = {"tool_call_id": "call_2"}
                mock_context.meta = mock_meta

                with patch('atlas.application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
                    mock_elicit_mgr = Mock()
                    mock_request = AsyncMock()
                    mock_request.wait_for_response = AsyncMock(return_value={
                        "action": "accept",
                        "data": "test_value"
                    })
                    mock_elicit_mgr.create_elicitation_request = Mock(return_value=mock_request)
                    mock_elicit_mgr.cleanup_request = Mock()
                    mock_get_mgr.return_value = mock_elicit_mgr

                    # Handler should route to cb2, not cb1
                    _result = await handler("Pick a color", str, None, mock_context)
                    # cb2 should have been called (routed correctly), cb1 should not
                    cb2.assert_called_once()
                    cb1.assert_not_called()
                    assert manager._elicitation_routing[("server-x", "call_2")].update_cb is cb2

    @pytest.mark.asyncio
    async def test_handler_fallback_single_match_without_meta(self, manager):
        """When meta is unavailable but only one routing entry exists, use it."""
        tool_call = ToolCall(id="call_1", name="tool_a", arguments={})
        cb = AsyncMock()

        handler = manager._create_elicitation_handler("server-x")

        async with manager._use_elicitation_context("server-x", tool_call, cb):
            mock_context = MagicMock()
            mock_context.meta = None

            with patch('atlas.application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
                mock_elicit_mgr = Mock()
                mock_request = AsyncMock()
                mock_request.wait_for_response = AsyncMock(return_value={
                    "action": "accept",
                    "data": "test_value"
                })
                mock_elicit_mgr.create_elicitation_request = Mock(return_value=mock_request)
                mock_elicit_mgr.cleanup_request = Mock()
                mock_get_mgr.return_value = mock_elicit_mgr

                _result = await handler("Pick a color", str, None, mock_context)
                cb.assert_called_once()
                assert manager._elicitation_routing[("server-x", "call_1")].update_cb is cb

    @pytest.mark.asyncio
    async def test_handler_cancels_on_ambiguous_routing_without_meta(self, manager):
        """When meta unavailable and multiple entries exist, cancel."""
        tool_call_1 = ToolCall(id="call_1", name="tool_a", arguments={})
        tool_call_2 = ToolCall(id="call_2", name="tool_b", arguments={})
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        handler = manager._create_elicitation_handler("server-x")

        async with manager._use_elicitation_context("server-x", tool_call_1, cb1):
            async with manager._use_elicitation_context("server-x", tool_call_2, cb2):
                mock_context = MagicMock()
                mock_context.meta = None
                result = await handler("Pick a color", str, None, mock_context)
                assert result.action == "cancel"


class TestElicitationIntegration:
    """Integration tests for elicitation workflow."""

    @pytest.fixture
    def manager(self):
        """Create a MCPToolManager instance for testing."""
        from atlas.modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_elicitation_request_sent_to_callback(self, manager):
        """Test that elicitation request is sent to update callback."""
        from atlas.domain.messages.models import ToolCall
        from atlas.modules.mcp_tools.client import _ElicitationRoutingContext

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})
        mock_callback = AsyncMock()
        routing_key = (server_name, tool_call.id)

        # Set routing with mock callback using composite key
        manager._elicitation_routing[routing_key] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=mock_callback
        )

        handler = manager._create_elicitation_handler(server_name)

        # Mock elicitation manager
        with patch('atlas.application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
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
        from atlas.domain.messages.models import ToolCall
        from atlas.modules.mcp_tools.client import _ElicitationRoutingContext

        server_name = "test_server"
        tool_call = ToolCall(id="call_123", name="test_tool", arguments={})
        mock_callback = AsyncMock()
        routing_key = (server_name, tool_call.id)

        manager._elicitation_routing[routing_key] = _ElicitationRoutingContext(
            server_name=server_name,
            tool_call=tool_call,
            update_cb=mock_callback,
        )

        handler = manager._create_elicitation_handler(server_name)

        with patch('atlas.application.chat.elicitation_manager.get_elicitation_manager') as mock_get_mgr:
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
