"""
Tool approval service for managing approval requests and responses.

This module handles the approval workflow for tool calls, allowing users to
approve, reject, or edit tool arguments before execution.
"""

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Dict, Optional, Union

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)


def resolve_approval_timeout() -> Optional[float]:
    """Configured wait for a tool approval / elicitation response.

    ``TOOL_APPROVAL_TIMEOUT_SECONDS=0`` means "wait indefinitely", which is how
    a deployment opts into approvals that survive the user closing the browser
    (issue #884). Any failure to read settings falls back to the historical
    five-minute wait rather than blocking a run forever on a config error.
    """
    try:
        # Read settings through the config module's own accessor rather than
        # the app factory: the factory pulls in the whole chat pipeline, which
        # imports this module back, and the resulting import cycle is both a
        # lint finding and a real fragility.
        from atlas.modules.config.config_manager import get_app_settings

        configured = get_app_settings().tool_approval_timeout_seconds
    except Exception:  # pragma: no cover - defensive
        return 300.0
    if configured is None:
        return 300.0
    return None if configured <= 0 else float(configured)


class ToolApprovalRequest:
    """Represents a pending tool approval request."""

    def __init__(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        allow_edit: bool = True,
        user_email: str = "",
        invocation_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ):
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.allow_edit = allow_edit
        self.user_email = user_email
        self.invocation_id = invocation_id
        self.run_id = run_id
        self.future: asyncio.Future = asyncio.Future()

    async def wait_for_response(self, timeout: Optional[float] = 300.0) -> Dict[str, Any]:
        """
        Wait for user response to this approval request.

        Args:
            timeout: Maximum time to wait in seconds (default 5 minutes).
                ``None`` (or a non-positive value) waits indefinitely. That is
                what makes an approval pause survive the user closing the
                browser: a background run (issue #884) blocks here until the
                user comes back and answers, instead of failing five minutes
                later with nobody watching. The run's wall-clock limit, not
                this timeout, is the backstop against waiting forever.

        Returns:
            Dict with 'approved', 'arguments', and optional 'reason'

        Raises:
            asyncio.TimeoutError: If timeout is reached
        """
        if timeout is None or timeout <= 0:
            return await self.future
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


_RESOLVED_LIMIT = 200


class ToolApprovalManager:
    """Manages tool approval requests and responses."""

    def __init__(self):
        self._pending_requests: Dict[str, ToolApprovalRequest] = {}
        # First outcome per storage key, so a duplicate response after cleanup
        # still returns that outcome. Capped; the shared store is the copy
        # other replicas read.
        self._resolved: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def create_approval_request(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        allow_edit: bool = True,
        user_email: str = "",
        invocation_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> ToolApprovalRequest:
        """
        Create a new approval request.

        Args:
            tool_call_id: Unique ID for this tool call
            tool_name: Name of the tool being called
            arguments: Tool arguments
            allow_edit: Whether to allow editing of arguments
            user_email: Authenticated email of the user who owns this request
            invocation_id: Stable run/step identity, when the call has one
            run_id: Run that owns the call

        Returns:
            ToolApprovalRequest object
        """
        request = ToolApprovalRequest(
            tool_call_id, tool_name, arguments, allow_edit,
            user_email=user_email, invocation_id=invocation_id, run_id=run_id,
        )
        key = invocation_id or tool_call_id
        existing = self._pending_requests.get(key)
        if existing is not None and not existing.future.done():
            return existing
        self._pending_requests[key] = request
        logger.info(
            "Created approval request for tool %s (call_id: %s)",
            sanitize_for_logging(tool_name), sanitize_for_logging(tool_call_id),
        )
        return request

    def handle_approval_response(
        self,
        tool_call_id: str,
        approved: bool,
        arguments: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        user_email: str = "",
        invocation_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Union[bool, Dict[str, Any]]:
        """
        Handle a user's response to an approval request.

        The first response that addresses one request returns the ``True``
        singleton. A response for a request that was already answered returns
        a dict with ``already_resolved`` so the caller can tell a duplicate
        from a fresh accept. ``False`` means the response addressed nothing
        this process can safely resolve (unknown, ambiguous, or the wrong user).

        A bare ``tool_call_id`` resolves only when exactly one pending request
        has it. Providers reuse ``call_0`` across steps and runs, so two
        matches are left untouched.
        """
        logger.debug(
            "handle_approval_response called: tool_call_id=%s, approved=%s",
            sanitize_for_logging(tool_call_id),
            sanitize_for_logging(approved),
        )

        if invocation_id:
            request = self._pending_requests.get(invocation_id)
            if request is not None:
                return self._accept(request, approved, arguments, reason, user_email)
            remembered = self._resolved.get(invocation_id)
            if remembered is None:
                return False
            if not self._owned(remembered.get("user_email"), user_email, tool_call_id):
                return False
            return self._public(remembered)

        matches = [
            request for request in self._pending_requests.values()
            if request.tool_call_id == tool_call_id
            and (
                not run_id or not request.run_id or request.run_id == run_id
            )
        ]
        if len(matches) > 1:
            return False
        if len(matches) == 1:
            return self._accept(matches[0], approved, arguments, reason, user_email)

        remembered_matches = [
            record for record in self._resolved.values()
            if record.get("tool_call_id") == tool_call_id
            and (not run_id or not record.get("run_id") or record.get("run_id") == run_id)
        ]
        if len(remembered_matches) != 1:
            # Inline CR/LF strip: CodeQL treats that as a log-injection
            # sanitizer and does not treat sanitize_for_logging as one.
            safe_tool_call_id = str(tool_call_id).replace("\r", "").replace("\n", "")
            logger.warning(
                "Received approval response for unknown tool call: %s",
                safe_tool_call_id,
            )
            return False
        record = remembered_matches[0]
        if not self._owned(record.get("user_email"), user_email, tool_call_id):
            return False
        return self._public(record)

    def _accept(
        self,
        request: ToolApprovalRequest,
        approved: bool,
        arguments: Optional[Dict[str, Any]],
        reason: Optional[str],
        user_email: str,
    ) -> Union[bool, Dict[str, Any]]:
        if not self._owned(request.user_email, user_email, request.tool_call_id):
            return False
        if request.future.done():
            key = request.invocation_id or request.tool_call_id
            remembered = self._resolved.get(key)
            if remembered is not None:
                return self._public(remembered)
            return self._public(self._record_from_future(request))
        request.set_response(approved, arguments, reason)
        record = {
            "approved": bool(approved),
            "arguments": arguments if arguments is not None else request.arguments,
            "reason": reason,
            "invocation_id": request.invocation_id,
            "tool_call_id": request.tool_call_id,
            "run_id": request.run_id,
            "user_email": request.user_email,
            "status": "approved" if approved else "rejected",
        }
        key = request.invocation_id or request.tool_call_id
        self._resolved[key] = record
        self._resolved.move_to_end(key)
        while len(self._resolved) > _RESOLVED_LIMIT:
            self._resolved.popitem(last=False)
        logger.info(
            "Approval response handled for tool %s: approved=%s",
            sanitize_for_logging(request.tool_name), approved,
        )
        return True

    def _record_from_future(self, request: ToolApprovalRequest) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if request.future.done() and not request.future.cancelled():
            try:
                result = request.future.result() or {}
            except Exception:
                result = {}
        approved = bool(result.get("approved"))
        return {
            "approved": approved,
            "arguments": result.get("arguments"),
            "reason": result.get("reason"),
            "invocation_id": request.invocation_id,
            "tool_call_id": request.tool_call_id,
            "run_id": request.run_id,
            "user_email": request.user_email,
            "status": "approved" if approved else "rejected",
        }

    def _public(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Outcome safe to hand back. The owner email stays on the record."""
        return {
            "already_resolved": True,
            "approved": bool(record.get("approved")),
            "arguments": record.get("arguments"),
            "reason": record.get("reason"),
            "invocation_id": record.get("invocation_id"),
            "tool_call_id": record.get("tool_call_id"),
            "run_id": record.get("run_id"),
            "status": record.get("status"),
        }

    def _owned(self, owner: Optional[str], user_email: str, tool_call_id: str) -> bool:
        # Security: verify the responding user owns this approval request.
        # Prevents cross-user approval bypass where one user approves
        # another user's pending tool call. Fail-closed: if the request has
        # a user_email binding, the response MUST supply a matching one.
        # Backward compat: only legacy requests (no user_email set) skip the
        # check so single-user deployments continue to work.
        if owner and normalize_user_email(owner) != normalize_user_email(user_email):
            # Inline sanitize for CodeQL py/log-injection: the query traces
            # explicit CR/LF removal as a log-injection sanitizer, but does
            # not recognize sanitize_for_logging() as one.
            safe_user_email = str(user_email).replace("\r", "").replace("\n", "")
            safe_tool_call_id = str(tool_call_id).replace("\r", "").replace("\n", "")
            logger.warning(
                "SECURITY: approval response rejected — user %s attempted to "
                "respond to tool call owned by a different user "
                "(call_id: %s)",
                safe_user_email,
                safe_tool_call_id,
            )
            return False
        return True

    def find_open_request(
        self,
        tool_call_id: Optional[str],
        invocation_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[ToolApprovalRequest]:
        """The still-registered request a response addressed, if there is one."""
        if invocation_id and invocation_id in self._pending_requests:
            return self._pending_requests[invocation_id]
        if not tool_call_id:
            return None
        matches = [
            request for request in self._pending_requests.values()
            if request.tool_call_id == tool_call_id
            and (not run_id or not request.run_id or request.run_id == run_id)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def cleanup_request(self, tool_call_id: str):
        """Remove a completed approval request.

        ``tool_call_id`` may be the provider id or the invocation id. An
        invocation-keyed request is still found by its provider id, which is
        what the executor's cleanup has always passed.
        """
        if tool_call_id in self._pending_requests:
            del self._pending_requests[tool_call_id]
            logger.debug("Cleaned up approval request: %s", tool_call_id)
            return
        # A bare provider id is only safe when one request has it. Two runs
        # can both be waiting on call_0; deleting every match would settle
        # the one this cleanup does not own.
        drop = [
            key for key, request in self._pending_requests.items()
            if request.tool_call_id == tool_call_id or request.invocation_id == tool_call_id
        ]
        if len(drop) != 1:
            return
        del self._pending_requests[drop[0]]
        logger.debug("Cleaned up approval request: %s", drop[0])

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
