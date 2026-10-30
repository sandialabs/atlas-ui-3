"""Agent runs started from outside ATLAS by a trusted service (#1105).

The REST API in ``atlas/routes/agent_run_routes.py`` is a thin transport over
this module, which is in turn a thin layer over the ``atlas_launch`` /
``atlas_follow_up`` machinery: the same workspace and model resolution, the
same re-filtering of tools and data sources through the target user's ACLs,
the same atomic admission into the run registry. What differs is only what
an external caller *is*:

* **There is no launching conversation.** An API run is a root (depth 0,
  no parent), so it lands as a top-level conversation in the target user's
  history and the per-run fan-out cap does not apply. The per-user
  ``MAX_CONCURRENT_RUNS_PER_USER`` cap and the wall-clock limit still do.
* **Nobody is watching it.** Ordinary tool approvals are given by the
  caller, the way the user's approval of an ``atlas_launch`` call covers the
  child. Tools an administrator has made approval-mandatory are never
  auto-approved: the launch is refused unless the caller asks for the run to
  pause until the user approves in the UI.
* **Scope is by origin.** The API sees only runs the API started; a run the
  user started in the browser is not addressable here, even by its id.

State lives in the process-local run registry, exactly like the runs it
mirrors: status and follow-up are available for the registry's retention
window (about 30 minutes after a run ends) on the replica that accepted the
launch, and do not survive a restart. The transcript itself is persisted to
the user's conversation history like any other run.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from atlas.application.chat.runs.follow_up import _revalidate_saved_workspace
from atlas.application.chat.runs.launcher import (
    MAX_PROMPT_CHARS,
    LaunchRefused,
    _set_result,
    _start_child_run,
    list_launch_options,
    resolve_model,
    resolve_workspace,
)
from atlas.application.chat.runs.registry import RunStatus, get_run_registry
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email

__all__ = [
    "API_SOURCE",
    "agent_runs_api_enabled",
    "cancel_api_run",
    "follow_up_api_run",
    "get_api_run",
    "launch_api_run",
    "list_api_runs",
    "list_api_options",
    "reset_idempotency_cache",
]

logger = logging.getLogger(__name__)

API_SOURCE = "agent_runs_api"
API_ACTOR = "service"

# Idempotency keys outlive the run record on purpose: a retry that arrives
# after the run was reaped must still not launch the work a second time.
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
MAX_IDEMPOTENCY_ENTRIES = 10000


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


@dataclass
class _IdempotencyEntry:
    fingerprint: str
    created_at: float
    handle: Optional[Dict[str, Any]] = None  # None while the first request is admitting


_idempotency: Dict[Tuple[str, str], _IdempotencyEntry] = {}


def reset_idempotency_cache() -> None:
    _idempotency.clear()


def _fingerprint(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _prune_idempotency(now: float) -> None:
    expired = [
        key
        for key, entry in _idempotency.items()
        if now - entry.created_at > IDEMPOTENCY_TTL_SECONDS
    ]
    for key in expired:
        del _idempotency[key]
    if len(_idempotency) > MAX_IDEMPOTENCY_ENTRIES:
        oldest = sorted(_idempotency.items(), key=lambda item: item[1].created_at)
        for key, entry in oldest[: len(_idempotency) - MAX_IDEMPOTENCY_ENTRIES]:
            if entry.handle is not None:
                del _idempotency[key]


def _reserve(
    user_email: str, key: Optional[str], payload: Dict[str, Any]
) -> Tuple[Optional[Tuple[str, str]], Optional[Dict[str, Any]]]:
    """Claim ``key`` for this request, or return the handle it already produced.

    Synchronous on purpose: the reservation is made before the first await of
    admission, so two concurrent requests with one key cannot both launch.
    """
    if not key:
        return None, None
    now = time.time()
    _prune_idempotency(now)
    slot = (normalize_user_email(user_email), key)
    fingerprint = _fingerprint(payload)
    entry = _idempotency.get(slot)
    if entry is not None:
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
        return slot, _replayed(entry.handle)
    _idempotency[slot] = _IdempotencyEntry(fingerprint=fingerprint, created_at=now)
    return slot, None


def _replayed(handle: Dict[str, Any]) -> Dict[str, Any]:
    record = get_run_registry().get(handle["run_id"])
    replay = dict(handle)
    replay["status"] = record.status.value if record is not None else "expired"
    replay["idempotent_replay"] = True
    return replay


def _release(slot: Optional[Tuple[str, str]], handle: Optional[Dict[str, Any]]) -> None:
    if slot is None:
        return
    if handle is None:
        _idempotency.pop(slot, None)
    else:
        entry = _idempotency.get(slot)
        if entry is not None:
            entry.handle = dict(handle)


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
    """``atlas_discover_launch_options`` for ``user_email``."""
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
    """``atlas_launch`` for ``user_email``: a new top-level conversation.

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

    slot, replay = _reserve(
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
    try:
        resolved = resolve_workspace(factory.workspace_repository, workspace, user_email)
        await resolve_model(config_manager, model, user_email)
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
            top_level=True,
            refuse_approval_gated=not allow_approval_pause,
            source=API_SOURCE,
            actor=API_ACTOR,
            compliance_level=compliance_level,
        )
        handle = _handle(record, spec, admin_gated)
        _audit("launch", record, spec)
        return handle
    finally:
        _release(slot, handle)


def _api_record(run_id: str) -> Any:
    """The API-started run ``run_id``, or a not-found refusal.

    Runs started anywhere else -- the browser, ``atlas_launch`` -- answer
    exactly like ids that never existed, so the API cannot be used to probe
    or act on them.
    """
    registry = get_run_registry()
    registry.reap_terminal()
    record = registry.get(run_id)
    if record is None or (record.launch_spec or {}).get("source") != API_SOURCE:
        raise LaunchRefused(
            "No agent run with that id. Runs are kept for about 30 minutes "
            "after they finish, on the server that accepted them.",
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
    """``atlas_result``: status, timestamps, error and final reply of one run."""
    return _status(_api_record(run_id))


def list_api_runs(user_email: Optional[str] = None) -> List[Dict[str, Any]]:
    """``atlas_get_runs``: API-started runs, optionally for one user, newest first."""
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


async def follow_up_api_run(
    factory: Any,
    run_id: str,
    *,
    message: str,
    allow_approval_pause: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """``atlas_follow_up``: the next user turn of an API run's conversation.

    Runs as the conversation's owner, on the workspace, model and compliance
    level it was launched with -- the caller cannot re-choose any of them --
    each re-checked against that user's access now. The chat service hydrates
    the stored transcript first, so the run sees its whole history.
    """
    config_manager = _require_ground(factory)
    message = _check_message(message)
    addressed = _api_record(run_id)
    user_email = addressed.user_email
    _check_user_allowed(config_manager, user_email)

    slot, replay = _reserve(
        user_email,
        idempotency_key,
        {
            "op": "follow_up",
            "conversation_id": addressed.conversation_id,
            "message": message,
            "allow_approval_pause": allow_approval_pause,
        },
    )
    if replay is not None:
        return replay
    handle: Optional[Dict[str, Any]] = None
    try:
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
        workspace = _revalidate_saved_workspace(factory, spec, user_email)
        await resolve_model(config_manager, spec["model"], user_email)
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
        )
        new_spec["previous_run_id"] = target.run_id
        handle = _handle(record, new_spec, admin_gated)
        handle["previous_run_id"] = target.run_id
        _audit("follow_up", record, new_spec, previous_run=target.run_id)
        return handle
    finally:
        _release(slot, handle)


def cancel_api_run(run_id: str) -> Dict[str, Any]:
    """Stop an API run (and anything it launched), as the UI's stop does."""
    record = _api_record(run_id)
    registry = get_run_registry()
    cancelled = registry.cancel(record.run_id, record.user_email)
    _audit("cancel", record, record.launch_spec or {}, cancelled=cancelled)
    payload = _status(registry.get(run_id) or record)
    payload["cancelled"] = cancelled
    return payload
