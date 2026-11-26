"""Background-task support for MCP tool calls (SEP-2663 tasks extension).

FastMCP 4 drives a server's background tasks transparently: when a server
answers ``tools/call`` with a task, the client extension polls ``tasks/get``
until the task finishes and hands back the tool's ordinary result. Atlas adds
two things the stock extension does not do:

- it tells the caller that the call became a task, so a long-running one can
  be announced to the UI, and
- it sends ``tasks/cancel`` when the waiting call is cancelled or times out.
  Without that a stopped run leaves its task executing on the server.

Tasks exist only on MCP 2026-07-28 connections; on a handshake-era server the
extension is inert and tools run synchronously.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from fastmcp.client.elicitation import create_elicitation_callback
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from fastmcp_tasks.client import TasksClientExtension
from fastmcp_tasks.client_models import CancelTaskRequest, CancelTaskRequestParams
from mcp.client.extension import ClientExtension, ResultClaim
from mcp_types import Result

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

# How long to wait for the server to acknowledge tasks/cancel. The cancel is
# best effort: the caller is already unwinding and must not hang on it.
TASK_CANCEL_TIMEOUT_SECONDS = 5.0


class TaskCallTracker:
    """Records whether the tool call running in the current task was tasked."""

    def __init__(self) -> None:
        self.task_id: Optional[str] = None
        self.created = asyncio.Event()

    def task_created(self, task_id: str) -> None:
        self.task_id = task_id
        self.created.set()


_ACTIVE_TASK_TRACKER: contextvars.ContextVar[Optional[TaskCallTracker]] = contextvars.ContextVar(
    "mcp_active_task_tracker", default=None
)


@contextmanager
def track_task_call(tracker: TaskCallTracker) -> Iterator[TaskCallTracker]:
    """Bind ``tracker`` to the tool call made inside this block."""
    token = _ACTIVE_TASK_TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_TASK_TRACKER.reset(token)


async def cancel_remote_task(session: Any, task_id: str) -> None:
    """Ask the server to cancel ``task_id``. Best effort: never raises."""
    try:
        request = CancelTaskRequest(params=CancelTaskRequestParams(task_id=task_id))
        await asyncio.wait_for(
            session.send_request(request, Result),
            timeout=TASK_CANCEL_TIMEOUT_SECONDS,
        )
        logger.info("Cancelled MCP background task %s", sanitize_for_logging(task_id))
    except Exception:
        logger.warning(
            "Could not cancel MCP background task %s",
            sanitize_for_logging(task_id),
            exc_info=True,
        )


class AtlasTasksClientExtension(ClientExtension):
    """The FastMCP tasks client extension, plus tracking and cancellation.

    Declares the same identifier as FastMCP's built-in extension, which makes
    FastMCP use this one in its place.
    """

    identifier = TASKS_EXTENSION_ID

    def __init__(self, elicitation_handler: Any = None) -> None:
        callback = (
            create_elicitation_callback(elicitation_handler)
            if elicitation_handler is not None
            else None
        )
        self._inner = TasksClientExtension(callback)

    def settings(self) -> dict[str, Any]:
        return self._inner.settings()

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return tuple(
            dataclasses.replace(claim, resolve=self._tracking(claim.resolve))
            for claim in self._inner.claims()
        )

    @staticmethod
    def _tracking(resolve: Any) -> Any:
        async def resolve_tracked(create_result: Any, ctx: Any) -> Any:
            task_id = create_result.task_id
            tracker = _ACTIVE_TASK_TRACKER.get()
            if tracker is not None:
                tracker.task_created(task_id)
            try:
                return await resolve(create_result, ctx)
            except (asyncio.CancelledError, TimeoutError):
                # Shielded so the request still goes out while this task is
                # being torn down; the original cancellation is re-raised.
                try:
                    await asyncio.shield(cancel_remote_task(ctx.session, task_id))
                except asyncio.CancelledError:
                    # A second cancellation while the cancel request is in
                    # flight; the original exception is re-raised below.
                    pass
                raise

        return resolve_tracked
