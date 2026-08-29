"""Persist tool-call input/output into conversation history (issue #684).

Tool calls surface in the UI through transient WebSocket events
(``tool_start`` / ``tool_complete`` / ``tool_error``); they were never written
to the session's :class:`ConversationHistory`, so the tool name, input
arguments, and output result all vanished when a saved conversation was
reloaded or exported.

:class:`ToolCallRecorder` wraps the turn's ``update_callback`` so it observes
those same already-UI-sanitized payloads as they stream to the client, then
turns them into display-only ``tool_call`` :class:`Message` records. Reusing
the emitted payloads (rather than the raw :class:`ToolResult`) keeps the
persisted view byte-for-byte consistent with what the user saw live, including
the token/filename sanitization applied before display.

The resulting messages are role ``tool`` and carry ``message_type=tool_call``
metadata, so they are excluded from
:meth:`ConversationHistory.get_messages_for_llm` and never replayed to the
model as conversation turns.

They are not entirely invisible to the model, though: since issue #755 these
rows are the source for the capped, explicitly-delimited tool digest built by
:mod:`atlas.application.chat.utilities.agent_digest`, which quotes their
arguments and results as untrusted data on the turn's closing assistant
message. Nothing here is replayed verbatim as a ``tool`` message.
"""

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.messages.models import ConversationHistory, Message, MessageRole
from atlas.modules.mcp_tools.atlas_server import CANVAS_TOOL_NAME, normalize_tool_name

logger = logging.getLogger(__name__)

UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]

# Persisted tool I/O is stored in ``conversation_messages.metadata_json``. A tool
# invocation can carry a base64 file upload as input or emit a very large output,
# which would bloat the saved conversation / DB row indefinitely. Cap individual
# string values so persistence stays bounded; the live UI event is forwarded
# untouched, so only what is written to history is elided (matching the
# display-side elision the frontend already applies on export).
_MAX_STR_CHARS = 8000
# Stop walking absurdly deep structures; anything past this is stored as-is.
_MAX_DEPTH = 6

# Total budget for announcing every interrupted call. A half-open socket can
# accept a write that never completes, and the unwind must not park on a
# best-effort notification.
_NOTIFY_BUDGET_SECONDS = 2.0

# Shown in the stopped tool row, live and after a reload.
_INTERRUPTED_RESULT = "Stopped before the tool result was recorded."


def _elide_for_storage(value: Any, depth: int = 0) -> Any:
    """Recursively cap large string values so persisted tool I/O stays bounded."""
    if isinstance(value, str):
        if len(value) > _MAX_STR_CHARS:
            dropped = len(value) - _MAX_STR_CHARS
            return value[:_MAX_STR_CHARS] + f"…[truncated {dropped} chars]"
        return value
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: _elide_for_storage(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_elide_for_storage(v, depth + 1) for v in value]
    return value


class ToolCallRecorder:
    """Wrap an update callback and capture tool-call events for persistence."""

    def __init__(self, inner: Optional[UpdateCallback]):
        self._inner = inner
        # Keyed by tool_call_id, insertion-ordered so persisted rows match the
        # order the tools were invoked in this turn.
        self._calls: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # Payloads queued by a mark_incomplete flush, drained by
        # notify_incomplete() once the history write is done.
        self._interrupted_notices: List[Dict[str, Any]] = []

    async def __call__(self, payload: Dict[str, Any]) -> None:
        # Record defensively: a malformed payload must never break the actual
        # event delivery the UI depends on.
        try:
            if isinstance(payload, dict):
                self._record(payload)
        except Exception:  # pragma: no cover - belt and suspenders
            # Fail open so UI event delivery never breaks, but leave a trace so a
            # silent persistence gap is diagnosable at production log levels.
            logger.warning("ToolCallRecorder failed to record a tool event", exc_info=True)
        if self._inner is not None:
            await self._inner(payload)

    def _record(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("type")
        tool_call_id = payload.get("tool_call_id")
        if not tool_call_id or event_type not in (
            "tool_start", "tool_complete", "tool_error", "auth_required",
        ):
            return
        # The canvas tool renders into the canvas panel, not the transcript; the
        # UI suppresses it as a chat row, so it must not be persisted as one.
        if normalize_tool_name(payload.get("tool_name")) == CANVAS_TOOL_NAME:
            return

        entry = self._calls.setdefault(tool_call_id, {"tool_call_id": tool_call_id})
        if payload.get("tool_name"):
            entry["tool_name"] = payload["tool_name"]

        if event_type == "tool_start":
            entry["server_name"] = payload.get("server_name")
            entry["arguments"] = payload.get("arguments")
            entry.setdefault("status", "calling")
        elif event_type == "tool_complete":
            entry["result"] = payload.get("result")
            entry["status"] = "completed" if payload.get("success") else "failed"
        elif event_type == "tool_error":
            entry["result"] = payload.get("error")
            entry["status"] = "failed"
        elif event_type == "auth_required":
            # The executor aborts the call after emitting this (no
            # tool_complete/tool_error follows), so treat it as terminal:
            # without this the row would persist as status="calling" and
            # reload as a forever-in-progress tool.
            entry["result"] = payload.get("message") or "Authentication required"
            entry["status"] = "failed"

    def messages(self) -> List[Message]:
        """Build display-only ``tool_call`` messages from recorded events."""
        out: List[Message] = []
        for entry in self._calls.values():
            tool_name = entry.get("tool_name")
            if not tool_name:
                # A bare progress/error event with no start: nothing renderable.
                continue
            metadata = {
                "message_type": "tool_call",
                "tool_call_id": entry.get("tool_call_id"),
                "tool_name": tool_name,
                "server_name": entry.get("server_name") or "tool",
                "arguments": _elide_for_storage(entry.get("arguments") or {}),
                "result": _elide_for_storage(entry.get("result")),
                "status": entry.get("status") or "completed",
            }
            out.append(Message(
                role=MessageRole.TOOL,
                content=f"Tool call: {tool_name}",
                metadata=metadata,
            ))
        return out

    async def notify_incomplete(self) -> None:
        """Tell the UI about calls the last flush closed out as interrupted.

        The live row was created on ``tool_start`` and nothing else arrives on
        the cancel path, so without this it spins as "CALLING" until a reload
        replaces it with the persisted ``interrupted`` row -- the live view
        contradicting the saved one (issue #755).

        Queued by ``flush(mark_incomplete=True)`` and drained here, so
        persistence never waits on a socket. The whole drain shares one
        deadline: a concurrent round can leave many calls pending, and a
        per-write timeout would multiply into a stall long enough to outlast
        the reset_session wait that keeps ``conversation_saved`` ahead of
        ``session_reset``.
        """
        notices, self._interrupted_notices = self._interrupted_notices, []
        if self._inner is None or not notices:
            return
        deadline = asyncio.get_event_loop().time() + _NOTIFY_BUDGET_SECONDS
        for payload in notices:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Interrupt announcement budget exhausted; %d tool row(s) "
                    "will show as in-progress until reload",
                    len(notices) - notices.index(payload),
                )
                return
            try:
                await asyncio.wait_for(self._inner(payload), timeout=remaining)
            except (Exception, asyncio.CancelledError):
                # CancelledError is listed explicitly because it is not an
                # Exception: the frontend sends stop_streaming then
                # reset_session, so a second cancel can land mid-write, and
                # letting it through would replace the exception being unwound
                # -- which may be a real failure, not a user stop. Interpreter
                # signals (KeyboardInterrupt, SystemExit) are deliberately
                # still allowed to propagate.
                logger.warning(
                    "Could not announce interrupted tool call %s; its row will "
                    "show as in-progress until reload",
                    payload.get("tool_call_id"),
                    exc_info=True,
                )
                continue

    async def unwind(self, history: ConversationHistory) -> None:
        """Close out a turn that is being cancelled: persist, then announce.

        The order is the contract. ``flush`` is synchronous and cannot block;
        the announcement is a socket write a half-open connection can park on.
        Losing the announcement costs a spinning row until reload -- losing the
        flush costs the turn's completed work.
        """
        try:
            self.flush(history, mark_incomplete=True)
        except Exception:  # pragma: no cover - never mask the real failure
            logger.warning("Failed to flush tool calls while unwinding", exc_info=True)
        await self.notify_incomplete()

    def flush(self, history: ConversationHistory, mark_incomplete: bool = False) -> None:
        """Append recorded tool-call messages to a history, then reset.

        Call immediately before adding the turn's final assistant message so
        the persisted order is ``user -> tool_call(s) -> assistant``. Clearing
        afterwards makes repeated flushes within a turn idempotent.

        ``mark_incomplete`` closes out calls that never reported a result --
        the turn was stopped or the connection dropped mid-execution
        (issue #755). Without it those rows persist as ``status="calling"`` and
        reload as a tool that is forever in progress.
        """
        if mark_incomplete:
            for entry in self._calls.values():
                if entry.get("status") in (None, "calling"):
                    if entry.get("tool_name"):
                        # Queued for notify_incomplete(), which the caller
                        # awaits once persistence is safely done.
                        self._interrupted_notices.append({
                            "type": "tool_interrupted",
                            "tool_call_id": entry["tool_call_id"],
                            "tool_name": entry["tool_name"],
                            "status": "interrupted",
                            "result": _INTERRUPTED_RESULT,
                        })
                    # A distinct status, not "failed": the user stopping their
                    # own turn is not a tool error, and the UI renders the two
                    # differently.
                    entry["status"] = "interrupted"
                    entry.setdefault("result", _INTERRUPTED_RESULT)
        for message in self.messages():
            history.add_message(message)
        self._calls.clear()
