"""
Elicitation manager for handling user input requests during tool execution.

This manager coordinates between MCP servers requesting user input (via ctx.elicit())
and the frontend UI collecting that input. It provides a synchronization mechanism
where tool execution pauses until the user responds.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ElicitationRequest:
    """Represents a pending elicitation request awaiting user response."""
    elicitation_id: str
    tool_call_id: str
    tool_name: str
    message: str
    response_schema: Dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())

    async def wait_for_response(self, timeout: float = 300.0) -> Dict[str, Any]:
        """
        Wait for the user to respond to the elicitation request.

        Args:
            timeout: Maximum time to wait in seconds (default 5 minutes)

        Returns:
            Dict with 'action' and optionally 'data' keys

        Raises:
            asyncio.TimeoutError: If timeout is reached
        """
        return await asyncio.wait_for(self.future, timeout=timeout)


class ElicitationManager:
    """
    Manages elicitation requests and responses.

    Provides synchronization between:
    - MCP servers requesting input via ctx.elicit()
    - Frontend UI collecting user responses
    """

    def __init__(self):
        """Initialize the elicitation manager."""
        self._pending_requests: Dict[str, ElicitationRequest] = {}
        self._lock = asyncio.Lock()

    def create_elicitation_request(
        self,
        elicitation_id: str,
        tool_call_id: str,
        tool_name: str,
        message: str,
        response_schema: Dict[str, Any]
    ) -> ElicitationRequest:
        """
        Create a new elicitation request.

        Args:
            elicitation_id: Unique identifier for this elicitation
            tool_call_id: ID of the tool call that requested input
            tool_name: Name of the tool requesting input
            message: Prompt message to display to user
            response_schema: JSON schema defining expected response structure

        Returns:
            ElicitationRequest object that can be awaited for response
        """
        request = ElicitationRequest(
            elicitation_id=elicitation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            message=message,
            response_schema=response_schema
        )
        self._pending_requests[elicitation_id] = request
        logger.info(
            f"Created elicitation request: id={elicitation_id}, "
            f"tool={tool_name}, message='{message[:50]}...'"
        )
        return request

    def handle_elicitation_response(
        self,
        elicitation_id: str,
        action: str,
        data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Handle an elicitation response from the user.

        Args:
            elicitation_id: ID of the elicitation being responded to
            action: User action - "accept", "decline", or "cancel"
            data: Optional response data (present when action is "accept")

        Returns:
            True if response was handled, False if request not found
        """
        request = self._pending_requests.get(elicitation_id)
        if not request:
            logger.warning(f"Received response for unknown elicitation: {elicitation_id}")
            return False

        response = {
            "action": action,
            "data": data
        }

        if not request.future.done():
            request.future.set_result(response)
            logger.info(
                f"Elicitation response received: id={elicitation_id}, "
                f"action={action}, has_data={data is not None}"
            )
        else:
            logger.warning(
                f"Elicitation response ignored (already resolved): {elicitation_id}"
            )

        return True

    def cleanup_request(self, elicitation_id: str) -> None:
        """
        Clean up a completed elicitation request.

        Args:
            elicitation_id: ID of the request to clean up
        """
        if elicitation_id in self._pending_requests:
            del self._pending_requests[elicitation_id]
            logger.debug(f"Cleaned up elicitation request: {elicitation_id}")

    def get_pending_request(self, elicitation_id: str) -> Optional[ElicitationRequest]:
        """
        Get a pending elicitation request by ID.

        Args:
            elicitation_id: ID of the request to retrieve

        Returns:
            ElicitationRequest if found, None otherwise
        """
        return self._pending_requests.get(elicitation_id)

    def get_all_pending_requests(self) -> Dict[str, ElicitationRequest]:
        """
        Get all pending elicitation requests.

        Returns:
            Dictionary mapping elicitation IDs to requests
        """
        return dict(self._pending_requests)

    def cancel_all_requests(self) -> None:
        """Cancel all pending elicitation requests."""
        for request in self._pending_requests.values():
            if not request.future.done():
                request.future.set_exception(
                    asyncio.CancelledError("Elicitation cancelled")
                )
        self._pending_requests.clear()
        logger.info("Cancelled all pending elicitation requests")


# Global singleton instance
_elicitation_manager: Optional[ElicitationManager] = None


def get_elicitation_manager() -> ElicitationManager:
    """
    Get the global elicitation manager singleton.

    Returns:
        Global ElicitationManager instance
    """
    global _elicitation_manager
    if _elicitation_manager is None:
        _elicitation_manager = ElicitationManager()
    return _elicitation_manager
