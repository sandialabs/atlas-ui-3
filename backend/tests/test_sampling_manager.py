"""Tests for the sampling manager."""

import asyncio
import pytest
from application.chat.sampling_manager import (
    SamplingManager,
    SamplingRequest,
    get_sampling_manager
)


class TestSamplingRequest:
    """Test SamplingRequest class."""

    @pytest.mark.asyncio
    async def test_create_sampling_request(self):
        """Test creating a sampling request."""
        request = SamplingRequest(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Hello"}],
            system_prompt="You are a helpful assistant",
            temperature=0.7,
            max_tokens=500
        )
        assert request.sampling_id == "sample_123"
        assert request.tool_call_id == "tool_456"
        assert request.tool_name == "test_tool"
        assert len(request.messages) == 1
        assert request.system_prompt == "You are a helpful assistant"
        assert request.temperature == 0.7
        assert request.max_tokens == 500

    @pytest.mark.asyncio
    async def test_wait_for_response_success(self):
        """Test waiting for a successful response."""
        request = SamplingRequest(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )
        
        # Simulate setting a response
        response_data = {"text": "This is the LLM response"}
        request.future.set_result(response_data)
        
        # Wait for the response (should be immediate since we already set it)
        response = await request.wait_for_response(timeout=1.0)
        
        assert response["text"] == "This is the LLM response"

    @pytest.mark.asyncio
    async def test_wait_for_response_error(self):
        """Test waiting for an error response."""
        request = SamplingRequest(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )
        
        # Simulate an error
        request.future.set_exception(Exception("LLM error"))
        
        # Should raise the exception
        with pytest.raises(Exception, match="LLM error"):
            await request.wait_for_response(timeout=1.0)

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Test that timeout works correctly."""
        request = SamplingRequest(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )
        
        # Should timeout since we don't set a response
        with pytest.raises(asyncio.TimeoutError):
            await request.wait_for_response(timeout=0.1)


class TestSamplingManager:
    """Test SamplingManager class."""

    @pytest.mark.asyncio
    async def test_create_sampling_request(self):
        """Test creating a sampling request via manager."""
        manager = SamplingManager()
        manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Hello"}],
            system_prompt="You are helpful",
            temperature=0.7,
            max_tokens=500
        )

        assert "sample_123" in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_handle_sampling_response_success(self):
        """Test handling a successful response."""
        manager = SamplingManager()
        manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )

        # Handle the response
        result = manager.handle_sampling_response(
            sampling_id="sample_123",
            text="Generated LLM response"
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_handle_sampling_response_error(self):
        """Test handling an error response."""
        manager = SamplingManager()
        manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )

        # Handle error response
        result = manager.handle_sampling_response(
            sampling_id="sample_123",
            text="",
            error="Model unavailable"
        )

        assert result is True

    def test_handle_unknown_sampling(self):
        """Test handling response for unknown sampling request."""
        manager = SamplingManager()
        
        # Try to handle response for non-existent request
        result = manager.handle_sampling_response(
            sampling_id="unknown_123",
            text="Some text"
        )
        
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_request(self):
        """Test cleaning up a sampling request."""
        manager = SamplingManager()
        manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )
        
        # Verify request exists
        assert "sample_123" in manager.get_all_pending_requests()
        
        # Cleanup the request
        manager.cleanup_request("sample_123")
        
        # Verify request is removed
        assert "sample_123" not in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_get_pending_request(self):
        """Test retrieving a pending request."""
        manager = SamplingManager()
        request = manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test"}]
        )

        # Retrieve the request
        retrieved = manager.get_pending_request("sample_123")

        assert retrieved is not None
        assert retrieved.sampling_id == "sample_123"
        assert retrieved.tool_call_id == "tool_456"

    @pytest.mark.asyncio
    async def test_get_pending_request_not_found(self):
        """Test retrieving non-existent request."""
        manager = SamplingManager()

        # Try to get non-existent request
        retrieved = manager.get_pending_request("unknown_123")

        assert retrieved is None

    @pytest.mark.asyncio
    async def test_cancel_all_requests(self):
        """Test cancelling all pending requests."""
        manager = SamplingManager()

        # Create multiple requests
        manager.create_sampling_request(
            sampling_id="sample_1",
            tool_call_id="tool_1",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test 1"}]
        )
        manager.create_sampling_request(
            sampling_id="sample_2",
            tool_call_id="tool_2",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Test 2"}]
        )

        # Verify both exist
        assert len(manager.get_all_pending_requests()) == 2

        # Cancel all requests
        manager.cancel_all_requests()

        # Verify all are removed
        assert len(manager.get_all_pending_requests()) == 0

    def test_get_sampling_manager_singleton(self):
        """Test that get_sampling_manager returns singleton."""
        manager1 = get_sampling_manager()
        manager2 = get_sampling_manager()
        
        assert manager1 is manager2


class TestSamplingManagerIntegration:
    """Integration tests for SamplingManager."""

    @pytest.mark.asyncio
    async def test_full_sampling_flow(self):
        """Test complete sampling flow from request to response."""
        manager = SamplingManager()
        
        # Create request
        request = manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Summarize this text"}],
            system_prompt="You are a summarizer",
            temperature=0.5,
            max_tokens=200
        )
        
        # Simulate async handling
        async def simulate_llm_response():
            # Wait a bit to simulate LLM processing
            await asyncio.sleep(0.1)
            # LLM responds
            manager.handle_sampling_response(
                sampling_id="sample_123",
                text="Here is a concise summary of the text..."
            )
        
        # Start the simulation
        asyncio.create_task(simulate_llm_response())
        
        # Wait for response
        response = await request.wait_for_response(timeout=2.0)
        
        assert response["text"] == "Here is a concise summary of the text..."
        
        # Cleanup
        manager.cleanup_request("sample_123")
        assert "sample_123" not in manager.get_all_pending_requests()

    @pytest.mark.asyncio
    async def test_sequential_sampling(self):
        """Test handling multiple sequential sampling requests."""
        manager = SamplingManager()
        
        # First sampling request
        request1 = manager.create_sampling_request(
            sampling_id="sample_1",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "First question"}]
        )
        
        # Immediately respond to first
        manager.handle_sampling_response(
            sampling_id="sample_1",
            text="First answer"
        )
        
        response1 = await request1.wait_for_response(timeout=1.0)
        assert response1["text"] == "First answer"
        
        manager.cleanup_request("sample_1")
        
        # Second sampling request
        request2 = manager.create_sampling_request(
            sampling_id="sample_2",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Second question"}]
        )
        
        # Respond to second
        manager.handle_sampling_response(
            sampling_id="sample_2",
            text="Second answer"
        )
        
        response2 = await request2.wait_for_response(timeout=1.0)
        assert response2["text"] == "Second answer"
        
        manager.cleanup_request("sample_2")

    @pytest.mark.asyncio
    async def test_sampling_with_model_preferences(self):
        """Test sampling request with model preferences."""
        manager = SamplingManager()
        
        request = manager.create_sampling_request(
            sampling_id="sample_123",
            tool_call_id="tool_456",
            tool_name="test_tool",
            messages=[{"role": "user", "content": "Generate code"}],
            model_preferences=["gpt-4", "claude-3-sonnet"]
        )
        
        # Verify preferences are stored
        assert request.model_preferences == ["gpt-4", "claude-3-sonnet"]
        
        # Respond
        manager.handle_sampling_response(
            sampling_id="sample_123",
            text="def hello(): print('Hello')"
        )
        
        response = await request.wait_for_response(timeout=1.0)
        assert "hello" in response["text"]
        
        manager.cleanup_request("sample_123")
