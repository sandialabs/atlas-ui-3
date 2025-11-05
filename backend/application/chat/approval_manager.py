"""
Tool approval service for managing approval requests and responses.

This module handles the approval workflow for tool calls, allowing users to
approve, reject, or edit tool arguments before execution.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class ToolApprovalRequest:
    """Represents a pending tool approval request."""
    
    def __init__(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        allow_edit: bool = True
    ):
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.allow_edit = allow_edit
        self.future: asyncio.Future = asyncio.Future()
    
    async def wait_for_response(self, timeout: float = 300.0) -> Dict[str, Any]:
        """
        Wait for user response to this approval request.
        
        Args:
            timeout: Maximum time to wait in seconds (default 5 minutes)
        
        Returns:
            Dict with 'approved', 'arguments', and optional 'reason'
        
        Raises:
            asyncio.TimeoutError: If timeout is reached
        """
        try:
            return await asyncio.wait_for(self.future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Approval request timed out for tool {self.tool_name}")
            raise
    
    def set_response(self, approved: bool, arguments: Optional[Dict[str, Any]] = None, reason: Optional[str] = None):
        """Set the user's response to this approval request."""
        if not self.future.done():
            self.future.set_result({
                "approved": approved,
                "arguments": arguments or self.arguments,
                "reason": reason
            })


class ToolApprovalManager:
    """Manages tool approval requests and responses."""
    
    def __init__(self):
        self._pending_requests: Dict[str, ToolApprovalRequest] = {}
    
    def create_approval_request(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        allow_edit: bool = True
    ) -> ToolApprovalRequest:
        """
        Create a new approval request.
        
        Args:
            tool_call_id: Unique ID for this tool call
            tool_name: Name of the tool being called
            arguments: Tool arguments
            allow_edit: Whether to allow editing of arguments
        
        Returns:
            ToolApprovalRequest object
        """
        request = ToolApprovalRequest(tool_call_id, tool_name, arguments, allow_edit)
        self._pending_requests[tool_call_id] = request
        logger.info(f"Created approval request for tool {tool_name} (call_id: {tool_call_id})")
        return request
    
    def handle_approval_response(
        self,
        tool_call_id: str,
        approved: bool,
        arguments: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None
    ) -> bool:
        """
        Handle a user's response to an approval request.
        
        Args:
            tool_call_id: ID of the tool call being responded to
            approved: Whether the user approved the call
            arguments: Potentially edited arguments (if allowed)
            reason: Optional reason for rejection
        
        Returns:
            True if request was found and handled, False otherwise
        """
        logger.info(f"handle_approval_response called: tool_call_id={tool_call_id}, approved={approved}")
        logger.info(f"Pending requests: {list(self._pending_requests.keys())}")
        
        request = self._pending_requests.get(tool_call_id)
        if request is None:
            logger.warning(f"Received approval response for unknown tool call: {tool_call_id}")
            logger.warning(f"Available pending requests: {list(self._pending_requests.keys())}")
            return False
        
        logger.info(f"Found pending request for {tool_call_id}, setting response")
        request.set_response(approved, arguments, reason)
        # Keep the request in the dict for a bit to avoid race conditions
        # It will be cleaned up later
        logger.info(f"Approval response handled for tool {request.tool_name}: approved={approved}")
        return True
    
    def cleanup_request(self, tool_call_id: str):
        """Remove a completed approval request."""
        if tool_call_id in self._pending_requests:
            del self._pending_requests[tool_call_id]
            logger.debug(f"Cleaned up approval request: {tool_call_id}")
    
    def get_pending_requests(self) -> Dict[str, ToolApprovalRequest]:
        """Get all pending approval requests."""
        return dict(self._pending_requests)


# Global approval manager instance (one per application)
_approval_manager: Optional[ToolApprovalManager] = None


def get_approval_manager() -> ToolApprovalManager:
    """Get the global tool approval manager instance."""
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = ToolApprovalManager()
    return _approval_manager
