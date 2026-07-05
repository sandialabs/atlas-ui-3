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
model.
"""

import logging
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.messages.models import ConversationHistory, Message, MessageRole

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

    async def __call__(self, payload: Dict[str, Any]) -> None:
        # Record defensively: a malformed payload must never break the actual
        # event delivery the UI depends on.
        try:
            if isinstance(payload, dict):
                self._record(payload)
        except Exception:  # pragma: no cover - belt and suspenders
            # Fail open so UI event delivery never breaks, but leave a trace so a
            # silent persistence gap is diagnosable.
            logger.debug("ToolCallRecorder failed to record a tool event", exc_info=True)
        if self._inner is not None:
            await self._inner(payload)

    def _record(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("type")
        tool_call_id = payload.get("tool_call_id")
        if not tool_call_id or event_type not in ("tool_start", "tool_complete", "tool_error"):
            return
        # The canvas tool renders into the canvas panel, not the transcript; the
        # UI suppresses it as a chat row, so it must not be persisted as one.
        if payload.get("tool_name") == "canvas_canvas":
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

    def flush(self, history: ConversationHistory) -> None:
        """Append recorded tool-call messages to a history, then reset.

        Call immediately before adding the turn's final assistant message so
        the persisted order is ``user -> tool_call(s) -> assistant``. Clearing
        afterwards makes repeated flushes within a turn idempotent.
        """
        for message in self.messages():
            history.add_message(message)
        self._calls.clear()
