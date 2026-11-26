"""
Tests for MCP elicitation routing functionality.

Tests the dictionary-based routing system that allows elicitation requests
from MCP tools to reach the correct WebSocket connection across async tasks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from atlas.domain.messages.models import ToolCall


def _types(callback):
    """The event types an update callback received, in order."""
    return [call.args[0]["type"] for call in callback.call_args_list]


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
                    assert _types(cb2) == ["elicitation_request", "elicitation_settled"]
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
                assert _types(cb) == ["elicitation_request", "elicitation_settled"]
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
            assert _types(mock_callback) == ["elicitation_request", "elicitation_settled"]
            call_args = mock_callback.call_args_list[0][0][0]
            settled = mock_callback.call_args_list[1][0][0]
            assert settled["elicitation_id"] == call_args["elicitation_id"]
            assert settled["tool_call_id"] == "call_123"
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


def _session_context(protocol_version, meta=None):
    """A stand-in for the request context FastMCP hands the handler."""
    context = MagicMock()
    context.session.protocol_version = protocol_version
    context.meta = meta
    return context


class TestRoundTripRouting:
    """Routing on MCP 2026-07-28 sessions, where the handler runs in the calling task."""

    MODERN = "2026-07-28"
    LEGACY = "2025-11-25"

    @pytest.fixture
    def manager(self):
        from atlas.modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @pytest.mark.asyncio
    async def test_modern_session_routes_to_the_calling_tool_call(self, manager):
        tc1 = ToolCall(id="call_1", name="tool_a", arguments={})
        tc2 = ToolCall(id="call_2", name="tool_b", arguments={})

        async with manager._use_elicitation_context("srv", tc1, AsyncMock(), "a@example.com"):
            async with manager._use_elicitation_context("srv", tc2, AsyncMock(), "a@example.com"):
                routing = manager._resolve_routing(
                    manager._elicitation_routing, "srv", _session_context(self.MODERN)
                )
                assert routing.tool_call.id == "call_2"
            # Leaving the inner call restores the outer one.
            routing = manager._resolve_routing(
                manager._elicitation_routing, "srv", _session_context(self.MODERN)
            )
            assert routing.tool_call.id == "call_1"

    @pytest.mark.asyncio
    async def test_modern_session_ignores_a_tool_call_id_from_the_server(self, manager):
        """A server cannot point a prompt at another user's call through request meta."""
        victim = ToolCall(id="call_victim", name="tool_a", arguments={})
        caller = ToolCall(id="call_caller", name="tool_a", arguments={})
        manager._elicitation_routing[("srv", victim.id)] = MagicMock(
            server_name="srv", tool_call=victim, user_email="victim@example.com"
        )

        async with manager._use_elicitation_context("srv", caller, AsyncMock(), "caller@example.com"):
            routing = manager._resolve_routing(
                manager._elicitation_routing,
                "srv",
                _session_context(self.MODERN, meta={"tool_call_id": "call_victim"}),
            )

        assert routing.tool_call.id == "call_caller"
        assert routing.user_email == "caller@example.com"

    @pytest.mark.asyncio
    async def test_modern_session_without_a_calling_entry_is_refused(self, manager):
        """No single-match guess on a modern session: an unbound call prompts nobody."""
        other = ToolCall(id="call_other", name="tool_a", arguments={})
        manager._elicitation_routing[("srv", other.id)] = MagicMock(
            server_name="srv", tool_call=other
        )

        routing = manager._resolve_routing(
            manager._elicitation_routing, "srv", _session_context(self.MODERN)
        )

        assert routing is None

    @pytest.mark.asyncio
    async def test_modern_session_refuses_an_entry_for_another_server(self, manager):
        tc = ToolCall(id="call_1", name="tool_a", arguments={})
        async with manager._use_elicitation_context("srv_a", tc, AsyncMock(), "a@example.com"):
            routing = manager._resolve_routing(
                manager._elicitation_routing, "srv_b", _session_context(self.MODERN)
            )
        assert routing is None

    @pytest.mark.asyncio
    async def test_modern_session_refuses_an_entry_that_was_already_released(self, manager):
        """A task that outlives its call (and still carries the contextvar) cannot prompt."""
        tc = ToolCall(id="call_1", name="tool_a", arguments={})
        resolved = []

        async def late(release: asyncio.Event) -> None:
            await release.wait()
            resolved.append(
                manager._resolve_routing(
                    manager._elicitation_routing, "srv", _session_context(self.MODERN)
                )
            )

        release = asyncio.Event()
        async with manager._use_elicitation_context("srv", tc, AsyncMock(), "a@example.com"):
            straggler = asyncio.create_task(late(release))
            await asyncio.sleep(0)
        release.set()
        await straggler

        assert resolved == [None]

    @pytest.mark.asyncio
    async def test_legacy_session_does_not_trust_the_contextvar(self, manager):
        """Back-channel requests run on the receive loop, whose context is stale."""
        tc1 = ToolCall(id="call_1", name="tool_a", arguments={})
        tc2 = ToolCall(id="call_2", name="tool_a", arguments={})
        manager._elicitation_routing[("srv", tc2.id)] = MagicMock(server_name="srv", tool_call=tc2)

        async with manager._use_elicitation_context("srv", tc1, AsyncMock(), "a@example.com"):
            ambiguous = manager._resolve_routing(
                manager._elicitation_routing, "srv", _session_context(self.LEGACY)
            )
            by_meta = manager._resolve_routing(
                manager._elicitation_routing,
                "srv",
                _session_context(self.LEGACY, meta={"tool_call_id": "call_2"}),
            )

        from atlas.modules.mcp_tools.mcp_routing import AMBIGUOUS_ROUTING

        assert ambiguous is AMBIGUOUS_ROUTING
        assert by_meta.tool_call.id == "call_2"

    @pytest.mark.asyncio
    async def test_legacy_meta_naming_no_live_call_is_not_redirected(self, manager):
        """A request naming a call that is not live must not reach the one that is."""
        bystander = ToolCall(id="call_1", name="tool_a", arguments={})

        async with manager._use_elicitation_context("srv", bystander, AsyncMock(), "a@example.com"):
            routing = manager._resolve_routing(
                manager._elicitation_routing,
                "srv",
                _session_context(self.LEGACY, meta={"tool_call_id": "call_gone"}),
            )

        assert routing is None

    @pytest.mark.asyncio
    async def test_legacy_handler_cancels_a_request_for_an_unknown_call(self, manager):
        update_cb = AsyncMock()
        bystander = ToolCall(id="call_1", name="tool_a", arguments={})
        handler = manager._create_elicitation_handler("srv")

        async with manager._use_elicitation_context("srv", bystander, update_cb, "a@example.com"):
            result = await handler(
                "Password?", str, MagicMock(),
                _session_context(self.LEGACY, meta={"tool_call_id": "call_gone"}),
            )

        assert result.action == "cancel"
        update_cb.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("meta", [None, {"tool_call_id": "call_1"}])
    async def test_two_live_calls_sharing_an_id_are_both_refused(self, manager, meta):
        """Tool call ids are provider-generated; a collision must not cross users."""
        from atlas.modules.mcp_tools.mcp_routing import AMBIGUOUS_ROUTING

        alice_cb, bob_cb = AsyncMock(), AsyncMock()
        alice = ToolCall(id="call_1", name="tool_a", arguments={})
        bob = ToolCall(id="call_1", name="tool_a", arguments={})
        handler = manager._create_elicitation_handler("srv")

        async with manager._use_elicitation_context("srv", alice, alice_cb, "alice@example.com"):
            async with manager._use_elicitation_context("srv", bob, bob_cb, "bob@example.com"):
                legacy = manager._resolve_routing(
                    manager._elicitation_routing, "srv", _session_context(self.LEGACY, meta=meta)
                )
                modern = manager._resolve_routing(
                    manager._elicitation_routing, "srv", _session_context(self.MODERN)
                )
                result = await handler("Password?", str, MagicMock(), _session_context(self.LEGACY, meta=meta))
            # Still poisoned while one holder remains: Alice's entry was
            # overwritten, so it cannot be trusted again for this call.
            after_one_left = manager._resolve_routing(
                manager._elicitation_routing, "srv", _session_context(self.LEGACY, meta=meta)
            )

        assert legacy is AMBIGUOUS_ROUTING
        assert modern is None
        assert result.action == "cancel"
        assert after_one_left is AMBIGUOUS_ROUTING
        alice_cb.assert_not_awaited()
        bob_cb.assert_not_awaited()
        assert manager._elicitation_routing == {}

    @pytest.mark.asyncio
    async def test_a_collision_on_one_server_does_not_disable_another(self, manager):
        first = ToolCall(id="call_1", name="tool_a", arguments={})
        second = ToolCall(id="call_1", name="tool_a", arguments={})
        other = ToolCall(id="call_1", name="tool_b", arguments={})

        async with manager._use_elicitation_context("srv", first, AsyncMock(), "a@example.com"):
            async with manager._use_elicitation_context("srv", second, AsyncMock(), "b@example.com"):
                async with manager._use_elicitation_context("other", other, AsyncMock(), "c@example.com"):
                    routing = manager._resolve_routing(
                        manager._elicitation_routing, "other", _session_context(self.LEGACY)
                    )

        assert routing.tool_call is other


class TestElicitationModes:
    """Form content shaping and URL-mode requests."""

    @pytest.fixture
    def manager(self):
        from atlas.modules.mcp_tools.client import MCPToolManager
        return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")

    @staticmethod
    def _answering(response):
        elicit_mgr = Mock()
        request = AsyncMock()
        request.wait_for_response = AsyncMock(return_value=response)
        elicit_mgr.create_elicitation_request = Mock(return_value=request)
        elicit_mgr.cleanup_request = Mock()
        return elicit_mgr

    def _handler(self, manager):
        from atlas.modules.mcp_tools.client import _ElicitationRoutingContext

        tool_call = ToolCall(id="call_1", name="tool", arguments={})
        callback = AsyncMock()
        manager._elicitation_routing[("srv", tool_call.id)] = _ElicitationRoutingContext(
            server_name="srv", tool_call=tool_call, update_cb=callback, user_email="a@example.com"
        )
        return manager._create_elicitation_handler("srv"), callback

    @pytest.mark.asyncio
    async def test_form_request_reads_the_requested_schema(self, manager):
        from mcp_types import ElicitRequestFormParams

        schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
        params = ElicitRequestFormParams(message="Name?", requested_schema=schema)
        handler, callback = self._handler(manager)

        with patch(
            "atlas.application.chat.elicitation_manager.get_elicitation_manager",
            return_value=self._answering({"action": "accept", "data": "Ada"}),
        ):
            result = await handler("Name?", str, params, None)

        event = callback.call_args_list[0][0][0]
        assert event["response_schema"] == schema
        assert event["mode"] == "form"
        assert "url" not in event
        assert result.content == {"value": "Ada"}

    @pytest.mark.asyncio
    async def test_blank_optional_fields_are_left_out(self, manager):
        from mcp_types import ElicitRequestFormParams

        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "nickname": {"type": "string"},
                "age": {"type": "integer"},
                "subscribed": {"type": "boolean"},
            },
            "required": ["title"],
        }
        params = ElicitRequestFormParams(message="Details", requested_schema=schema)
        handler, _ = self._handler(manager)
        answer = {"title": "", "nickname": "", "age": None, "subscribed": False}

        with patch(
            "atlas.application.chat.elicitation_manager.get_elicitation_manager",
            return_value=self._answering({"action": "accept", "data": answer}),
        ):
            result = await handler("Details", dict, params, None)

        # A required field is sent even when blank; False is a real answer.
        assert result.content == {"title": "", "subscribed": False}

    @pytest.mark.asyncio
    async def test_blank_optional_single_value_is_left_out(self, manager):
        """A bare "" for an optional scalar must not be sent as {"value": ""}."""
        from mcp_types import ElicitRequestFormParams

        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        params = ElicitRequestFormParams(message="How many?", requested_schema=schema)
        handler, _ = self._handler(manager)

        for answer in ("", {}):
            with patch(
                "atlas.application.chat.elicitation_manager.get_elicitation_manager",
                return_value=self._answering({"action": "accept", "data": answer}),
            ):
                result = await handler("How many?", float, params, None)
            assert result.action == "accept"
            assert result.content == {}

    @pytest.mark.asyncio
    async def test_unknown_action_is_a_cancel(self, manager):
        handler, _ = self._handler(manager)
        with patch(
            "atlas.application.chat.elicitation_manager.get_elicitation_manager",
            return_value=self._answering({"action": "approve-all", "data": {"value": "x"}}),
        ):
            result = await handler("Name?", str, None, None)
        assert result.action == "cancel"
        assert result.content is None

    @pytest.mark.asyncio
    async def test_url_request_is_shown_and_accept_carries_no_data(self, manager):
        from mcp_types import ElicitRequestURLParams

        params = ElicitRequestURLParams(
            message="Sign in to continue", url="https://auth.example.com/start?state=abc"
        )
        handler, callback = self._handler(manager)

        with patch(
            "atlas.application.chat.elicitation_manager.get_elicitation_manager",
            return_value=self._answering({"action": "accept", "data": {"value": "injected"}}),
        ):
            result = await handler("Sign in to continue", None, params, None)

        event = callback.call_args_list[0][0][0]
        assert event["mode"] == "url"
        assert event["url"] == "https://auth.example.com/start?state=abc"
        assert event["response_schema"] == {}
        assert result.action == "accept"
        assert result.content is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "//evil.example.com/x",
            "/relative/path",
            "https://",
            "",
        ],
    )
    async def test_non_web_url_is_declined_without_prompting(self, manager, url):
        from mcp_types import ElicitRequestURLParams

        params = ElicitRequestURLParams(message="Open this", url=url)
        handler, callback = self._handler(manager)

        result = await handler("Open this", None, params, None)

        assert result.action == "decline"
        callback.assert_not_called()
