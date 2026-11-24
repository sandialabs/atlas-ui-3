"""Agent runs started from outside ATLAS by a trusted service (#1105).

The REST API in ``atlas/routes/agent_run_routes.py`` is a thin transport over
this module, which is in turn a thin layer over the ``atlas_launch_subagent`` /
``atlas_follow_up_subagent`` machinery: the same workspace and model resolution, the
same re-filtering of tools and data sources through the target user's ACLs,
the same atomic admission into the run registry. What differs is only what
an external caller *is*:

* **There is no launching conversation.** An API run is a root (depth 0,
  no parent), so it lands as a top-level conversation in the target user's
  history and the per-run fan-out cap does not apply. The per-user
  ``MAX_CONCURRENT_RUNS_PER_USER`` cap and the wall-clock limit still do.
* **Nobody is watching it.** Ordinary tool approvals are given by the
  caller, the way the user's approval of an ``atlas_launch_subagent`` call covers the
  child. Tools an administrator has made approval-mandatory are never
  auto-approved: the launch is refused unless the caller asks for the run to
  pause until the user approves in the UI.
* **Scope is by origin.** The API sees only runs the API started; a run the
  user started in the browser is not addressable here, even by its id.

Without Redis, state lives in the process-local run registry. With Redis
coordination, API run identity, continuation settings, and retained listing
are shared, while the task itself still executes on its admitting replica.
The transcript is persisted to the user's conversation history as usual.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import hashlib
import json
import logging
import time
from types import SimpleNamespace
from typing import Any, Dict, List, NoReturn, Optional, Set, Tuple
from uuid import uuid4

from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError, get_run_coordinator
from atlas.application.chat.runs.follow_up import _revalidate_saved_workspace
from atlas.application.chat.runs.idempotency import (
    IDEMPOTENCY_COMPLETE_RETRY_SECONDS,
    IDEMPOTENCY_PENDING_TTL_SECONDS,
    IDEMPOTENCY_RENEW_INTERVAL_SECONDS,
    IdempotencyEntry,
    IdempotencySlot,
    InProcessIdempotencyStore,
    get_idempotency_store,
)
from atlas.application.chat.runs.launcher import (
    MAX_PROMPT_CHARS,
    LaunchRefused,
    _set_result,
    _start_child_run,
    list_launch_options,
    resolve_model,
    resolve_workspace,
)
from atlas.application.chat.runs.leases import get_lease_guard, publish_run_now, shared_view
from atlas.application.chat.runs.registry import RunStatus, get_run_registry
from atlas.core.coordination_metrics import CoordinationEvent, coordination_metrics
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.conversation_origin import ORIGIN_AGENT_RUNS_API

__all__ = [
    "API_SOURCE",
    "agent_runs_api_enabled",
    "cancel_api_run",
    "follow_up_api_run",
    "get_api_run",
    "get_api_run_anywhere",
    "launch_api_run",
    "list_api_runs",
    "list_api_runs_anywhere",
    "list_api_options",
    "reset_idempotency_cache",
]

logger = logging.getLogger(__name__)

API_SOURCE = ORIGIN_AGENT_RUNS_API
API_ACTOR = "service"

def agent_runs_api_enabled(app_settings: Any) -> bool:
    """Whether the API is switched on, with the ground a background run needs."""
    if app_settings is None:
        return False
    return bool(
        getattr(app_settings, "feature_agent_runs_api_enabled", False)
        and getattr(app_settings, "feature_chat_history_enabled", False)
        and getattr(app_settings, "feature_agent_mode_available", False)
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def reset_idempotency_cache() -> None:
    """Forget every in-process reservation (tests). A shared store is untouched."""
    store = get_idempotency_store()
    if isinstance(store, InProcessIdempotencyStore):
        store.clear()


def _fingerprint(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def _replay_or_refuse(entry: IdempotencyEntry, fingerprint: str) -> Dict[str, Any]:
    if entry.fingerprint != fingerprint:
        raise LaunchRefused(
            "This idempotency_key was already used for a different request.",
            code="idempotency_conflict",
        )
    if entry.handle is None:
        raise LaunchRefused(
            "A request with this idempotency_key is still being admitted. "
            "Retry shortly.",
            code="idempotency_in_progress",
        )
    return await _replayed(entry.handle)


async def _reserve(
    user_email: str,
    key: Optional[str],
    payload: Dict[str, Any],
    addressed_run_id: Optional[str] = None,
) -> Tuple[Optional[IdempotencySlot], Optional[Dict[str, Any]]]:
    """Claim ``key`` for this request, or return the handle it already produced.

    The store reserves atomically (in process before the first await of
    admission; in Redis with one script), so two concurrent requests with one
    key cannot both launch, on one replica or on two. With a shared store
    that cannot be reached a keyed request is refused (``unavailable``).
    """
    if not key:
        return None, None
    fingerprint = _fingerprint(payload)
    # Taken before the reserve call: the store's expiry clock starts inside it.
    started = time.monotonic()
    try:
        slot, existing = await get_idempotency_store().reserve(
            user_email,
            key,
            fingerprint,
            addressed_run_id=addressed_run_id,
            conversation_id=payload.get("conversation_id"),
        )
    except RunCoordinationUnavailableError as exc:
        raise LaunchRefused(str(exc), code="unavailable") from exc
    if existing is not None:
        return None, await _replay_or_refuse(existing, fingerprint)
    return dataclasses.replace(slot, reserved_at=started), None


def _follow_up_payload(
    conversation_id: Optional[str], message: str, allow_approval_pause: bool
) -> Dict[str, Any]:
    return {
        "op": "follow_up",
        "conversation_id": conversation_id,
        "message": message,
        "allow_approval_pause": allow_approval_pause,
    }


async def _replay_follow_up_of_reaped_run(
    config_manager: Any,
    run_id: str,
    key: Optional[str],
    message: str,
    allow_approval_pause: bool,
) -> Optional[Dict[str, Any]]:
    """The remembered reply to a follow-up whose addressed run is not here.

    The run was reaped, or another replica holds it. The slot is keyed by the
    run's owner, which can no longer be read from the registry, so the entry
    is found by the run id it addressed instead.
    """
    if not key:
        return None
    try:
        found = await get_idempotency_store().find_follow_up(
            run_id, key, *await _shared_conversation(run_id)
        )
    except RunCoordinationUnavailableError as exc:
        raise LaunchRefused(str(exc), code="unavailable") from exc
    if found is None:
        return None
    owner, entry = found
    _check_user_allowed(config_manager, owner)
    fingerprint = _fingerprint(
        _follow_up_payload(entry.conversation_id, message, allow_approval_pause)
    )
    return await _replay_or_refuse(entry, fingerprint)


async def _replayed(handle: Dict[str, Any]) -> Dict[str, Any]:
    """The stored handle with the run's current status.

    The owner replica reads its registry. Another replica reads the shared run
    record; a read failure keeps the status the handle was stored with, since
    the replay's job is to name the original run.
    """
    replay = dict(handle)
    record = get_run_registry().get(handle["run_id"])
    if record is not None:
        replay["status"] = record.status.value
    else:
        replay["status"] = await _shared_status(handle) or (
            "expired" if get_lease_guard() is None else replay.get("status", "expired")
        )
    replay["idempotent_replay"] = True
    return replay


async def _shared_conversation(run_id: str) -> Tuple[Optional[str], Optional[str]]:
    """``(conversation_id, user)`` of an API run another replica holds, from its
    shared record, so a follow-up retry naming a sibling run id still replays.

    An unreadable record is not "no such run": a client that gives up on a 404
    would relaunch work that already exists, so it is refused as unavailable.
    """
    guard = get_lease_guard()
    if guard is None:
        return None, None
    try:
        doc = await guard.read_record(run_id)
    except Exception as exc:
        logger.warning("shared run record unreadable on replay: %s", type(exc).__name__)
        raise LaunchRefused(
            "The run's record could not be read right now. Try again.", code="unavailable"
        ) from exc
    if not doc or doc.get("source") != API_SOURCE:
        return None, None
    return doc.get("conversation_id") or None, doc.get("user_email") or None


async def _shared_status(handle: Dict[str, Any]) -> Optional[str]:
    guard = get_lease_guard()
    if guard is None:
        return None
    try:
        doc = await guard.read_record(handle["run_id"])
    except Exception as exc:
        logger.warning("shared run record unreadable on replay: %s", type(exc).__name__)
        return None
    if doc is None:
        return "expired"
    return doc.get("status") or None


class _Hold:
    """Keeps a pending reservation alive while its request admits a run.

    If the reservation is lost (another request holds the key, or Redis stayed
    unreachable for as long as the pending TTL), the request is cancelled
    before it admits a run, so an expired reservation cannot lead to a second
    run for the same key.
    """

    def __init__(self, slot: IdempotencySlot):
        self.slot = slot
        self.lost = False
        # Set once the request stops admitting and writes its outcome: the
        # reservation stays protected through that write, but a loss then
        # must not cancel a request that is already finishing.
        self.writing = False
        self._request = asyncio.current_task()
        self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        store = get_idempotency_store()
        # The reservation is only good until its last extension plus the
        # pending TTL, however long a renewal takes to answer. ``last_ok`` is
        # taken before the call, so the deadline is never later than the
        # store's own.
        last_ok = self.slot.reserved_at or time.monotonic()
        while True:
            deadline = last_ok + IDEMPOTENCY_PENDING_TTL_SECONDS
            await asyncio.sleep(max(0.0, min(IDEMPOTENCY_RENEW_INTERVAL_SECONDS, deadline - time.monotonic())))
            attempt = time.monotonic()
            remaining = deadline - attempt
            extended: Optional[bool] = None
            if remaining > 0:
                try:
                    extended = await asyncio.wait_for(store.renew(self.slot), timeout=remaining)
                except asyncio.TimeoutError:
                    extended = None
            if extended:
                last_ok = attempt
            elif extended is False or time.monotonic() >= deadline:
                logger.error("An idempotency reservation was lost while its request was admitting")
                coordination_metrics().record_event(
                    CoordinationEvent.IDEMPOTENCY_RESERVATION_LOST,
                    logging.ERROR,
                    reason="taken" if extended is False else "expired",
                )
                self.lost = True
                if self._request is not None and not self.writing:
                    self._request.cancel()
                return

    async def stop(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


def _hold(slot: Optional[IdempotencySlot]) -> Optional[_Hold]:
    return _Hold(slot) if slot is not None else None


def _after_cancel(holder: Optional[_Hold], handle: Optional[Dict[str, Any]]) -> NoReturn:
    """Resolve a cancellation that came from a lost reservation.

    Before a run exists the request is refused (nothing launched, nothing to
    undo). A run that was admitted just before the loss is cancelled, so the
    key's lapse cannot leave one run running and a retry free to start a
    second. Any other cancellation (the client went away) propagates.
    """
    if holder is None or not holder.lost:
        raise asyncio.CancelledError()
    task = asyncio.current_task()
    if task is not None:
        task.uncancel()
    if handle is not None:
        get_run_registry().cancel(handle["run_id"], handle["user"])
    raise LaunchRefused(
        "The idempotency reservation for this request was lost before the run "
        "was admitted, so nothing was launched. Retry shortly.",
        code="unavailable",
    )


_KEY_TAKEN_MESSAGE = (
    "The idempotency reservation for this request was taken over before the run "
    "was recorded, so it was cancelled. Retry shortly."
)


def _abandon_run(handle: Dict[str, Any], reason: str = "taken") -> None:
    """Cancel an admitted run whose idempotency key can no longer be recorded
    (taken over, or the store stayed unwritable), so no live run is left that a
    retry of the same request could duplicate."""
    logger.error("An idempotency key could not be recorded for its run (%s)", reason)
    coordination_metrics().record_event(
        CoordinationEvent.IDEMPOTENCY_RESERVATION_LOST, logging.ERROR, reason=reason
    )
    get_run_registry().cancel(handle["run_id"], handle["user"])


async def _release(
    slot: Optional[IdempotencySlot],
    handle: Optional[Dict[str, Any]],
    holder: Optional[_Hold] = None,
) -> None:
    try:
        if holder is not None:
            holder.writing = True  # keep renewing until the outcome is written
        if slot is None:
            return
        store = get_idempotency_store()
        if handle is None:
            await store.release(slot)
        else:
            stored = await store.complete(slot, handle)
            if stored is None:
                # The key is another request's: this run must not be handed
                # out as the key's run, or a retry would replay the other one.
                _abandon_run(handle)
                raise LaunchRefused(_KEY_TAKEN_MESSAGE, code="unavailable")
            if not stored:
                _complete_later(slot, handle)
    finally:
        if holder is not None:
            await holder.stop()


# Strong references: asyncio keeps only weak ones to running tasks.
_deferred_completions: Set["asyncio.Task[None]"] = set()


def _complete_later(slot: IdempotencySlot, handle: Dict[str, Any]) -> None:
    """Keep retrying to record the run handle after a failed write.

    The run was admitted, but its record is still pending in the store; if it
    were left to lapse, a client retry would launch the run a second time.
    """
    task = asyncio.ensure_future(_retry_complete(slot, handle))
    _deferred_completions.add(task)
    task.add_done_callback(_deferred_completions.discard)


async def _retry_complete(slot: IdempotencySlot, handle: Dict[str, Any]) -> None:
    store = get_idempotency_store()
    deadline = time.monotonic() + IDEMPOTENCY_PENDING_TTL_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(IDEMPOTENCY_COMPLETE_RETRY_SECONDS)
        # A renewal that finds the key gone does not end the attempt: a key
        # that merely expired is re-taken by ``complete``; one that is another
        # request's makes ``complete`` answer taken.
        await store.renew(slot)
        stored = await store.complete(slot, handle)
        if stored:
            return
        if stored is None:
            _abandon_run(handle)
            return
    # Still unwritable: do not leave a live, unrecorded run that a retry of the
    # same request could duplicate.
    _abandon_run(handle, "unrecorded")


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


def _require_ground(factory: Any) -> Any:
    config_manager = factory.get_config_manager()
    if getattr(factory, "conversation_repository", None) is None:
        # A background run is read back from history; without it the run's
        # work would be unobservable once its record is reaped.
        raise LaunchRefused(
            "Conversation history is unavailable on this deployment, so an "
            "agent run would have nowhere to be saved.",
            code="persistence_unavailable",
        )
    if getattr(factory, "workspace_repository", None) is None:
        raise LaunchRefused(
            "Workspaces are not configured on this deployment.", code="unavailable"
        )
    return config_manager


@functools.lru_cache(maxsize=1)
def _domain_whitelist() -> Any:
    # Loaded once, as the middleware loads it once at startup.
    from atlas.core.domain_whitelist import DomainWhitelistManager

    return DomainWhitelistManager()


def _check_user_allowed(config_manager: Any, user_email: str) -> None:
    """Apply the email domain whitelist to the user the run acts as.

    The browser path enforces it in middleware on the signed-in user; the
    service has no such user, so the whitelist is applied to the target here
    -- otherwise the API would be a way to run as someone the deployment
    does not admit.
    """
    settings = getattr(config_manager, "app_settings", None)
    if getattr(settings, "feature_domain_whitelist_enabled", False) is not True:
        return
    if "@" not in user_email or not _domain_whitelist().is_domain_allowed(user_email):
        raise LaunchRefused(
            "That user is not permitted on this deployment (domain whitelist).",
            code="user_not_allowed",
        )


def _check_message(message: str) -> str:
    message = (message or "").strip()
    if not message:
        raise LaunchRefused("'message' must be a non-empty string.", code="invalid_request")
    if len(message) > MAX_PROMPT_CHARS:
        raise LaunchRefused(
            f"'message' is too long ({len(message)} characters); the limit is "
            f"{MAX_PROMPT_CHARS}.",
            code="invalid_request",
        )
    return message


def _handle(record: Any, spec: Dict[str, Any], admin_gated: List[str]) -> Dict[str, Any]:
    return {
        "run_id": record.run_id,
        "conversation_id": record.conversation_id,
        "user": record.user_email,
        "status": record.status.value,
        "workspace": spec.get("workspace_name"),
        "model": spec.get("model"),
        "tools": list(spec.get("tools") or []),
        "data_sources": list(spec.get("data_sources") or []),
        "data_sources_unavailable": [
            source
            for source in spec.get("requested_data_sources") or []
            if source not in (spec.get("data_sources") or [])
        ],
        "compliance_level": spec.get("compliance_level"),
        "tools_needing_approval": list(admin_gated),
    }


def _audit(action: str, record: Any, spec: Dict[str, Any], **extra: Any) -> None:
    # Identities and ids only: never the message, the result, or the key.
    logger.info(
        "agent-runs API %s: actor=%s user=%s run=%s conversation=%s "
        "workspace=%s model=%s classification=%s%s",
        action,
        API_ACTOR,
        sanitize_for_logging(record.user_email),
        record.run_id,
        sanitize_for_logging(record.conversation_id),
        sanitize_for_logging(str(spec.get("workspace_id"))),
        sanitize_for_logging(str(spec.get("model"))),
        sanitize_for_logging(str(spec.get("compliance_level"))),
        "".join(f" {k}={sanitize_for_logging(str(v))}" for k, v in extra.items()),
    )


async def list_api_options(factory: Any, user_email: str) -> Dict[str, Any]:
    """``atlas_discover_subagent_options`` for ``user_email``."""
    _check_user_allowed(_require_ground(factory), user_email)
    return await list_launch_options(factory, user_email)


async def launch_api_run(
    factory: Any,
    *,
    user_email: str,
    workspace: str,
    model: str,
    message: str,
    compliance_level: Optional[str] = None,
    allow_approval_pause: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """``atlas_launch_subagent`` for ``user_email``: a new top-level conversation.

    Returns the run handle, with ``idempotent_replay`` set when an earlier
    request with the same ``idempotency_key`` already launched it.
    """
    config_manager = _require_ground(factory)
    _check_user_allowed(config_manager, user_email)
    message = _check_message(message)
    workspace = (workspace or "").strip()
    model = (model or "").strip()
    if not workspace or not model:
        raise LaunchRefused("'workspace' and 'model' are required.", code="invalid_request")

    slot, replay = await _reserve(
        user_email,
        idempotency_key,
        {
            "op": "launch",
            "workspace": workspace,
            "model": model,
            "message": message,
            "compliance_level": compliance_level,
            "allow_approval_pause": allow_approval_pause,
        },
    )
    if replay is not None:
        return replay
    handle: Optional[Dict[str, Any]] = None
    holder = _hold(slot)
    try:
        resolved = resolve_workspace(factory.workspace_repository, workspace, user_email)
        await _resolve_api_model(config_manager, model, user_email)
        workspace_config = resolved.get("config") or {}
        data_sources = (
            list(workspace_config.get("selected_data_sources") or [])
            if workspace_config.get("rag_enabled")
            else []
        )
        record, spec, admin_gated = await _start_child_run(
            factory=factory,
            config_manager=config_manager,
            context=None,
            user_email=user_email,
            conversation_id=str(uuid4()),
            model=model,
            prompt=message,
            selected_tools=list(workspace_config.get("selected_tools") or []),
            data_sources=data_sources,
            workspace_id=resolved.get("id"),
            workspace_name=resolved.get("name"),
            title=message,
            no_tools_message=(
                "That workspace has no tools the user is currently authorized to "
                "use, so a run under it would have nothing to work with. Choose "
                "a workspace with tools."
            ),
            top_level=True,
            refuse_approval_gated=not allow_approval_pause,
            source=API_SOURCE,
            actor=API_ACTOR,
            compliance_level=compliance_level,
        )
        handle = _handle(record, spec, admin_gated)
        _audit("launch", record, spec)
        # Before the 202: a poll that lands on another replica must find it.
        await publish_run_now(record.run_id)
        return handle
    except asyncio.CancelledError:
        admitted = handle
        if holder is not None and holder.lost:
            handle = None  # the key is lost: record nothing, release it
        _after_cancel(holder, admitted)
    finally:
        await _release(slot, handle, holder)


async def _resolve_api_model(config_manager: Any, model: str, user_email: str) -> None:
    """``resolve_model``, with a refusal worded for a REST caller, not a chat."""
    try:
        await resolve_model(config_manager, model, user_email)
    except LaunchRefused as exc:
        if getattr(exc, "code", None) != "model_not_available":
            raise
        raise LaunchRefused(
            f"Model '{model}' is not available to that user. "
            "GET /api/agent-runs/options lists the models they may use.",
            code="model_not_available",
        ) from exc


def _revalidate_api_workspace(factory: Any, spec: Dict[str, Any], user_email: str) -> Dict[str, Any]:
    """``_revalidate_saved_workspace``, with refusals worded for a REST caller."""
    try:
        return _revalidate_saved_workspace(factory, spec, user_email)
    except LaunchRefused as exc:
        name = spec.get("workspace_name") or spec.get("workspace_id") or "unknown"
        code = getattr(exc, "code", None)
        messages = {
            "workspace_not_found": (
                f"The workspace '{name}' this conversation was launched under is no "
                "longer available to the user (deleted, or access revoked), so it "
                "cannot be continued. Launch a new run; GET /api/agent-runs/options "
                "lists the user's workspaces."
            ),
            "not_continuable": (
                "This conversation's workspace is no longer known, so it cannot be "
                "continued. Launch a new run instead."
            ),
            "unavailable": (
                f"The workspace '{name}' could not be checked just now, so the "
                "conversation cannot be continued. Retry shortly."
            ),
        }
        if code not in messages:
            raise
        raise LaunchRefused(messages[code], code=code) from exc


def _api_record(run_id: str) -> Any:
    """The API-started run ``run_id``, or a not-found refusal.

    Runs started anywhere else -- the browser, ``atlas_launch_subagent`` -- answer
    exactly like ids that never existed, so the API cannot be used to probe
    or act on them.
    """
    registry = get_run_registry()
    registry.reap_terminal()
    record = registry.get(run_id)
    if record is None or (record.launch_spec or {}).get("source") != API_SOURCE:
        raise LaunchRefused(
            "No agent run with that id. Runs are kept for about 30 minutes "
            "after they finish.",
            code="run_not_found",
        )
    return record


def _api_runs_for_conversation(record: Any) -> List[Any]:
    return [
        other
        for other in get_run_registry().records_for_user(record.user_email)
        if other.conversation_id == record.conversation_id
        and (other.launch_spec or {}).get("source") == API_SOURCE
    ]


def _shared_unavailable() -> LaunchRefused:
    return LaunchRefused("The API run record could not be read right now. Try again.", code="unavailable")


def _note_shared_api_read_failure(exc: Exception, op: str) -> None:
    logger.warning("Shared API run read failed (%s): %s", op, type(exc).__name__)
    try:
        coordination_metrics().dependency_failed("redis", exc, op=op)
    except Exception:
        logger.debug("Recording shared API run read failure failed", exc_info=True)


async def _shared_api_record(coordinator: Any, run_id: str) -> Optional[Dict[str, Any]]:
    try:
        raw = await coordinator.read(run_id)
        doc = shared_view(raw)
    except Exception as exc:
        _note_shared_api_read_failure(exc, "api_run_read")
        raise _shared_unavailable() from exc
    if doc is None or doc.get("source") != API_SOURCE:
        return None
    if doc.get("snapshot_unreadable") or not doc.get("user_email") or not doc.get("conversation_id"):
        raise _shared_unavailable()
    return doc


async def _shared_follow_up_target(coordinator: Any, addressed: Any) -> Any:
    try:
        raw = await coordinator.read_api_continuation(addressed.conversation_id)
        doc = shared_view(raw)
        spec = json.loads(raw["api_spec"]) if raw else None
    except Exception as exc:
        _note_shared_api_read_failure(exc, "api_continuation_read")
        raise _shared_unavailable() from exc
    if (
        not doc or doc.get("snapshot_unreadable")
        or doc.get("source") != API_SOURCE
        or doc.get("user_email") != addressed.user_email
        or doc.get("conversation_id") != addressed.conversation_id
        or not isinstance(spec, dict) or spec.get("source") != API_SOURCE
        or not all(isinstance(spec.get(k), str) and spec[k] for k in ("model", "workspace_id"))
        or not all(isinstance(spec.get(k), list) and all(isinstance(v, str) for v in spec[k])
                   for k in ("tools", "data_sources", "requested_data_sources"))
        or "compliance_level" not in spec
    ):
        raise _shared_unavailable()
    status = doc.get("status")
    if status == RunStatus.WAITING_FOR_INPUT.value:
        raise LaunchRefused(
            "The conversation is waiting for the user "
            f"({doc.get('waiting_on') or 'input'}) and cannot take a follow-up "
            "until they answer it in ATLAS.",
            code="waiting_for_input",
        )
    if status not in ("completed", "failed", "cancelled"):
        raise LaunchRefused(
            f"The conversation is still working (run {doc['run_id']}, status "
            f"{status}). Wait for it to finish first.",
            code="conversation_busy",
        )
    if status != "completed" and raw.get("api_completed_before") != "1":
        raise LaunchRefused(
            "That conversation never completed a turn, so there is nothing "
            "to follow up on. Launch a new run instead.",
            code="not_continuable",
        )
    return SimpleNamespace(
        run_id=doc["run_id"], conversation_id=doc["conversation_id"],
        user_email=doc["user_email"], title=doc.get("title"), launch_spec=spec,
    )


def _status(record: Any) -> Dict[str, Any]:
    spec = record.launch_spec or {}
    payload = record.to_public_dict()
    payload.update(
        {
            "user": record.user_email,
            "workspace": spec.get("workspace_name"),
            "model": spec.get("model"),
            "tools": list(spec.get("tools") or []),
            "data_sources": list(spec.get("data_sources") or []),
            "compliance_level": spec.get("compliance_level"),
            "previous_run_id": spec.get("previous_run_id"),
            "result": None,
            "result_status": "pending" if not record.is_terminal else "unavailable",
            "interrupted": bool(record.fenced),
        }
    )
    if record.status is RunStatus.COMPLETED:
        if record.result == "":
            payload["result_status"] = "empty"
        elif record.result is not None:
            _set_result(payload, record.result)
        else:
            payload["result_error"] = "This run's reply could not be captured."
    return payload


def get_api_run(run_id: str) -> Dict[str, Any]:
    """``atlas_get_subagent_result``: status, timestamps, error and final reply of one run."""
    return _status(_api_record(run_id))


def _status_from_shared(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The status of a run another replica owns (or owned), from its
    shared record (#1128). Same shape as :func:`_status`."""
    payload = {
        key: doc.get(key)
        for key in (
            "run_id", "conversation_id", "status", "waiting_on", "created_at",
            "updated_at", "ended_at", "error", "end_reason", "title", "parent_run_id",
            "parent_conversation_id", "workspace", "model", "compliance_level",
            "previous_run_id",
        )
    }
    payload.update({
        "user": doc.get("user_email"),
        "detached": bool(doc.get("detached")),
        "depth": int(doc.get("depth") or 0),
        "tools": list(doc.get("tools") or []),
        "data_sources": list(doc.get("data_sources") or []),
        "interrupted": bool(doc.get("interrupted")),
        "result": None,
        "result_status": (
            "pending" if doc.get("status") not in ("completed", "failed", "cancelled")
            else "unavailable"
        ),
    })
    if doc.get("status") == "completed":
        result = doc.get("result")
        if result == "":
            payload["result_status"] = "empty"
        elif result is not None:
            _set_result(payload, result)
        else:
            payload["result_error"] = "This run's reply could not be captured."
    return payload


async def get_api_run_anywhere(run_id: str) -> Dict[str, Any]:
    """:func:`get_api_run`, falling back to the shared run record.

    The replica that owns a run answers from its registry. Any other
    replica answers from the record the owner mirrors to Redis -- including
    a run whose owner died, which the orphan sweep marked interrupted.
    """
    try:
        return get_api_run(run_id)
    except LaunchRefused:
        from atlas.application.chat.runs.leases import get_lease_guard

        guard = get_lease_guard()
        if guard is None:
            raise
        try:
            doc = await guard.read_record(run_id)
        except Exception as exc:
            # Not "no such run": a client that relaunches on 404 would repeat
            # a live run's side effects.
            logger.warning("shared run record unreadable: %s", type(exc).__name__)
            raise LaunchRefused(
                "The run's status could not be read right now. Try again.", code="unavailable"
            ) from exc
        if doc is None:
            raise
        if doc.get("snapshot_unreadable"):
            raise LaunchRefused(
                "The run's status could not be read right now. Try again.", code="unavailable"
            )
        # The source is in the record from admission, so a run that is not an
        # API run is known not to be one.
        if doc.get("source") != API_SOURCE:
            raise
        return _status_from_shared(doc)


def list_api_runs(user_email: Optional[str] = None) -> List[Dict[str, Any]]:
    """``atlas_get_subagent_runs``: API-started runs, optionally for one user, newest first."""
    registry = get_run_registry()
    registry.reap_terminal()
    if user_email:
        records = registry.records_for_user(user_email)
    else:
        records = registry.all_records()
    return [
        _status(record)
        for record in sorted(records, key=lambda r: r.created_at, reverse=True)
        if (record.launch_spec or {}).get("source") == API_SOURCE
    ]


async def list_api_runs_anywhere(user_email: Optional[str] = None) -> List[Dict[str, Any]]:
    coordinator = get_run_coordinator()
    if not hasattr(coordinator, "read_api_runs"):
        return list_api_runs(user_email)
    from atlas.core.user_identity import normalize_user_email

    try:
        raw_records = await coordinator.read_api_runs(user_email)
        result = []
        for raw in raw_records:
            try:
                doc = shared_view(raw)
                if not doc or doc.get("source") != API_SOURCE:
                    continue
                if user_email and normalize_user_email(doc.get("user_email")) != normalize_user_email(user_email):
                    continue
                if doc.get("snapshot_unreadable"):
                    logger.warning("Skipping unreadable API run snapshot in listing")
                    continue
                payload = _status_from_shared(doc)
                if not isinstance(payload["created_at"], (int, float)) or not isinstance(payload["run_id"], str):
                    logger.warning("Skipping API run record with invalid listing metadata")
                    continue
                result.append(payload)
            except (TypeError, ValueError, KeyError, AttributeError):
                logger.warning("Skipping malformed API run record in listing", exc_info=False)
        return sorted(result, key=lambda run: (run["created_at"], run["run_id"]), reverse=True)
    except LaunchRefused:
        raise
    except Exception as exc:
        _note_shared_api_read_failure(exc, "api_run_listing")
        raise _shared_unavailable() from exc


async def follow_up_api_run(
    factory: Any,
    run_id: str,
    *,
    message: str,
    allow_approval_pause: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """``atlas_follow_up_subagent``: the next user turn of an API run's conversation.

    Runs as the conversation's owner, on the workspace, model and compliance
    level it was launched with -- the caller cannot re-choose any of them --
    each re-checked against that user's access now. The chat service hydrates
    the stored transcript first, so the run sees its whole history.
    """
    config_manager = _require_ground(factory)
    message = _check_message(message)
    coordinator = get_run_coordinator()
    shared = hasattr(coordinator, "read_api_continuation")
    try:
        if shared:
            doc = await _shared_api_record(coordinator, run_id)
            if doc is None:
                # A retained local record without shared metadata is not safe to continue.
                try:
                    _api_record(run_id)
                except LaunchRefused:
                    raise LaunchRefused("No retained API run with that id.", code="run_not_found")
                raise _shared_unavailable()
            addressed = SimpleNamespace(**doc)
        else:
            addressed = _api_record(run_id)
    except LaunchRefused as exc:
        if exc.code != "run_not_found":
            raise
        replay = await _replay_follow_up_of_reaped_run(
            config_manager, run_id, idempotency_key, message, allow_approval_pause
        )
        if replay is not None:
            return replay
        raise
    user_email = addressed.user_email
    _check_user_allowed(config_manager, user_email)

    slot, replay = await _reserve(
        user_email,
        idempotency_key,
        _follow_up_payload(addressed.conversation_id, message, allow_approval_pause),
        addressed_run_id=addressed.run_id,
    )
    if replay is not None:
        return replay
    handle: Optional[Dict[str, Any]] = None
    holder = _hold(slot)
    try:
        if shared:
            target = await _shared_follow_up_target(coordinator, addressed)
        else:
            siblings = _api_runs_for_conversation(addressed)
            # Continue from the newest run, whichever id was passed: it carries
            # the narrowest spec, and an older id must not resurrect a tool a
            # later follow-up already lost.
            target = max(siblings, key=lambda r: r.created_at)
            registry = get_run_registry()
            active = registry.active_for_conversation(target.conversation_id, user_email)
            if active is not None and active.status is RunStatus.WAITING_FOR_INPUT:
                raise LaunchRefused(
                    "The conversation is waiting for the user "
                    f"({active.waiting_on or 'input'}) and cannot take a follow-up "
                    "until they answer it in ATLAS.",
                    code="waiting_for_input",
                )
            if active is not None:
                raise LaunchRefused(
                    f"The conversation is still working (run {active.run_id}, status "
                    f"{active.status.value}). Wait for it to finish first.",
                    code="conversation_busy",
                )
            if not any(r.status is RunStatus.COMPLETED for r in siblings):
                raise LaunchRefused(
                    "That conversation never completed a turn, so there is nothing "
                    "to follow up on. Launch a new run instead.",
                    code="not_continuable",
                )
        spec = target.launch_spec or {}
        if not spec.get("model"):
            raise LaunchRefused(
                "That run has no recorded launch settings to continue from. "
                "Launch a new run instead.",
                code="not_continuable",
            )
        workspace = _revalidate_api_workspace(factory, spec, user_email)
        await _resolve_api_model(config_manager, spec["model"], user_email)
        record, new_spec, admin_gated = await _start_child_run(
            factory=factory,
            config_manager=config_manager,
            context=None,
            user_email=user_email,
            conversation_id=target.conversation_id,
            model=spec["model"],
            prompt=message,
            selected_tools=list(spec.get("tools") or []),
            data_sources=list(
                spec.get("requested_data_sources", spec.get("data_sources")) or []
            ),
            workspace_id=workspace.get("id") or spec.get("workspace_id"),
            workspace_name=workspace.get("name") or spec.get("workspace_name"),
            title=target.title,
            no_tools_message=(
                "None of the tools that conversation was launched with are still "
                "available to the user, so it cannot be continued."
            ),
            top_level=True,
            refuse_approval_gated=not allow_approval_pause,
            source=API_SOURCE,
            actor=API_ACTOR,
            # The conversation's classification is immutable: the follow-up
            # runs at the level it was launched at, never one the caller picks.
            compliance_level=spec.get("compliance_level"),
            follow_up_of=target.run_id,
        )
        new_spec["previous_run_id"] = target.run_id
        handle = _handle(record, new_spec, admin_gated)
        handle["previous_run_id"] = target.run_id
        _audit("follow_up", record, new_spec, previous_run=target.run_id)
        await publish_run_now(record.run_id)
        return handle
    except asyncio.CancelledError:
        admitted = handle
        if holder is not None and holder.lost:
            handle = None  # the key is lost: record nothing, release it
        _after_cancel(holder, admitted)
    finally:
        await _release(slot, handle, holder)


def cancel_api_run(run_id: str) -> Dict[str, Any]:
    """Stop an API run (and anything it launched), as the UI's stop does.

    Like a follow-up, any of the conversation's run ids addresses it: when the
    run named has already finished, the conversation's in-progress API run
    (a later follow-up) is the one stopped, and the response describes it.
    A turn the user started themselves in ATLAS is never touched.
    """
    record = _api_record(run_id)
    registry = get_run_registry()
    # The named run first, even when it has finished: that cascades to any
    # sub-conversations it launched that are still working.
    cancelled = registry.cancel(record.run_id, record.user_email)
    if record.is_terminal:
        live = [r for r in _api_runs_for_conversation(record) if not r.is_terminal]
        if live:
            record = max(live, key=lambda r: r.created_at)
            cancelled = registry.cancel(record.run_id, record.user_email) or cancelled
    _audit("cancel", record, record.launch_spec or {}, cancelled=cancelled)
    payload = _status(registry.get(record.run_id) or record)
    payload["cancelled"] = cancelled
    return payload
