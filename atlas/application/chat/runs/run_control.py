"""Stop, steer and approve a run on the replica that owns it (issue #1128).

Two halves share this module:

* **Owner side.** :class:`RunControlService` executes commands that arrive on
  this replica's inbox (see ``control_bus``). It re-checks everything against
  its own registry: the command's actor must own the run (or be the trusted
  agent-runs service, for an API-started run), and the run must still be
  live here. The directory entry that routed the command is never trusted
  for authorization.
* **Sender side.** ``route_*`` send a command to the owner of a run that
  ``resolve`` found in the shared directory (see ``resolve`` and ``commands``);
  they do no lookup of their own. A directory that cannot be read is never
  taken for "nothing matches": ``resolve`` answers ``Unknown`` and the callers
  report ``owner_unavailable``, so a Redis outage cannot start a second turn
  beside a run another replica is executing.

Commands are idempotent. Stop is (terminal is sticky). A steer is queued once
per request id. An approval is settled once by the shared write-once outcome
(issue #1122); the command only wakes the owner's waiting tool so it does not
wait for its next poll, and a repeat finds the future already resolved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import UUID

from atlas.application.chat.runs.control_bus import (
    COMMAND_EXPIRED,
    OWNER_UNAVAILABLE,
    STALE_GENERATION,
    get_run_control_bus,
    reply,
)
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

STOP = "stop"
STEER = "steer"
APPROVAL = "approval"
AUTO_APPROVE = "auto_approve"

RefusalCheck = Callable[[UUID, Any], Awaitable[Optional[str]]]


def user_actor(user_email: str) -> Dict[str, Any]:
    return {"kind": "user", "email": normalize_user_email(user_email)}


SERVICE_ACTOR: Dict[str, Any] = {"kind": "service"}


class RunDirectoryUnavailable(Exception):
    """The shared run directory could not be read (Redis error or timeout)."""


_DIRECTORY_UNAVAILABLE_MESSAGE = "The shared run directory could not be read."


def cancel_api_run_here(run_id: str) -> Dict[str, Any]:
    """``cancel_api_run`` on this replica, as a run-control reply."""
    from atlas.application.chat.runs.external_runs import cancel_api_run
    from atlas.application.chat.runs.launcher import LaunchRefused

    try:
        return reply(True, status=cancel_api_run(run_id))
    except LaunchRefused as exc:
        return reply(False, getattr(exc, "code", "run_not_found"), str(exc))


# ---------------------------------------------------------------------------
# Owner side
# ---------------------------------------------------------------------------


class RunControlService:
    """Executes run-control commands against this replica's live runs."""

    def __init__(
        self,
        registry_getter: Callable[[], Any],
        approval_manager_getter: Callable[[], Any],
        refusal_check: Optional[RefusalCheck] = None,
    ):
        self._registry = registry_getter
        self._approvals = approval_manager_getter
        self._refusal_check = refusal_check

    async def handle(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        command = envelope.get("command")
        run_id = envelope.get("run_id") or ""
        actor = envelope.get("actor") or {}
        args = envelope.get("args") or {}
        if command == STOP:
            return self._stop(run_id, actor)
        if command == STEER:
            return await self._steer(run_id, actor, args, envelope.get("deadline"))
        if command == APPROVAL:
            return await self._approval(run_id, actor, args)
        if command == AUTO_APPROVE:
            return self._auto_approve(actor, args)
        return reply(False, "unknown_command", "Unknown run-control command.")

    def _owned_record(self, run_id: str, actor: Dict[str, Any]):
        """The live-or-retained record the actor may act on, or None."""
        if actor.get("kind") != "user" or not actor.get("email"):
            return None
        return self._registry().get_for_user(run_id, actor["email"])

    def _stop(self, run_id: str, actor: Dict[str, Any]) -> Dict[str, Any]:
        if actor.get("kind") == "service":
            # The trusted agent-runs service may stop API-started runs only;
            # cancel_api_run answers any other id as not found.
            return cancel_api_run_here(run_id)
        record = self._owned_record(run_id, actor)
        if record is None:
            return reply(False, "run_not_found", "No such run.")
        cancelled = self._registry().cancel(record.run_id, actor["email"])
        logger.info("Run %s stopped by a command from another replica", record.run_id)
        return reply(True, run=record.to_public_dict(), cancelled=cancelled)

    def _auto_approve(self, actor: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the user's auto-approve switch to the runs this replica owns.

        The switch is the user's, not one run's, so the command sets it on
        every user-started run of the actor here. Idempotent: a repeat finds
        the flag already set.
        """
        if actor.get("kind") != "user" or not actor.get("email"):
            return reply(False, "run_not_found", "No such run.")
        changed = self._registry().set_auto_approve_user_tools(actor["email"], args.get("enabled") is True)
        return reply(True, changed=changed)

    async def _steer(self, run_id: str, actor: Dict[str, Any], args: Dict[str, Any],
                     deadline: Any = None) -> Dict[str, Any]:
        record = self._owned_record(run_id, actor)
        if record is None or record.is_terminal:
            return reply(False, "run_not_found", "The run has finished.")
        steering = record.steering
        if steering is None or not steering.active:
            return reply(
                False,
                "conversation_busy",
                "This conversation already has a run in progress. Wait for it to "
                "reach its next step, or stop it, before sending another message.",
            )
        if self._refusal_check is not None:
            refusal = await self._refusal_check(record.session_id, args.get("compliance_level_filter"))
            if refusal:
                return reply(False, "classification_refused", refusal)
        if deadline is not None and await _shared_now() > float(deadline):
            # The check outlasted the sender's wait: it has already reported a
            # timeout, and a client retry without a request id would queue a
            # second copy.
            return reply(False, COMMAND_EXPIRED, "The command arrived after its deadline.")
        # Re-checked after the last await, right before queueing: the run may
        # have ended, or its loop stopped draining, while the classification
        # check or the clock read was awaited, and a message queued then would
        # be acknowledged but never consumed.
        if record.is_terminal or record.steering is not steering or not steering.active:
            return reply(False, "run_not_found", "The run has finished.")
        try:
            steering.queue.put_nowait(args.get("content", ""))
        except asyncio.QueueFull:
            return reply(
                False,
                "steering_queue_full",
                "The agent steering queue is full. Please wait for the agent to make "
                "progress before sending another steering message.",
            )
        logger.info("Steered run %s with a message received by another replica", record.run_id)
        return reply(True, run_id=record.run_id, conversation_id=record.conversation_id)

    async def _approval(self, run_id: str, actor: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
        from atlas.application.chat.runs.execution_state import (
            approval_storage_key,
            get_execution_store,
        )

        email = actor.get("email") if actor.get("kind") == "user" else None
        if not email:
            return reply(False, "forbidden", "Approvals are answered by the run's user.")
        invocation_id = args.get("invocation_id")
        tool_call_id = args.get("tool_call_id")
        manager = self._approvals()
        request = manager.find_open_request(tool_call_id, invocation_id, run_id, email)
        if request is None or request.future.done():
            # Settled already: by the executor's own poll, a timeout, or an
            # earlier delivery of this command. Nothing resumes twice.
            return reply(True, applied=False)
        if normalize_user_email(request.user_email) != email:
            return reply(False, "forbidden", "Not your approval.")
        key = approval_storage_key(request.invocation_id, request.tool_call_id, email)
        outcome = await get_execution_store().read_resolution(key) if key else None
        if not outcome:
            # Only the shared outcome settles an approval. A command without
            # one would let the transport, not the write-once record, decide.
            return reply(False, "no_outcome", "No recorded answer for this approval.")
        request.set_response(bool(outcome.get("approved")), outcome.get("arguments"), outcome.get("reason"))
        if request.run_id:
            self._registry().answer_pending_request(request.run_id, tool_call_id=request.tool_call_id)
        return reply(True, applied=True)


async def _shared_now() -> float:
    """The clock command deadlines are stamped on (see ``RedisRunControlBus.shared_now``)."""
    bus = get_run_control_bus()
    return await bus.shared_now() if bus is not None else time.time()


def start_run_control(refusal_check: Optional[RefusalCheck] = None) -> Optional[RunControlService]:
    """Start consuming this replica's inbox, when a bus is bound."""
    bus = get_run_control_bus()
    if bus is None:
        return None
    from atlas.application.chat.approval_manager import get_approval_manager
    from atlas.application.chat.runs.registry import get_run_registry

    service = RunControlService(get_run_registry, get_approval_manager, refusal_check)
    bus.start(service.handle)
    return service


# ---------------------------------------------------------------------------
# Sender side
# ---------------------------------------------------------------------------


async def remote_entry(run_id: Optional[str], *, include_terminal: bool = False) -> Optional[Dict[str, Any]]:
    """Another replica's directory entry for ``run_id``, or None.

    Raises :class:`RunDirectoryUnavailable` when the directory cannot be read.
    """
    bus = get_run_control_bus()
    if bus is None or not run_id:
        return None
    try:
        entry = await bus.owner_of(str(run_id), include_terminal=include_terminal)
    except Exception as exc:
        logger.warning("run directory lookup failed", exc_info=True)
        raise RunDirectoryUnavailable(str(exc)) from exc
    if entry is None or entry.get("replica") == bus.replica_id:
        # Ours (the registry already answered) or unknown.
        return None
    return entry


async def conversation_entry(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The live run holding ``conversation_id``, on any replica (this one included)."""
    bus = get_run_control_bus()
    if bus is None or not conversation_id:
        return None
    try:
        return await bus.run_for_conversation(str(conversation_id))
    except Exception as exc:
        logger.warning("run directory lookup failed", exc_info=True)
        raise RunDirectoryUnavailable(str(exc)) from exc


async def _send(entry: Dict[str, Any], command: str, actor: Dict[str, Any], args=None, request_id=None,
                off_record: bool = False):
    """Send to the entry's owner, carrying the generation it was routed by.

    A ``stale_generation`` refusal executed nothing, so it is safe to look the
    run up once more and resend when the claim really moved to another
    replica; the request id is kept, so the owner still runs it at most once.
    """
    bus = get_run_control_bus()
    assert bus is not None
    try:
        answer = await bus.send(entry["replica"], command, entry["run_id"], actor, args,
                                generation=entry.get("generation"), request_id=request_id,
                                off_record=off_record)
        if answer.get("code") != STALE_GENERATION:
            return answer
        try:
            fresh = await remote_entry(entry["run_id"], include_terminal=bool(entry.get("terminal")))
        except RunDirectoryUnavailable:
            return answer
        moved = fresh is not None and (fresh["replica"], fresh.get("generation")) != (
            entry["replica"], entry.get("generation"))
        if not moved or fresh.get("user_email") != entry.get("user_email"):
            return answer
        return await bus.send(fresh["replica"], command, fresh["run_id"], actor, args,
                              generation=fresh.get("generation"), request_id=request_id,
                              off_record=off_record)
    except Exception:
        logger.warning("run-control %s could not be sent", command, exc_info=True)
        return reply(False, OWNER_UNAVAILABLE, "The run's server could not be reached.")


async def route_stop(entry: Dict[str, Any], user_email: str) -> Dict[str, Any]:
    """Stop the user's run on its owner (``entry`` is where ``resolve`` found it).

    An owner that does not answer is reported (``command_timeout``), never
    taken for "no run": its live children may still be running, and a retry is
    safe. Stopping a finished parent stops the sub-conversations it launched.
    """
    answer = await _send(entry, STOP, user_actor(user_email))
    # The run the stop resolved to, so a failure frame for a stop addressed
    # only by conversation still names the run (the client keeps its running
    # state and Stop control while the run executes on its owner).
    return {**answer, "run_id": entry["run_id"]}


async def route_api_cancel(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The agent-runs API's cancel, on the owner of ``entry``.

    As ``cancel_api_run`` does on one replica, a finished run is stopped
    first (on its last owner, which cascades to the sub-conversations it
    launched), then the conversation's in-progress API run, a follow-up that
    may execute on another replica, this one included.
    """
    from atlas.application.chat.runs.external_runs import API_SOURCE

    answer = await _send(entry, STOP, SERVICE_ACTOR)
    if entry.get("source") != API_SOURCE:
        # Not an API run: the owner refuses it like an unknown id.
        return answer
    # Looked up after the owner answered, live or not: a run that was live
    # when routed may have finished while the stop was in flight, and its
    # follow-up may already be executing on another replica. The lookup skips
    # the named run itself.
    earlier = ((answer.get("payload") or {}).get("status") or {}) if answer.get("ok") else None
    later = await route_api_follow_up_cancel(
        str(entry.get("run_id")), entry.get("conversation_id"), entry.get("user_email"), earlier,
    )
    if not answer.get("ok"):
        # The follow-up was stopped best effort, but the named run's own stop
        # (which cascades to the sub-conversations it launched) did not reach
        # its owner. Report that retryable failure, never a 200.
        return answer
    if later is not None:
        return later
    return answer


async def route_api_follow_up_cancel(
    run_id: str,
    conversation_id: Optional[str],
    user_email: Optional[str],
    earlier_status: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Stop the in-progress API follow-up of the finished API run ``run_id``.

    For a cancel naming a finished run, after that run itself was cancelled
    (``earlier_status``, or None if that failed). The follow-up may execute on
    any replica. Returns None when there is no follow-up to stop (or it just
    finished), the follow-up's status with ``cancelled`` merged when it was
    stopped, and its failure (a timeout, the directory unreadable) otherwise,
    so the caller reports a retryable error rather than ``cancelled: false``.
    """
    from atlas.application.chat.runs.external_runs import API_SOURCE

    bus = get_run_control_bus()
    if bus is None:
        return None
    try:
        follow_up = await conversation_entry(conversation_id)
    except RunDirectoryUnavailable:
        return reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE)
    if (
        follow_up is None
        or follow_up.get("run_id") == run_id
        or follow_up.get("source") != API_SOURCE
        or follow_up.get("user_email") != normalize_user_email(user_email or "")
    ):
        return None
    if follow_up.get("replica") == bus.replica_id:
        later = cancel_api_run_here(str(follow_up["run_id"]))
    else:
        later = await _send(follow_up, STOP, SERVICE_ACTOR)
    if not later.get("ok"):
        return None if later.get("code") == "run_not_found" else later
    status = dict((later.get("payload") or {}).get("status") or {})
    status["cancelled"] = bool(status.get("cancelled") or (earlier_status or {}).get("cancelled"))
    return reply(True, status=status)


async def route_steer(
    entry: Dict[str, Any],
    user_email: str,
    content: Any,
    compliance_level_filter: Any,
    request_id: Optional[str] = None,
    off_record: bool = False,
) -> Dict[str, Any]:
    """Steer the run on its owner. ``off_record`` (an incognito or not-saved
    turn) is refused before the content is written to the shared inbox."""
    args = {"content": content, "compliance_level_filter": compliance_level_filter}
    return await _send(entry, STEER, user_actor(user_email), args, request_id=request_id,
                       off_record=off_record)


async def route_approval(entry: Dict[str, Any], user_email: str, invocation_id: Optional[str],
                         tool_call_id: Optional[str]) -> Dict[str, Any]:
    """Wake the owner's waiting tool after this replica recorded the outcome.

    The request id is derived from the invocation, so every replica that
    records or re-sends the same answer sends the same command.
    """
    args = {"invocation_id": invocation_id, "tool_call_id": tool_call_id}
    request_id = f"approval:{invocation_id or tool_call_id}"
    return await _send(entry, APPROVAL, user_actor(user_email), args, request_id=request_id)


async def route_auto_approve(entry: Dict[str, Any], user_email: str, enabled: bool) -> Dict[str, Any]:
    """Send the user's auto-approve switch to the owner of ``entry``'s run.

    ``entry`` is one of the user's live runs there; it addresses the replica
    and fences the command, and the owner applies the switch to all of the
    user's runs it holds. A fresh request id per send: each toggle is its own
    command, and the owner applying it twice changes nothing.
    """
    return await _send(entry, AUTO_APPROVE, user_actor(user_email), {"enabled": enabled is True})
