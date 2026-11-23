"""Sub-conversations read from shared state (issue #1222).

``atlas_get_subagent_runs`` and ``atlas_get_subagent_result`` first look in the
local run registry. That registry only knows the runs this process started, and
only for the retention window after they finish, so on its own a parent turn
that lands on another replica, or after the retention window or a restart,
would be told its children do not exist while they run or sit saved elsewhere.

This module answers what the local registry cannot, from two shared places:

* the shared run records in Redis (``run_directory.py``), for a child that is
  live on another replica or finished within the retention window. The owner
  mirrors the run's status and final reply into the record;
* the stored child conversations (``parent_conversation_id`` and
  ``launch_run_id`` in the child's metadata, written server-side), for a child
  whose run records are gone.

Authorization is the registry path's, unchanged: a caller sees only children of
its own conversation that its own user owns. A run id that is unknown, belongs
to another user or hangs off another conversation yields no answer here, and the
caller reports the one "not a sub-conversation" refusal for all three.

``follow_up`` is not served from here: continuing a child needs its owning
replica, which is still the launching one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from atlas.application.chat.runs.control_bus import get_run_control_bus
from atlas.application.chat.runs.leases import shared_view
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email
from atlas.domain.conversation_lineage import launch_run_id_from_metadata
from atlas.domain.conversation_origin import origin_from_metadata

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# Shown for a stored child whose outcome was never recorded (it predates the
# recorded outcome, or its replica died before writing it). Reported as
# ``failed``, the status an interrupted run gets, so it is never taken for a
# success.
UNRECORDED_OUTCOME_ERROR = "The run's outcome was not recorded."


def record_read_failure(exc: BaseException, op: str) -> None:
    """Count a shared-state read error; a Redis one also marks Redis down (#1129)."""
    try:
        from atlas.core.coordination_metrics import coordination_metrics
        from atlas.infrastructure.redis.client import is_redis_connectivity_error

        metrics = coordination_metrics()
        metrics.incr("atlas_shared_child_read_failures_total", op=op)
        if is_redis_connectivity_error(exc):
            metrics.dependency_failed("redis", exc, op=op)
    except Exception:  # pragma: no cover - bookkeeping must never escape
        logger.debug("recording a shared read failure failed", exc_info=True)


def outcome_from_metadata(metadata: Any) -> Optional[str]:
    """The terminal status recorded on a stored child, or ``None``."""
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("launch_status")
    return status if status in TERMINAL_STATUSES else None


class SharedStateUnavailable(Exception):
    """The stored conversations could not be read, so "not found" is not known."""


def _repository(factory: Any) -> Any:
    if factory is None:
        from atlas.infrastructure.app_factory import app_factory as factory
    return getattr(factory, "conversation_repository", None)


async def _read_record(run_id: str) -> Optional[Dict[str, Any]]:
    """The shared view of one run record; ``None`` only when it is absent.

    Raises :class:`SharedStateUnavailable` when Redis cannot be read: an
    unreadable record is not an absent one.
    """
    bus = get_run_control_bus()
    if bus is None or not run_id:
        return None
    try:
        raw = await bus.directory.record(run_id)
    except Exception as exc:
        logger.warning("shared run record unreadable: %s", type(exc).__name__)
        record_read_failure(exc, "record_read")
        raise SharedStateUnavailable(type(exc).__name__) from exc
    return shared_view(raw)


def _is_child_record(view: Optional[Dict[str, Any]], user_email: str, parent_id: str) -> bool:
    return bool(
        view
        and view.get("user_email") == normalize_user_email(user_email)
        and view.get("parent_conversation_id") == parent_id
    )


def _public_run(view: Dict[str, Any]) -> Dict[str, Any]:
    """A run listed from its shared record, in the shape of a local run."""
    return {
        "run_id": view.get("run_id"),
        "conversation_id": view.get("conversation_id"),
        "status": view.get("status"),
        "waiting_on": view.get("waiting_on"),
        "created_at": view.get("created_at"),
        "updated_at": view.get("updated_at"),
        "ended_at": view.get("ended_at"),
        "error": view.get("error"),
        "end_reason": view.get("end_reason"),
        "detached": bool(view.get("detached", False)),
        "parent_run_id": view.get("parent_run_id"),
        "parent_conversation_id": view.get("parent_conversation_id"),
        "launch_origin": view.get("launch_origin"),
        "depth": view.get("depth") or 0,
        "title": view.get("title"),
        "connection_bound": False,
    }


def _public_stored(child: Dict[str, Any]) -> Dict[str, Any]:
    """A run listed from its stored conversation alone."""
    metadata = child.get("metadata")
    outcome = outcome_from_metadata(metadata)
    depth = metadata.get("launch_depth") if isinstance(metadata, dict) else None
    return {
        "run_id": launch_run_id_from_metadata(metadata),
        "conversation_id": child.get("id"),
        "status": outcome or "failed",
        "waiting_on": None,
        "created_at": child.get("created_at"),
        "updated_at": child.get("updated_at"),
        "ended_at": child.get("updated_at"),
        "error": None if outcome else UNRECORDED_OUTCOME_ERROR,
        "end_reason": None,
        "detached": False,
        "parent_run_id": None,
        "parent_conversation_id": metadata.get("parent_conversation_id") if isinstance(metadata, dict) else None,
        "launch_origin": origin_from_metadata(metadata),
        "depth": depth if isinstance(depth, int) and depth > 0 else 1,
        "title": child.get("title"),
        "connection_bound": False,
    }


async def _stored_children(
    factory: Any, user_email: str, parent_id: str, child_ref: Optional[str] = None
) -> List[Dict[str, Any]]:
    repository = _repository(factory)
    lister = getattr(repository, "list_child_conversations", None)
    if lister is None:
        return []
    try:
        if child_ref:
            return await asyncio.to_thread(lister, parent_id, user_email, child_ref=child_ref)
        return await asyncio.to_thread(lister, parent_id, user_email)
    except Exception as exc:
        raise SharedStateUnavailable(type(exc).__name__) from exc


async def shared_child_runs(
    *,
    user_email: str,
    parent_conversation_id: str,
    known_conversation_ids: "set[str]",
    factory: Any = None,
) -> List[Dict[str, Any]]:
    """Children of ``parent_conversation_id`` the local registry does not hold.

    Live children come from the shared run records of the user's live runs,
    on any replica; the rest from the stored child conversations, each
    described by its run record when one is still kept. A child already listed
    locally (``known_conversation_ids``) is skipped. Raises
    :class:`SharedStateUnavailable` when shared state cannot be read: a short
    list would read as "no such children".
    """
    found: List[Dict[str, Any]] = []
    seen = set(known_conversation_ids)

    bus = get_run_control_bus()
    if bus is not None:
        try:
            live = await bus.runs_for_user(user_email)
        except Exception as exc:
            logger.warning("live run lookup failed: %s", type(exc).__name__)
            record_read_failure(exc, "live_runs")
            raise SharedStateUnavailable(type(exc).__name__) from exc
        wanted = [
            entry for entry in live
            if entry.get("parent_conversation_id") == parent_conversation_id
            and entry.get("conversation_id") not in seen
        ]
        views = await asyncio.gather(*(_read_record(e.get("run_id") or "") for e in wanted))
        for view in views:
            if _is_child_record(view, user_email, parent_conversation_id) and view["conversation_id"] not in seen:
                seen.add(view["conversation_id"])
                found.append(_public_run(view))

    stored = [
        child
        for child in await _stored_children(factory, user_email, parent_conversation_id)
        if child.get("id") not in seen
    ]
    # One round of concurrent reads for the children whose run record may
    # still be kept; a child without a recorded launch run has none.
    launch_runs = [launch_run_id_from_metadata(child.get("metadata")) for child in stored]
    records = await asyncio.gather(*(_read_record(run) if run else _none() for run in launch_runs))
    for child, view in zip(stored, records):
        if child.get("id") in seen:
            continue
        seen.add(child.get("id"))
        if _is_child_record(view, user_email, parent_conversation_id) and view.get("conversation_id") == child.get("id"):
            found.append(_public_run(view))
        else:
            found.append(_public_stored(child))
    return found


async def _none() -> None:
    return None


async def find_shared_child(
    run_id: str,
    *,
    user_email: str,
    parent_conversation_id: str,
    factory: Any = None,
) -> Optional[Dict[str, Any]]:
    """The child ``run_id`` names, or ``None`` when it is not the caller's child.

    Returns ``{"view": <shared run record or None>, "conversation_id": ...,
    "by_run_id": bool}`` (``by_run_id``: a stored child named by its launching
    run id, whose reply must belong to that run alone).
    ``None`` covers an unknown run id, another user's run and a run of another
    conversation alike. A run id is matched against the shared record first,
    then against the stored children's launching run id, and finally against
    their conversation ids (for children stored before the run id was kept).

    Raises :class:`SharedStateUnavailable` when the stored conversations
    cannot be read, which is not the same as "not a child".
    """
    view = await _read_record(run_id)
    if view is not None and _is_child_record(view, user_email, parent_conversation_id):
        return {"view": view, "conversation_id": view.get("conversation_id"), "by_run_id": False, "outcome": None}

    stored = await _stored_children(factory, user_email, parent_conversation_id, run_id)
    by_run = next(
        (c for c in stored if launch_run_id_from_metadata(c.get("metadata")) == run_id), None
    )
    if by_run is not None:
        # The launching run, named by its own id: its answer is its own, so a
        # later run of the conversation must not stand in for it.
        return {
            "view": None,
            "conversation_id": by_run.get("id"),
            "by_run_id": True,
            "outcome": outcome_from_metadata(by_run.get("metadata")),
        }
    by_conversation = next((c for c in stored if c.get("id") == run_id), None)
    if by_conversation is not None:
        conversation_id = by_conversation.get("id")
        return {
            "view": await _live_view(conversation_id, user_email),
            "conversation_id": conversation_id,
            "by_run_id": False,
            "outcome": outcome_from_metadata(by_conversation.get("metadata")),
        }
    return None


async def _live_view(conversation_id: str, user_email: str) -> Optional[Dict[str, Any]]:
    """The run now holding a stored child's conversation, on any replica.

    A child that took a follow-up is running again though its launch run is
    over; its status is that run's, not "finished".
    """
    bus = get_run_control_bus()
    if bus is None:
        return None
    try:
        entry = await bus.run_for_conversation(conversation_id)
    except Exception as exc:
        logger.warning("live run lookup failed: %s", type(exc).__name__)
        record_read_failure(exc, "live_conversation")
        raise SharedStateUnavailable(type(exc).__name__) from exc
    if entry is None or entry.get("user_email") != normalize_user_email(user_email):
        return None
    return await _read_record(entry.get("run_id") or "")


SEPARATE_REPLY_ERROR = (
    "This run's reply could not be read separately from the other runs in this "
    "sub-conversation."
)


async def read_stored_reply(
    conversation_id: str, user_email: str, factory: Any = None, *, single_run: bool = False
) -> Optional[str]:
    """The last assistant message of a stored child, ``""`` when it has none.

    ``None`` when the conversation is gone, or when ``single_run`` is set and
    the conversation holds more than the launch prompt (a follow-up ran, so its
    last reply may answer another run). Raises
    :class:`SharedStateUnavailable` when the store cannot be read.
    """
    repository = _repository(factory)
    if repository is None:
        raise SharedStateUnavailable("no conversation store")
    try:
        conversation = await asyncio.to_thread(
            repository.get_conversation, conversation_id, user_email
        )
    except Exception as exc:
        logger.warning(
            "Could not load stored child %s: %s", sanitize_for_logging(conversation_id), exc
        )
        raise SharedStateUnavailable(type(exc).__name__) from exc
    if not conversation:
        return None
    from atlas.application.chat.runs.launcher import _content_text

    if single_run and sum(
        1 for message in conversation.get("messages", []) if message.get("role") == "user"
    ) > 1:
        return None

    replies = [
        message for message in conversation.get("messages", []) if message.get("role") == "assistant"
    ]
    return _content_text(replies[-1].get("content")) if replies else ""
