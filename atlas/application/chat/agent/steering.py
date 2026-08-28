"""Steering channel for injecting user messages into a running agent loop.

Issue #824: while the native agentic loop is mid-run, a user message the user
sends should reach the LLM at the next iteration boundary -- as a normal user
turn -- without breaking or stopping the loop. This lets the user steer an
agent that is already working.

The transport (WebSocket endpoint) creates a :class:`SteeringChannel` for an
agent-mode chat turn and passes it down through the service layer to the loop.
The loop drains ``queue`` at each iteration boundary; the transport pushes the
user's text into it while a loop is actively consuming.

``active`` is flipped on by the agent runner right before the loop starts and
off when it finishes (including cancel/error), so the transport only steers when
a loop is genuinely draining. Without that gate, a turn that *requested* agent
mode but fell back to a non-agent turn (e.g. the selected model lacks tool
support) would never drain the queue and a steering message pushed into it would
be silently lost.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

# Bound on how many steering messages may be queued for one agent turn. The
# channel is drained at each iteration boundary, so in normal use this holds at
# most a couple of messages; the bound just stops a long-running turn from being
# flooded with queued turns that all land in history and in every subsequent
# prompt (issue #824 review).
STEERING_QUEUE_MAXSIZE = 8


class SteeringChannel:
    """Conduit for user steering messages to a running agent loop.

    The channel is transport-owned and loop-consumed. It is intentionally a
    small, explicit object rather than a bare ``asyncio.Queue`` so the
    ``active`` flag gives the transport a single, authoritative answer to "is a
    loop actually consuming right now?" without guessing from the request
    payload.
    """

    def __init__(self) -> None:
        self.queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=STEERING_QUEUE_MAXSIZE)
        self.active: bool = False

    def activate(self) -> None:
        """Mark a loop as actively consuming this channel.

        Called by the agent runner immediately before handing control to the
        loop, and only then, so activation is proof that the loop will drain.
        """
        self.active = True

    def deactivate(self) -> None:
        """Mark the channel as no longer consumed.

        Called from the runner's cleanup path so a later chat on the same
        connection never steers into a queue whose loop has already exited.
        """
        self.active = False

    def has_pending(self) -> bool:
        """Whether any steering message is waiting to be drained."""
        return not self.queue.empty()

    def drain_leftovers(self) -> list:
        """Non-blocking drain of whatever the loop never consumed.

        Used by the agent runner after a turn ends: any message still queued
        arrived too late to be injected (the loop had stopped draining), and is
        surfaced to the user instead of being silently lost (issue #824).
        """
        leftovers = []
        while True:
            try:
                leftovers.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return leftovers


def should_steer(
    active_chat_task: Dict[str, Any],
    frame_conversation_id: Optional[str],
) -> bool:
    """Whether a new chat frame should be routed into the running agent's channel.

    Pure and transport-agnostic so the routing decision is unit-testable. The
    transport keys ``active_chat_task`` with ``task`` (an ``asyncio.Task`` or
    ``None``), ``steering`` (a ``SteeringChannel`` or ``None``), and
    ``conversation_id`` (the running turn's conversation id, or ``None``).

    Only steer when a loop is genuinely consuming (task live, channel active)
    AND the frame is for the same conversation the loop is running in -- so a
    message typed after the user switched conversations starts a fresh turn in
    the new conversation instead of being injected into the old one's context
    and persisted in its transcript (issue #824 review).
    """
    task = active_chat_task.get("task")
    steering = active_chat_task.get("steering")
    if task is None or steering is None or not steering.active:
        return False
    done = getattr(task, "done", None)
    if done is not None and done():
        return False
    if active_chat_task.get("conversation_id") != frame_conversation_id:
        return False
    return True
