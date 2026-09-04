"""
Tool approval service for managing approval requests and responses.

This module handles the approval workflow for tool calls, allowing users to
approve, reject, or edit tool arguments before execution.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.mcp_tools.tool_audit_log import record_tool_decision

logger = logging.getLogger(__name__)


class ToolApprovalRequest:
    """Represents a pending tool approval request."""

    def __init__(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        allow_edit: bool = True,
        user_email: str = "",
        display_arguments: Optional[Dict[str, Any]] = None,
    ):
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.arguments = arguments
        # PresentedCall is owned by the caller that actually emits the UI
        # payload. If a legacy/direct caller omits it, keep the supplied
        # arguments as-is rather than reconstructing a client-visible form.
        self.display_arguments = arguments if display_arguments is None else display_arguments
        self.allow_edit = allow_edit
        self.user_email = user_email
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
            safe_tool_name = str(self.tool_name).replace("\r", "").replace("\n", "")
            logger.warning("Approval request timed out for tool %s", safe_tool_name)
            record_tool_decision(
                user_email=self.user_email,
                request_owner=self.user_email,
                tool_call_id=self.tool_call_id,
                tool_name=self.tool_name,
                arguments=self.display_arguments,
                decision="timeout",
                decision_origin="approval_timeout",
            )
            raise

    def set_response(self, approved: bool, arguments: Optional[Dict[str, Any]] = None, reason: Optional[str] = None):
        """Set the user's response to this approval request."""
        if not self.future.done():
            effective_arguments = (
                arguments
                if self.allow_edit and arguments is not None
                else self.arguments
            )
            self.future.set_result({
                "approved": approved,
                "arguments": effective_arguments,
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
        allow_edit: bool = True,
        user_email: str = "",
        display_arguments: Optional[Dict[str, Any]] = None,
    ) -> ToolApprovalRequest:
        """
        Create a new approval request.

        Args:
            tool_call_id: Unique ID for this tool call
            tool_name: Name of the tool being called
            arguments: Tool arguments
            allow_edit: Whether to allow editing of arguments
            user_email: Authenticated email of the user who owns this request
            display_arguments: Canonical client-visible arguments shown for approval

        Returns:
            ToolApprovalRequest object
        """
        request = ToolApprovalRequest(
            tool_call_id,
            tool_name,
            arguments,
            allow_edit,
            user_email=user_email,
            display_arguments=display_arguments,
        )
        self._pending_requests[tool_call_id] = request
        logger.info(f"Created approval request for tool {sanitize_for_logging(tool_name)} (call_id: {sanitize_for_logging(tool_call_id)})")
        return request

    def handle_approval_response(
        self,
        tool_call_id: str,
        approved: bool,
        arguments: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        user_email: str = "",
    ) -> bool:
        """
        Handle a user's response to an approval request.

        Args:
            tool_call_id: ID of the tool call being responded to
            approved: Whether the user approved the call
            arguments: Potentially edited arguments (if allowed)
            reason: Optional reason for rejection
            user_email: Authenticated email of the responding user

        Returns:
            True if request was found and handled, False otherwise
        """
        logger.debug(
            "handle_approval_response called: tool_call_id=%s, approved=%s",
            sanitize_for_logging(tool_call_id),
            sanitize_for_logging(approved),
        )
        logger.debug("Pending requests: %s", [sanitize_for_logging(key) for key in self._pending_requests.keys()])

        request = self._pending_requests.get(tool_call_id)
        if request is None:
            logger.warning(f"Received approval response for unknown tool call: {sanitize_for_logging(tool_call_id)}")
            logger.debug("Available pending requests: %s", list(self._pending_requests.keys()))
            return False

        # Security: verify the responding user owns this approval request.
        # Prevents cross-user approval bypass where one user approves
        # another user's pending tool call. Fail-closed: if the request has
        # a user_email binding, the response MUST supply a matching one.
        # Backward compat: only legacy requests (no user_email set) skip the
        # check so single-user deployments continue to work.
        if request.user_email and request.user_email != user_email:
            # Inline sanitize for CodeQL py/log-injection: the query traces
            # explicit CR/LF removal as a log-injection sanitizer, but does
            # not recognize sanitize_for_logging() as one.
            safe_user_email = str(user_email).replace("\r", "").replace("\n", "")
            safe_tool_call_id = str(tool_call_id).replace("\r", "").replace("\n", "")
            safe_approved = str(approved).replace("\r", "").replace("\n", "")
            logger.warning(
                "SECURITY: approval response rejected — user %s attempted to "
                "respond to tool call owned by a different user "
                "(call_id: %s, approved=%s)",
                safe_user_email,
                safe_tool_call_id,
                safe_approved,
            )
            record_tool_decision(
                user_email=user_email,
                request_owner=request.user_email,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.display_arguments,
                decision="invalid_responder",
                decision_origin="ownership_check",
            )
            return False

        # The request deliberately remains pending until the executor cleans it
        # up, so a duplicate response can arrive after the Future is resolved.
        # Only the first response affects execution; do not emit contradictory
        # audit evidence for later responses that are ignored by set_response().
        if request.future.done():
            safe_tool_call_id = str(tool_call_id).replace("\r", "").replace("\n", "")
            logger.debug(
                "Ignoring duplicate approval response for completed request: %s",
                safe_tool_call_id,
            )
            return True

        # Audit the same client-visible representation used at the approval gate.
        # Client-supplied arguments only become decision evidence when editing is
        # allowed; otherwise both the executor and the audit retain the baseline.
        effective_arguments = request.display_arguments
        arguments_edited = False
        if request.allow_edit and arguments is not None:
            effective_arguments = arguments
            try:
                arguments_edited = arguments != request.display_arguments
            except Exception:
                # Comparison is audit-only evidence and must never break approval.
                arguments_edited = True

        logger.debug("Found pending request for %s; setting response", sanitize_for_logging(tool_call_id))
        request.set_response(approved, arguments, reason)
        record_tool_decision(
            user_email=user_email or request.user_email,
            request_owner=request.user_email,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=effective_arguments,
            decision="approved" if approved else "rejected",
            decision_origin="approval_response",
            arguments_edited=arguments_edited,
            reason_present=bool(reason),
        )
        # Keep the request in the dict for a bit to avoid race conditions
        # It will be cleaned up later
        safe_tool_name = str(request.tool_name).replace("\r", "").replace("\n", "")
        safe_approved = str(approved).replace("\r", "").replace("\n", "")
        logger.info(
            "Approval response handled for tool %s: approved=%s",
            safe_tool_name,
            safe_approved,
        )
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
