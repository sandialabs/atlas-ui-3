"""One operation per run-control entry point, built on ``resolve`` (issue #1128).

Each function locates the addressed run with :func:`resolve` and then either
acts on this replica's registry or sends the command to the owner. The
handlers in ``main.py`` and the agent-runs routes call these and nothing else.
Who may act on the run is still decided on the owner's side.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from atlas.application.chat.runs import external_runs
from atlas.application.chat.runs.control_bus import OWNER_UNAVAILABLE, get_run_control_bus, reply
from atlas.application.chat.runs.eligibility import turn_is_off_record
from atlas.application.chat.runs.remote_run_frames import (
    directory_unavailable_frame,
    steer_frame,
    stop_frame,
)
from atlas.application.chat.runs.resolve import Address, Local, Remote, Unknown, resolve
from atlas.application.chat.runs.run_control import (
    SERVICE_ACTOR,
    route_api_cancel,
    route_api_follow_up_cancel,
    route_approval,
    route_auto_approve,
    route_steer,
    route_stop,
    user_actor,
)
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

_DIRECTORY_UNAVAILABLE_MESSAGE = "The shared run directory could not be read."
_APPROVAL_NUDGES: "set[asyncio.Task]" = set()


async def stop_addressed_run(websocket, registry, user_email: str, data: dict,
                             connection_session_id: Any = None) -> None:
    """Stop the run a ``stop_streaming`` / ``agent_control`` stop frame names.

    The run is the user's own, by ``run_id`` and then ``conversation_id``, on
    this replica or on its owner. A frame that finds no run is a no-op: it
    finished, was reaped, or the id is stale, and a stale id must never stop an
    unrelated run. A frame that names nothing (an older client, or New Chat's
    stop before its reset) means the plain turn running on this connection's
    own session, when there is one.
    """
    address = Address(data.get("run_id"), data.get("conversation_id"))
    if not address.run_id and not address.conversation_id and connection_session_id is not None:
        bound = registry.active_bound_to(connection_session_id)
        if bound:
            address = Address(bound[0].run_id, None)
    resolution = await resolve(registry, user_actor(user_email), address, include_terminal=True)

    if isinstance(resolution, Local):
        logger.info("Cancelling run %s on user request", resolution.record.run_id)
        registry.cancel(resolution.record.run_id, user_email)
        return
    if isinstance(resolution, Remote) and resolution.entry.get("user_email") == normalize_user_email(user_email):
        # Another replica owns it; the client sees the frames a local stop sends.
        answer = await route_stop(resolution.entry, user_email)
        if answer.get("code") != OWNER_UNAVAILABLE:
            frame = stop_frame(answer, data)
            if frame is not None:
                await websocket.send_json(frame)
            return
        # The owner did not answer: the same as an unreadable directory, except
        # that the run named is known to be elsewhere. A frame that named it
        # still stops the conversation's run held here rather than nothing.
        local = registry.active_for_conversation(address.conversation_id, user_email) if address.run_id else None
        if local is not None:
            logger.info("Cancelling run %s on user request (owner unavailable)", local.run_id)
            registry.cancel(local.run_id, user_email)
            return
        await websocket.send_json(stop_frame(answer, data))
    elif isinstance(resolution, Unknown):
        await websocket.send_json(stop_frame(reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE), data))
    # Anything else is no such run (finished, reaped, another user's): nothing to stop.


async def steer_addressed_run(websocket, registry, user_email: str, data: dict) -> bool:
    """Steer the run another replica executes for a chat frame's conversation.

    Returns whether the frame was fully answered. ``False`` means no live run
    holds the conversation anywhere (or the one that did has just finished),
    so the caller handles the message as a new turn here. An unreadable
    directory and another user's run both refuse the turn rather than start
    one beside a run that may be executing elsewhere.
    """
    conversation_id = data.get("conversation_id")
    resolution = await resolve(registry, user_actor(user_email), Address(None, conversation_id))
    if isinstance(resolution, Unknown):
        await websocket.send_json(directory_unavailable_frame(conversation_id))
        return True
    if not isinstance(resolution, Remote):
        return False
    entry = resolution.entry
    if entry.get("user_email") != normalize_user_email(user_email):
        # The cross-replica form of the local guard against another user's run.
        logger.warning(
            "WS refused a turn naming conversation=%s while another "
            "user's run is executing under it on another replica",
            sanitize_for_logging(conversation_id),
        )
        await websocket.send_json({
            "type": "error",
            "message": "Conversation not found",
            "error_type": "authorization",
            "conversation_id": conversation_id,
        })
        return True
    answer = await route_steer(
        entry,
        user_email,
        data.get("content", ""),
        data.get("compliance_level_filter"),
        request_id=data.get("request_id"),
        # Incognito / not-saved content never goes to the shared inbox; the
        # steer is refused instead.
        off_record=turn_is_off_record(data),
    )
    frame = steer_frame(answer, entry)
    if frame is None:
        return False
    await websocket.send_json(frame)
    return True


async def cancel_api_run_addressed(run_id: str) -> Dict[str, Any]:
    """The agent-runs API's cancel: a run-control reply carrying the run's status.

    Any replica accepts the request. A run another replica is executing is
    stopped there. Raises :class:`LaunchRefused` for an id nobody holds (or one
    that was not started through the API) and for refusals the local cancel
    raises; an owner that did not answer, or an unreadable directory, is a
    failed reply the caller maps to a retryable error.
    """
    registry = external_runs.get_run_registry()
    registry.reap_terminal()
    resolution = await resolve(registry, SERVICE_ACTOR, Address(run_id, None), include_terminal=True)
    if isinstance(resolution, Remote):
        return await route_api_cancel(resolution.entry)
    if isinstance(resolution, Unknown):
        return reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE)
    # Nothing is not found: cancel_api_run raises the refusal.
    named = resolution.record if isinstance(resolution, Local) else None
    # Read before cancelling: the cancel marks a live record terminal.
    finished = named is not None and named.is_terminal
    status = external_runs.cancel_api_run(run_id)
    if not finished or status.get("run_id") != run_id:
        return reply(True, status=status)
    # The named run had finished here and no follow-up of it is live here; one
    # may be executing on another replica.
    later = await route_api_follow_up_cancel(run_id, named.conversation_id, named.user_email, status)
    return later if later is not None else reply(True, status=status)


async def approval_nudge(registry, user_email: str, outcome: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wake the executor of an approval this replica just recorded.

    Returns the owner's answer, or None when there is nobody to wake (no run
    named, finished, on this replica, not the user's, or the directory
    unreadable): the executor also polls the shared outcome, so a missing wake
    only delays the tool by one poll interval.
    """
    if not isinstance(outcome, dict) or not outcome.get("run_id"):
        return None
    resolution = await resolve(registry, user_actor(user_email), Address(outcome.get("run_id"), None))
    if not isinstance(resolution, Remote):
        return None
    return await route_approval(
        resolution.entry, user_email, outcome.get("invocation_id"), outcome.get("tool_call_id"),
    )


def nudge_approval(registry, user_email: str, outcome: Optional[Dict[str, Any]]) -> None:
    """Fire and forget :func:`approval_nudge`; a lost nudge only delays the tool."""
    if not isinstance(outcome, dict) or not outcome.get("run_id"):
        return

    async def _nudge() -> None:
        try:
            answer = await approval_nudge(registry, user_email, outcome)
            if answer is not None and not answer.get("ok"):
                logger.info("approval nudge not applied: %s", answer.get("code"))
        except Exception:
            logger.warning("approval nudge failed", exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(_nudge())
    except RuntimeError:
        return
    _APPROVAL_NUDGES.add(task)
    task.add_done_callback(_APPROVAL_NUDGES.discard)


async def set_auto_approve(registry, user_email: str, enabled: bool) -> None:
    """Apply the user's auto-approve switch here, then on every other owner.

    The switch belongs to the user: it is set on the user-started runs this
    replica holds, then sent to each distinct other replica that owns one of
    the user's live runs (found through the shared per-user run set). Best
    effort: an unreadable directory or an owner that does not answer is
    logged, never surfaced; a run that starts later takes the flag from its
    own admission frame.
    """
    registry.set_auto_approve_user_tools(user_email, enabled)
    bus = get_run_control_bus()
    if bus is None:
        return
    try:
        entries = await bus.runs_for_user(user_email)
    except Exception:
        logger.warning("auto-approve switch not sent to other replicas: directory unreadable", exc_info=True)
        return
    owners: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if entry.get("replica") != bus.replica_id:
            owners.setdefault(entry["replica"], entry)
    if not owners:
        return
    answers = await asyncio.gather(
        *(route_auto_approve(entry, user_email, enabled) for entry in owners.values()),
        return_exceptions=True,
    )
    for replica, answer in zip(owners, answers):
        if isinstance(answer, BaseException):
            logger.warning("auto-approve switch failed for replica %s", sanitize_for_logging(replica), exc_info=answer)
        elif not answer.get("ok"):
            logger.info("auto-approve switch not applied on replica %s: %s",
                        sanitize_for_logging(replica), answer.get("code"))
