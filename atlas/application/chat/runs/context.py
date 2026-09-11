"""Ambient identity of the run currently executing (issue #884).

Tagging events at the transport callback only covers the events the transport
itself forwards. The agent loop publishes most of its output -- tokens, agent
updates, citations, completion -- through the ChatService's shared
``WebSocketEventPublisher``, which is owned by the *connection*, not by a run.
With two runs sharing one socket, those frames would arrive untagged and the
client would apply them to whichever conversation happened to be visible.

Threading a run id through every publisher call site would touch most of the
chat pipeline. A context variable does the same job at the one place it
matters, because ``asyncio.Task`` copies the current context when it is
created: each run's task gets its own binding, and every coroutine it awaits --
however deep -- reads that run's identity and nobody else's. The transport
adapter stamps outgoing frames from it.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, NamedTuple, Optional


class RunContext(NamedTuple):
    run_id: str
    conversation_id: str


_current_run: ContextVar[Optional[RunContext]] = ContextVar("atlas_current_run", default=None)


def set_current_run(run_id: str, conversation_id: str) -> None:
    """Bind this task's context to a run.

    Called from inside the run's own task, so the binding cannot leak to the
    receive loop or to another run.
    """
    _current_run.set(RunContext(run_id=run_id, conversation_id=conversation_id))


def get_current_run() -> Optional[RunContext]:
    """The run executing in this context, if any."""
    return _current_run.get()


def clear_current_run() -> None:
    """Drop the binding (used by tests)."""
    _current_run.set(None)


def tag_event(
    data: Dict[str, Any],
    run_id: str,
    conversation_id: str,
    *,
    copy: bool = True,
) -> Dict[str, Any]:
    """Stamp an outbound event with the run that produced it (issue #884).

    The single authority for the tagging rule; every caller goes through here
    so the rule cannot drift between code paths (issue #915).

    With several conversations executing at once, a bare event is ambiguous:
    the client cannot tell whether a token, tool row, file, or completion
    belongs to the conversation on screen or to one running in the background.
    Every event a tracked run emits carries both ids so the client can route it
    -- and, crucially, so it can *drop* events for a conversation it is not
    displaying instead of splicing them into the visible transcript.

    Existing ids are never overwritten: a producer deeper in the pipeline that
    already knows its own conversation is more authoritative than the run
    envelope.

    ``copy`` picks between the two call sites' needs. The turn callback is
    handed an event it does not own, so it copies. The publisher builds each
    frame fresh per send and stamps in place, because copying every token event
    would be wasteful for no benefit.
    """
    if not isinstance(data, dict):
        return data
    tagged = dict(data) if copy else data
    tagged.setdefault("run_id", run_id)
    tagged.setdefault("conversation_id", conversation_id)
    return tagged


def stamp_with_current_run(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add run_id/conversation_id to an outgoing frame, if a run is executing.

    Resolves the ambient run and delegates the stamping rule to
    :func:`tag_event`, in place -- see its ``copy`` note for why.
    """
    context = _current_run.get()
    if context is None:
        return data
    return tag_event(data, context.run_id, context.conversation_id, copy=False)
