"""Per-turn trusted context for hook events, propagated via a ContextVar.

Most hook points fire where the session is already in hand, so they fill
``session_id`` / ``user_email`` / ``conversation_id`` / ``compliance_level``
directly. RAG is the exception: ``UnifiedRAGService.query_rag`` is reached
through the LLM caller, which deliberately knows nothing about sessions, and
threading the turn's identity through every mode runner, streaming generator,
and caller signature would spread session state across layers that have stayed
free of it.

A ContextVar is the same tool the capture pipeline uses for the same reason
(see ``application/chat/capture/capture_context.py``): the retrieval call is
awaited inside the task that activated the context -- and ``asyncio`` copies
the context into any task spawned from it, so the parallel per-source fan-out
inherits it too -- while turns on other connections never observe each other's
values. When no context is active, events simply carry ``None``, exactly as
before.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional


@dataclass(frozen=True)
class HookTurnContext:
    """The trusted, server-side identity of the turn currently running."""

    session_id: Optional[str] = None
    user_email: Optional[str] = None
    conversation_id: Optional[str] = None
    compliance_level: Optional[Any] = None

    def as_event_fields(self) -> Dict[str, Any]:
        """Return the ``HookEvent`` base-field kwargs this context supplies."""
        return {
            "session_id": self.session_id,
            "user_email": self.user_email,
            "conversation_id": self.conversation_id,
            "compliance_level": self.compliance_level,
        }


_hook_turn_context: ContextVar[Optional[HookTurnContext]] = ContextVar(
    "atlas_hook_turn_context", default=None
)


def current_hook_context() -> Optional[HookTurnContext]:
    """Return the active turn context for this task, or ``None``."""
    return _hook_turn_context.get()


@contextlib.contextmanager
def hook_turn(context: HookTurnContext) -> Iterator[HookTurnContext]:
    """Activate *context* for the duration of a turn, then restore."""
    token = _hook_turn_context.set(context)
    try:
        yield context
    finally:
        _hook_turn_context.reset(token)
