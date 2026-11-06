"""Stop, steer and approve a run on the replica that owns it (issue #1128).

Two halves share this module:

* **Owner side.** :class:`RunControlService` executes commands that arrive on
  this replica's inbox (see ``control_bus``). It re-checks everything against
  its own registry: the command's actor must own the run (or be the trusted
  agent-runs service, for an API-started run), and the run must still be
  live here. The directory entry that routed the command is never trusted
  for authorization.
* **Sender side.** ``route_*`` find a run that is not in this replica's
  registry in the shared directory and send the command to its owner. They
  return ``None`` when nothing in the directory matches, so the caller keeps
  its single-instance answer. A directory that cannot be read is never taken
  for "nothing matches": the lookups raise :class:`RunDirectoryUnavailable`
  and the routes answer ``owner_unavailable``, so a Redis outage cannot start
  a second turn beside a run another replica is executing.

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
    COMMAND_TIMEOUT,
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
        if record.is_terminal or record.steering is not steering:
            # The run ended while the classification check was awaited.
            return reply(False, "run_not_found", "The run has finished.")
        if deadline is not None and time.time() > float(deadline):
            # The check outlasted the sender's wait: it has already reported a
            # timeout, and a client retry without a request id would queue a
            # second copy.
            return reply(False, COMMAND_EXPIRED, "The command arrived after its deadline.")
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


async def _remote_entry(run_id: Optional[str], *, include_terminal: bool = False) -> Optional[Dict[str, Any]]:
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


async def _conversation_entry(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The live run holding ``conversation_id``, on any replica (this one included)."""
    bus = get_run_control_bus()
    if bus is None or not conversation_id:
        return None
    try:
        return await bus.run_for_conversation(str(conversation_id))
    except Exception as exc:
        logger.warning("run directory lookup failed", exc_info=True)
        raise RunDirectoryUnavailable(str(exc)) from exc


async def remote_run_for_conversation(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The run another replica is executing for a conversation, any owner.

    The caller compares ``user_email``: a conversation another user's run
    holds is refused like the local guard refuses it. Raises
    :class:`RunDirectoryUnavailable` when the directory cannot be read; the
    caller must then refuse rather than start a turn.
    """
    entry = await _conversation_entry(conversation_id)
    bus = get_run_control_bus()
    if entry is None or bus is None or entry.get("replica") == bus.replica_id:
        return None
    return entry


async def _send(entry: Dict[str, Any], command: str, actor: Dict[str, Any], args=None, request_id=None):
    """Send to the entry's owner, carrying the generation it was routed by.

    A ``stale_generation`` refusal executed nothing, so it is safe to look the
    run up once more and resend when the claim really moved to another
    replica; the request id is kept, so the owner still runs it at most once.
    """
    bus = get_run_control_bus()
    assert bus is not None
    try:
        answer = await bus.send(entry["replica"], command, entry["run_id"], actor, args,
                                generation=entry.get("generation"), request_id=request_id)
        if answer.get("code") != STALE_GENERATION:
            return answer
        try:
            fresh = await _remote_entry(entry["run_id"], include_terminal=bool(entry.get("terminal")))
        except RunDirectoryUnavailable:
            return answer
        moved = fresh is not None and (fresh["replica"], fresh.get("generation")) != (
            entry["replica"], entry.get("generation"))
        if not moved or fresh.get("user_email") != entry.get("user_email"):
            return answer
        return await bus.send(fresh["replica"], command, fresh["run_id"], actor, args,
                              generation=fresh.get("generation"), request_id=request_id)
    except Exception:
        logger.warning("run-control %s could not be sent", command, exc_info=True)
        return reply(False, OWNER_UNAVAILABLE, "The run's server could not be reached.")


def _unreached(answer: Dict[str, Any]) -> bool:
    return not answer.get("ok") and answer.get("code") in (COMMAND_TIMEOUT, OWNER_UNAVAILABLE)


async def route_stop(user_email: str, run_id: Optional[str], conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Stop the user's run on its owner. None when no other replica owns it.

    Resolution mirrors the local stop: the named run first, even when it has
    finished (stopping a finished parent stops the sub-conversations it
    launched, on its owner), then the conversation's live run.
    """
    email = normalize_user_email(user_email)
    try:
        entry = await _remote_entry(run_id, include_terminal=True)
        if entry is not None and entry.get("user_email") != email:
            # Another user's run answers like one that does not exist.
            entry = None
        if entry is None:
            entry = await remote_run_for_conversation(conversation_id)
            if entry is not None and entry.get("user_email") != email:
                entry = None
    except RunDirectoryUnavailable:
        return reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE)
    if entry is None:
        return None
    answer = await _send(entry, STOP, user_actor(user_email))
    if entry.get("terminal") and _unreached(answer):
        # A finished run whose last owner is gone: nothing live to stop there.
        return None
    return answer


async def route_api_cancel(run_id: str) -> Optional[Dict[str, Any]]:
    """The agent-runs API's cancel, on the owner. None when nobody owns it.

    As ``cancel_api_run`` does on one replica, a finished run is stopped
    first (on its last owner, which cascades to the sub-conversations it
    launched), then the conversation's in-progress API run, a follow-up that
    may execute on another replica, this one included.
    """
    bus = get_run_control_bus()
    try:
        entry = await _remote_entry(run_id, include_terminal=True)
    except RunDirectoryUnavailable:
        return reply(False, OWNER_UNAVAILABLE, _DIRECTORY_UNAVAILABLE_MESSAGE)
    if entry is None:
        return None
    answer = await _send(entry, STOP, SERVICE_ACTOR)
    if not entry.get("terminal"):
        return answer
    from atlas.application.chat.runs.external_runs import API_SOURCE

    try:
        follow_up = await _conversation_entry(entry.get("conversation_id"))
    except RunDirectoryUnavailable:
        follow_up = None
    if (
        follow_up is not None and bus is not None
        and follow_up.get("run_id") != entry.get("run_id")
        and follow_up.get("source") == API_SOURCE
        and follow_up.get("user_email") == entry.get("user_email")
    ):
        if follow_up.get("replica") == bus.replica_id:
            later = cancel_api_run_here(str(follow_up["run_id"]))
        else:
            later = await _send(follow_up, STOP, SERVICE_ACTOR)
        if later.get("ok"):
            status = dict((later.get("payload") or {}).get("status") or {})
            earlier = (answer.get("payload") or {}).get("status") or {} if answer.get("ok") else {}
            status["cancelled"] = bool(status.get("cancelled") or earlier.get("cancelled"))
            return reply(True, status=status)
    if _unreached(answer):
        return None
    return answer


async def route_steer(
    entry: Dict[str, Any],
    user_email: str,
    content: Any,
    compliance_level_filter: Any,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    args = {"content": content, "compliance_level_filter": compliance_level_filter}
    return await _send(entry, STEER, user_actor(user_email), args, request_id=request_id)


async def route_approval(user_email: str, run_id: Optional[str], invocation_id: Optional[str],
                         tool_call_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Wake the owner's waiting tool after this replica recorded the outcome.

    The request id is derived from the invocation, so every replica that
    records or re-sends the same answer sends the same command.
    """
    try:
        entry = await _remote_entry(run_id)
    except RunDirectoryUnavailable:
        # Best effort: the executor also polls the shared outcome.
        return None
    if entry is None or entry.get("user_email") != normalize_user_email(user_email):
        return None
    args = {"invocation_id": invocation_id, "tool_call_id": tool_call_id}
    request_id = f"approval:{invocation_id or tool_call_id}"
    return await _send(entry, APPROVAL, user_actor(user_email), args, request_id=request_id)
