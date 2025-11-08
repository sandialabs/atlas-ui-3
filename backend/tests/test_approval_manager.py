"""Tests for the tool approval manager."""

import asyncio
import pytest
from application.chat.approval_manager import (
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
        manager.create_approval_request(
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
        asyncio.create_task(approve_after_delay())

        # Wait for response
        response = await request.wait_for_response(timeout=1.0)
        
        assert response["approved"] is True
        assert response["arguments"]["code"] == "print('edited test')"
        
        # Cleanup
        manager.cleanup_request("test_123")
        assert "test_123" not in manager.get_pending_requests()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_approvals(self):
        """Test handling multiple concurrent approval requests."""
        manager = ToolApprovalManager()

        # Create multiple requests
        request1 = manager.create_approval_request(
            tool_call_id="test_1",
            tool_name="tool_a",
            arguments={"arg": "value1"}
        )
        request2 = manager.create_approval_request(
            tool_call_id="test_2",
            tool_name="tool_b",
            arguments={"arg": "value2"}
        )
        request3 = manager.create_approval_request(
            tool_call_id="test_3",
            tool_name="tool_c",
            arguments={"arg": "value3"}
        )

        assert len(manager.get_pending_requests()) == 3

        # Approve them in different order
        async def approve_requests():
            await asyncio.sleep(0.05)
            manager.handle_approval_response("test_2", approved=True)
            await asyncio.sleep(0.05)
            manager.handle_approval_response("test_1", approved=False, reason="Rejected")
            await asyncio.sleep(0.05)
            manager.handle_approval_response("test_3", approved=True)

        asyncio.create_task(approve_requests())

        # Wait for all responses
        response1 = await request1.wait_for_response(timeout=1.0)
        response2 = await request2.wait_for_response(timeout=1.0)
        response3 = await request3.wait_for_response(timeout=1.0)

        assert response1["approved"] is False
        assert response1["reason"] == "Rejected"
        assert response2["approved"] is True
        assert response3["approved"] is True

    @pytest.mark.asyncio
    async def test_approval_with_no_arguments_change(self):
        """Test approval where arguments are returned but not changed."""
        manager = ToolApprovalManager()

        original_args = {"code": "print('hello')"}
        request = manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments=original_args,
            allow_edit=True
        )

        # Approve with same arguments
        async def approve():
            await asyncio.sleep(0.05)
            manager.handle_approval_response(
                tool_call_id="test_123",
                approved=True,
                arguments={"code": "print('hello')"}  # Same as original
            )

        asyncio.create_task(approve())
        response = await request.wait_for_response(timeout=1.0)

        assert response["approved"] is True
        assert response["arguments"] == original_args

    @pytest.mark.asyncio
    async def test_double_response_handling(self):
        """Test that setting response twice doesn't cause issues."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )

        # Set response first time
        request.set_response(approved=True, arguments={"arg1": "first"})

        # Try to set response second time (should be ignored)
        request.set_response(approved=False, arguments={"arg1": "second"})

        # Should get the first response
        response = await request.wait_for_response(timeout=0.5)
        assert response["approved"] is True
        assert response["arguments"]["arg1"] == "first"

    @pytest.mark.asyncio
    async def test_rejection_with_empty_reason(self):
        """Test rejection with no reason provided."""
        manager = ToolApprovalManager()

        request = manager.create_approval_request(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"}
        )

        async def reject():
            await asyncio.sleep(0.05)
            manager.handle_approval_response(
                tool_call_id="test_123",
                approved=False
                # No reason provided
            )

        _ = asyncio.create_task(reject())
        response = await request.wait_for_response(timeout=1.0)

        assert response["approved"] is False
        assert response.get("reason") is None or response.get("reason") == ""

    @pytest.mark.asyncio
    async def test_allow_edit_false(self):
        """Test approval request with editing disabled."""
        request = ToolApprovalRequest(
            tool_call_id="test_123",
            tool_name="test_tool",
            arguments={"arg1": "value1"},
            allow_edit=False
        )

        assert request.allow_edit is False

        # Even if arguments are provided, they should be used
        request.set_response(approved=True, arguments={"arg1": "edited_value"})
        response = await request.wait_for_response(timeout=0.5)

        # The response will contain the edited arguments, but the UI should
        # respect allow_edit=False to prevent showing edit controls
        assert response["arguments"] == {"arg1": "edited_value"}

    def test_cleanup_nonexistent_request(self):
        """Test cleaning up a request that doesn't exist."""
        manager = ToolApprovalManager()

        # Should not raise an error
        manager.cleanup_request("nonexistent_id")

        assert "nonexistent_id" not in manager.get_pending_requests()

    def test_multiple_managers_vs_singleton(self):
        """Test that direct instantiation creates different instances but singleton returns same."""
        manager1 = ToolApprovalManager()
        manager2 = ToolApprovalManager()

        # Direct instantiation creates different instances
        assert manager1 is not manager2

        # But singleton returns the same instance
        singleton1 = get_approval_manager()
        singleton2 = get_approval_manager()
        assert singleton1 is singleton2

    @pytest.mark.asyncio
    async def test_approval_with_complex_arguments(self):
        """Test approval with complex nested arguments."""
        manager = ToolApprovalManager()

        complex_args = {
            "nested": {
                "level1": {
                    "level2": ["item1", "item2", "item3"]
                }
            },
            "list_of_dicts": [
                {"key": "value1"},
                {"key": "value2"}
            ],
            "numbers": [1, 2, 3, 4, 5]
        }

        request = manager.create_approval_request(
            tool_call_id="test_complex",
            tool_name="complex_tool",
            arguments=complex_args,
            allow_edit=True
        )

        # Modify nested structure
        edited_args = {
            "nested": {
                "level1": {
                    "level2": ["item1", "modified_item", "item3"]
                }
            },
            "list_of_dicts": [
                {"key": "value1"},
                {"key": "new_value"}
            ],
            "numbers": [1, 2, 3, 4, 5, 6]
        }

        async def approve():
            await asyncio.sleep(0.05)
            manager.handle_approval_response(
                tool_call_id="test_complex",
                approved=True,
                arguments=edited_args
            )

        asyncio.create_task(approve())
        response = await request.wait_for_response(timeout=1.0)

        assert response["approved"] is True
        assert response["arguments"]["nested"]["level1"]["level2"][1] == "modified_item"
        assert len(response["arguments"]["numbers"]) == 6

    @pytest.mark.asyncio
    async def test_sequential_approvals(self):
        """Test approving requests one after another in sequence."""
        manager = ToolApprovalManager()

        # First approval
        request1 = manager.create_approval_request(
            tool_call_id="seq_1",
            tool_name="tool_1",
            arguments={"step": 1}
        )

        async def approve1():
            await asyncio.sleep(0.05)
            manager.handle_approval_response("seq_1", approved=True)

        task1 = asyncio.create_task(approve1())
        response1 = await request1.wait_for_response(timeout=1.0)
        manager.cleanup_request("seq_1")
        await task1

        assert response1["approved"] is True
        assert "seq_1" not in manager.get_pending_requests()

        # Second approval after first is complete
        request2 = manager.create_approval_request(
            tool_call_id="seq_2",
            tool_name="tool_2",
            arguments={"step": 2}
        )

        async def approve2():
            await asyncio.sleep(0.05)
            manager.handle_approval_response("seq_2", approved=True)

        task2 = asyncio.create_task(approve2())
        response2 = await request2.wait_for_response(timeout=1.0)
        manager.cleanup_request("seq_2")
        await task2

        assert response2["approved"] is True
        assert "seq_2" not in manager.get_pending_requests()
