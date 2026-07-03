"""Per-turn capture context propagated via a ContextVar.

The recorder lives at the edges of a chat turn (``ChatService``) but the raw
LLM input/output it wants is produced deep inside the LLM caller. Rather than
thread a capture sink through every mode runner and streaming generator, the
service activates a :class:`CaptureTurnContext` for the duration of the turn
and the LLM caller appends each call's I/O to it via :func:`record_llm_call`.

A ContextVar is the right tool here: the LLM call is awaited within the same
asyncio task that activated the context, so the value propagates without any
explicit plumbing, and turns on other connections never see each other's
context. When capture is off, :func:`current_capture_context` returns ``None``
and every hook is a single cheap branch with no behaviour change.
"""

from __future__ import annotations

import contextlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def normalize_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    """Normalize provider tool-call objects to ``{"name", "arguments"}`` dicts.

    Accepts the attribute-style objects litellm returns (both the streaming
    ``SimpleNamespace`` and non-streaming forms) as well as plain dicts.
    ``arguments`` is decoded from its JSON string into a dict when possible so
    the stored record is directly usable as training data; otherwise the raw
    string is preserved. Always returns a list and never raises.
    """
    if not raw:
        return []
    normalized: List[Dict[str, Any]] = []
    for tc in raw:
        try:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = (fn.get("name") if isinstance(fn, dict) else None) or tc.get("name", "")
                args = (fn.get("arguments") if isinstance(fn, dict) else None)
                if args is None:
                    args = tc.get("arguments")
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") if fn is not None else getattr(tc, "name", "")
                args = getattr(fn, "arguments", None) if fn is not None else getattr(tc, "arguments", None)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    pass
            normalized.append({"name": name or "", "arguments": args if args is not None else {}})
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to normalize tool call: %s", exc)
    return normalized

# Cap how much we accumulate in memory for a single turn. A runaway agent loop
# could otherwise pin a large transcript in RAM; past the cap we stop appending
# and flag the record as truncated rather than grow unbounded.
_MAX_LLM_CALLS_PER_TURN = 64


@dataclass
class CaptureTurnContext:
    """Mutable accumulator for one captured turn.

    Created by :class:`CaptureService` when both capture flags are on, activated
    for the turn, then flushed to storage. ``llm_calls`` collects the raw
    per-call I/O appended by the LLM caller.
    """

    turn_id: str
    conversation_id: str
    user_hash: str
    model: str
    temperature: Optional[float] = None
    consent: Dict[str, Any] = field(default_factory=dict)
    # Present only when this turn is a rollback correction; carries the rejected
    # trajectory the user is correcting plus their note.
    correction: Optional[Dict[str, Any]] = None
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def add_llm_call(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        content: str,
        tool_calls: List[Dict[str, Any]],
    ) -> None:
        if len(self.llm_calls) >= _MAX_LLM_CALLS_PER_TURN:
            self.truncated = True
            return
        self.llm_calls.append(
            {
                "messages": messages,
                "tools": tools_schema or [],
                "content": content,
                "tool_calls": tool_calls,
            }
        )


_capture_context: ContextVar[Optional[CaptureTurnContext]] = ContextVar(
    "atlas_capture_context", default=None
)


def current_capture_context() -> Optional[CaptureTurnContext]:
    """Return the active capture context for this task, or ``None``."""
    return _capture_context.get()


@contextlib.contextmanager
def capture_turn(context: CaptureTurnContext) -> Iterator[CaptureTurnContext]:
    """Activate ``context`` for the duration of a turn, then restore."""
    token = _capture_context.set(context)
    try:
        yield context
    finally:
        _capture_context.reset(token)


def record_llm_call(
    messages: List[Dict[str, Any]],
    tools_schema: Optional[List[Dict[str, Any]]],
    content: str,
    tool_calls: List[Dict[str, Any]],
) -> None:
    """Append one LLM call's I/O to the active capture context, if any.

    Called from the LLM caller on every tool-capable completion. Defensive: any
    failure here must never break the actual chat turn, so all exceptions are
    swallowed with a debug log.
    """
    ctx = _capture_context.get()
    if ctx is None:
        return
    try:
        ctx.add_llm_call(
            _sanitize_messages(messages),
            tools_schema,
            content or "",
            normalize_tool_calls(tool_calls),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Capture record_llm_call failed: %s", exc)


def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shallow-copy the message list into JSON-safe plain dicts.

    The caller's list is reused across rounds and mutated in place, so we copy
    each message; tool-call fragments stored as attribute objects are
    normalized to dicts so the snapshot serializes cleanly.
    """
    out: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        copy = dict(msg)
        if copy.get("tool_calls"):
            copy["tool_calls"] = normalize_tool_calls(copy["tool_calls"])
        out.append(copy)
    return out
