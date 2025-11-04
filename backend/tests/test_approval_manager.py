"""Tests for the tool approval manager."""

import asyncio
import pytest
from backend.application.chat.approval_manager import (
    ToolApprovalManager,
    ToolApprovalRequest,
    get_approval_manager
)


class TestToolApprovalRequest:
    """Test ToolApprovalRequest class."""

    def test_create_approval_request(self):
        """Test creating an approval request."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"},
            allow_edit=True
        )
        assert request.tool_call_id == "test_123"
        assert request.tool_name == "test_tool"
        assert request.arguments == {"arg1": "value1"}
        assert request.allow_edit is True

    @pytest.mark.asyncio
    async def test_set_response(self):
        """Test setting a response to an approval request."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )
        
        # Set approved response
        request.set_response(approved=True, arguments={"arg1": "edited_value"})
        
        # Wait for the response (should be immediate since we already set it)
        response = await request.wait_for_response(timeout=1.0)
        
        assert response["approved"] is True
        assert response["arguments"] == {"arg1": "edited_value"}

    @pytest.mark.asyncio
    async def test_rejection_response(self):
        """Test rejecting an approval request."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )
        
        # Set rejected response
        request.set_response(approved=False, reason="User rejected")
        
        # Wait for the response
        response = await request.wait_for_response(timeout=1.0)
        
        assert response["approved"] is False
        assert response["reason"] == "User rejected"

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Test that timeout works correctly."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )
        
        # Should timeout since we don't set a response
        with pytest.raises(asyncio.TimeoutError):
            await request.wait_for_response(timeout=0.1)


class TestToolApprovalManager:
    """Test ToolApprovalManager class."""

    def test_create_approval_request(self):
        """Test creating an approval request via manager."""
        manager = ToolApprovalManager()
        request = manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"},
            allow_edit=True
        )
        
        assert request.tool_call_id == "test_123"
        assert "test_123" in manager.get_pending_requests()

    def test_handle_approval_response(self):
        """Test handling an approval response."""
        manager = ToolApprovalManager()
        request = manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )
        
        # Handle approval response
        result = manager.handle_approval_response(
            tool_call_id="test_123",
            approved=True,
            arguments={"arg1": "edited_value"}
        )
        
        assert result is True
        # Request should still be in pending (cleaned up manually later)
        assert "test_123" in manager.get_pending_requests()

    def test_handle_unknown_request(self):
        """Test handling response for unknown request."""
        manager = ToolApprovalManager()
        
        result = manager.handle_approval_response(
            tool_call_id="unknown_123",
            approved=True
        )
        
        assert result is False

    def test_cleanup_request(self):
        """Test cleaning up a completed request."""
        manager = ToolApprovalManager()
        manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )
        
        assert "test_123" in manager.get_pending_requests()
        
        manager.cleanup_request("test_123")
        
        assert "test_123" not in manager.get_pending_requests()

    def test_get_approval_manager_singleton(self):
        """Test that get_approval_manager returns a singleton."""
        manager1 = get_approval_manager()
        manager2 = get_approval_manager()
        
        assert manager1 is manager2

    @pytest.mark.asyncio
    async def test_full_approval_workflow(self):
        """Test the complete approval workflow."""
        manager = ToolApprovalManager()
        
        # Create request
        request = manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"code": "print('test')"},
            allow_edit=True
        )
        
        # Simulate async approval (in a separate task)
        async def approve_after_delay():
            await asyncio.sleep(0.1)
            manager.handle_approval_response(
                tool_call_id="test_123",
                approved=True,
                arguments={"code": "print('edited test')"}
            )
        
        # Start approval task
        approval_task = asyncio.create_task(approve_after_delay())
        
        # Wait for response
        response = await request.wait_for_response(timeout=1.0)
        
        assert response["approved"] is True
        assert response["arguments"]["code"] == "print('edited test')"
        
        # Cleanup
        manager.cleanup_request("test_123")
        assert "test_123" not in manager.get_pending_requests()
        
        await approval_task
