"""One operation per run-control entry point, built on ``resolve`` (issue #1128).

Each function locates the addressed run with :func:`resolve` and then either
acts on this replica's registry or sends the command to the owner. The
handlers in ``main.py`` and the agent-runs routes call these and nothing else.
Who may act on the run is still decided on the owner's side.
"""

from __future__ import annotations

import logging
from typing import Optional

from atlas.application.chat.runs.control_bus import OWNER_UNAVAILABLE, reply
from atlas.application.chat.runs.remote_run_frames import (
    stop_frame,
)
from atlas.application.chat.runs.resolve import Address, Local, Remote, Unknown, resolve
from atlas.application.chat.runs.run_control import (
    route_stop,
    user_actor,
)
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

_DIRECTORY_UNAVAILABLE_MESSAGE = "The shared run directory could not be read."


async def stop_addressed_run(websocket, registry, user_email: str, data: dict,
                             active_chat_task: Optional[dict] = None) -> None:
    """Stop the run a ``stop_streaming`` / ``agent_control`` stop frame names.

    The run is the user's own, by ``run_id`` and then ``conversation_id``, on
    this replica or on its owner. A frame that names a run but finds none is a
    no-op: a stale id must never stop an unrelated untracked turn. Only a frame
    that names no run (an older client), or names a conversation nothing is
    running in, falls back to the connection's untracked turn.
    """
    address = Address(data.get("run_id"), data.get("conversation_id"))
    resolution = await resolve(registry, user_actor(user_email), address, include_terminal=True)

    if isinstance(resolution, Local):
        logger.info("Cancelling run %s on user request", resolution.record.run_id)
        registry.cancel(resolution.record.run_id, user_email)
        return
    if isinstance(resolution, Remote) and resolution.entry.get("user_email") == normalize_user_email(user_email):
        # Another replica owns it; the client sees the frames a local stop sends.
        answer = await route_stop(resolution.entry, user_email)
        frame = stop_frame(answer, data)
        if frame is not None:
            await websocket.send_json(frame)
        return

    task = (active_chat_task or {}).get("task")
    task_live = task is not None and not task.done()
    if isinstance(resolution, Unknown):
        # The owner is unknown. A frame naming a run id is told so; a
        # conversation-only frame still stops the untracked turn it may mean.
        if address.run_id or not task_live:
            frame = stop_frame(reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE), data)
            await websocket.send_json(frame)
            return
    elif address.run_id:
        # Addressed a run that is finished, reaped, or not the user's.
        return
    # Nothing addressed: the untracked turn (removed once every turn is a run).
    if task_live:
        logger.info("Cancelling active chat task (stop frame)")
        task.cancel()
