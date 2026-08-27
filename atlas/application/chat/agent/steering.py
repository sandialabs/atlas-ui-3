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
from typing import Any


class SteeringChannel:
    """Conduit for user steering messages to a running agent loop.

    The channel is transport-owned and loop-consumed. It is intentionally a
    small, explicit object rather than a bare ``asyncio.Queue`` so the
    ``active`` flag gives the transport a single, authoritative answer to "is a
    loop actually consuming right now?" without guessing from the request
    payload.
    """

    def __init__(self) -> None:
        self.queue: "asyncio.Queue[Any]" = asyncio.Queue()
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
