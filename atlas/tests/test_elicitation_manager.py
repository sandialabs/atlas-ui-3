"""Tests for the elicitation manager."""

import asyncio

import pytest

from atlas.application.chat.elicitation_manager import ElicitationManager, ElicitationRequest, get_elicitation_manager


class TestElicitationRequest:
    """Test ElicitationRequest class."""

    @pytest.mark.asyncio
    async def test_create_elicitation_request(self):
        """Test creating an elicitation request."""
        request = ElicitationRequest(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Please provide your name",
            response_schema={"type": "object", "properties": {"value": {"type": "string"}}}
        )
        assert request.elicitation_id == "elicit_123"
        assert request.tool_call_id == "tool_456"
        assert request.tool_name == "test_tool"
        assert request.message == "Please provide your name"
        assert "properties" in request.response_schema

    @pytest.mark.asyncio
    async def test_wait_for_response_accept(self):
        """Test waiting for an accept response."""
        request = ElicitationRequest(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Simulate setting a response
        response_data = {"action": "accept", "data": {"name": "John"}}
        request.future.set_result(response_data)

        # Wait for the response (should be immediate since we already set it)
        response = await request.wait_for_response(timeout=1.0)

        assert response["action"] == "accept"
        assert response["data"] == {"name": "John"}

    @pytest.mark.asyncio
    async def test_wait_for_response_decline(self):
        """Test waiting for a decline response."""
        request = ElicitationRequest(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Simulate decline response
        response_data = {"action": "decline", "data": None}
        request.future.set_result(response_data)

        response = await request.wait_for_response(timeout=1.0)

        assert response["action"] == "decline"
        assert response["data"] is None

    @pytest.mark.asyncio
    async def test_wait_for_response_cancel(self):
        """Test waiting for a cancel response."""
        request = ElicitationRequest(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Simulate cancel response
        response_data = {"action": "cancel", "data": None}
        request.future.set_result(response_data)

        response = await request.wait_for_response(timeout=1.0)

        assert response["action"] == "cancel"
        assert response["data"] is None

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Test that timeout works correctly."""
        request = ElicitationRequest(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Should timeout since we don't set a response
        with pytest.raises(asyncio.TimeoutError):
            await request.wait_for_response(timeout=0.1)


class TestElicitationManager:
    """Test ElicitationManager class."""

    @pytest.mark.asyncio
    async def test_create_elicitation_request(self):
        """Test creating an elicitation request via manager."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        assert "elicit_123" in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_handle_elicitation_response_accept(self):
        """Test handling an accept response."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Handle the response
        result = manager.handle_elicitation_response(
            elicitation_id="elicit_123",
            action="accept",
            data={"name": "John"}
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_elicitation_response_decline(self):
        """Test handling a decline response."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Handle decline response
        result = manager.handle_elicitation_response(
            elicitation_id="elicit_123",
            action="decline",
            data=None
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_elicitation_response_cancel(self):
        """Test handling a cancel response."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Handle cancel response
        result = manager.handle_elicitation_response(
            elicitation_id="elicit_123",
            action="cancel",
            data=None
        )

        assert result is True

    def test_handle_unknown_elicitation(self):
        """Test handling response for unknown elicitation."""
        manager = ElicitationManager()

        # Try to handle response for non-existent elicitation
        result = manager.handle_elicitation_response(
            elicitation_id="unknown_123",
            action="accept",
            data={"name": "John"}
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_request(self):
        """Test cleaning up an elicitation request."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Verify request exists
        assert "elicit_123" in manager.get_all_pending_requests()

        # Cleanup the request
        manager.cleanup_request("elicit_123")

        # Verify request is removed
        assert "elicit_123" not in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_get_pending_request(self):
        """Test retrieving a pending request."""
        manager = ElicitationManager()
        manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Retrieve the request
        retrieved = manager.get_pending_request("elicit_123")

        assert retrieved is not None
        assert retrieved.elicitation_id == "elicit_123"
        assert retrieved.tool_call_id == "tool_456"

    @pytest.mark.asyncio
    async def test_get_pending_request_not_found(self):
        """Test retrieving non-existent request."""
        manager = ElicitationManager()

        # Try to get non-existent request
        retrieved = manager.get_pending_request("unknown_123")

        assert retrieved is None

    @pytest.mark.asyncio
    async def test_cancel_all_requests(self):
        """Test cancelling all pending requests."""
        manager = ElicitationManager()

        # Create multiple requests
        manager.create_elicitation_request(
            elicitation_id="elicit_1",
            tool_call_id="tool_1",
            tool_name="test_tool",
            message="Request 1",
            response_schema={"type": "object"}
        )
        manager.create_elicitation_request(
            elicitation_id="elicit_2",
            tool_call_id="tool_2",
            tool_name="test_tool",
            message="Request 2",
            response_schema={"type": "object"}
        )

        # Verify both exist
        assert len(manager.get_all_pending_requests()) == 2

        # Cancel all requests
        manager.cancel_all_requests()

        # Verify all are removed
        assert len(manager.get_all_pending_requests()) == 0

    def test_get_elicitation_manager_singleton(self):
        """Test that get_elicitation_manager returns singleton."""
        manager1 = get_elicitation_manager()
        manager2 = get_elicitation_manager()

        assert manager1 is manager2


class TestElicitationManagerIntegration:
    """Integration tests for ElicitationManager."""

    @pytest.mark.asyncio
    async def test_full_elicitation_flow(self):
        """Test complete elicitation flow from request to response."""
        manager = ElicitationManager()

        # Create request
        request = manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your information",
            response_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"}
                }
            }
        )

        # Simulate async handling
        async def simulate_user_response():
            # Wait a bit to simulate user thinking
            await asyncio.sleep(0.1)
            # User responds
            manager.handle_elicitation_response(
                elicitation_id="elicit_123",
                action="accept",
                data={"name": "Alice", "age": 30}
            )

        # Start the simulation
        asyncio.create_task(simulate_user_response())

        # Wait for response
        response = await request.wait_for_response(timeout=2.0)

        assert response["action"] == "accept"
        assert response["data"]["name"] == "Alice"
        assert response["data"]["age"] == 30

        # Cleanup
        manager.cleanup_request("elicit_123")
        assert "elicit_123" not in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_multi_turn_elicitation(self):
        """Test handling multiple sequential elicitation requests."""
        manager = ElicitationManager()

        # First elicitation
        request1 = manager.create_elicitation_request(
            elicitation_id="elicit_1",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your name",
            response_schema={"type": "object"}
        )

        # Immediately respond to first
        manager.handle_elicitation_response(
            elicitation_id="elicit_1",
            action="accept",
            data={"name": "Bob"}
        )

        response1 = await request1.wait_for_response(timeout=1.0)
        assert response1["action"] == "accept"

        manager.cleanup_request("elicit_1")

        # Second elicitation
        request2 = manager.create_elicitation_request(
            elicitation_id="elicit_2",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter your age",
            response_schema={"type": "object"}
        )

        # Respond to second
        manager.handle_elicitation_response(
            elicitation_id="elicit_2",
            action="accept",
            data={"age": 25}
        )

        response2 = await request2.wait_for_response(timeout=1.0)
        assert response2["action"] == "accept"
        assert response2["data"]["age"] == 25

        manager.cleanup_request("elicit_2")

    @pytest.mark.asyncio
    async def test_elicitation_with_decline(self):
        """Test elicitation flow when user declines."""
        manager = ElicitationManager()

        request = manager.create_elicitation_request(
            elicitation_id="elicit_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            message="Enter optional information",
            response_schema={"type": "object"}
        )

        # User declines
        manager.handle_elicitation_response(
            elicitation_id="elicit_123",
            action="decline",
            data=None
        )

        response = await request.wait_for_response(timeout=1.0)

        assert response["action"] == "decline"
        assert response["data"] is None

        manager.cleanup_request("elicit_123")
