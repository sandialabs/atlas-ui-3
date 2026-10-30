"""Decide what a tool-approval response did (issue #1122).

The executor replica holds the in-process future. A response can land on
another replica, or arrive twice. The first write of the shared outcome wins.
A later response returns that outcome instead of "no matching approval", and
it does not resume a run this replica does not own.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.runs.execution_state import (
    approval_storage_key,
    get_execution_store,
    list_execution_snapshots,
    public_projection,
)
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

Kind = str  # "accepted" | "duplicate" | "unknown"


def _frame(outcome: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "tool_approval_settled",
        "already_resolved": True,
        "approved": bool(outcome.get("approved")),
        "tool_call_id": outcome.get("tool_call_id") or data.get("tool_call_id"),
        "invocation_id": outcome.get("invocation_id") or data.get("invocation_id"),
        "run_id": outcome.get("run_id") or data.get("run_id"),
        "status": outcome.get("status") or ("approved" if outcome.get("approved") else "rejected"),
    }


def _unknown(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "error",
        "message": "No matching pending tool approval",
        "error_type": "unknown_tool_approval",
        "tool_call_id": data.get("tool_call_id"),
        "invocation_id": data.get("invocation_id"),
        "run_id": data.get("run_id"),
    }


def _outcome(data: Dict[str, Any], request=None, user_email: Optional[str] = None) -> Dict[str, Any]:
    approved = bool(data.get("approved"))
    invocation_id = data.get("invocation_id") or getattr(request, "invocation_id", None)
    run_id = data.get("run_id") or getattr(request, "run_id", None)
    tool_call_id = data.get("tool_call_id") or getattr(request, "tool_call_id", None)
    owner = user_email or getattr(request, "user_email", None) or ""
    return {
        "approved": approved,
        "arguments": data.get("arguments"),
        "reason": data.get("reason"),
        "tool_call_id": tool_call_id,
        "invocation_id": invocation_id,
        "run_id": run_id,
        "user_email": normalize_user_email(owner),
        "status": "approved" if approved else "rejected",
    }


async def _put(key: Optional[str], outcome: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    try:
        return await get_execution_store().put_resolution(key, outcome)
    except Exception:
        logger.warning("approval outcome write failed", exc_info=True)
        return None


async def _read(key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    try:
        return await get_execution_store().read_resolution(key)
    except Exception:
        logger.warning("approval outcome read failed", exc_info=True)
        return None


def _owns(owner: Optional[str], user_email: str) -> bool:
    if not owner:
        return True
    return normalize_user_email(owner) == normalize_user_email(user_email)


async def _pending_matches(user_email: str, data: Dict[str, Any]):
    invocation_id = data.get("invocation_id")
    tool_call_id = data.get("tool_call_id")
    run_id = data.get("run_id")
    conversation_id = data.get("conversation_id")
    projections = []
    try:
        store = get_execution_store()
        if conversation_id:
            doc = await store.get(str(conversation_id), user_email)
            if doc:
                projections = [public_projection(doc)]
        else:
            projections = await list_execution_snapshots(user_email)
    except Exception:
        logger.warning("approval projection scan failed", exc_info=True)
        return []
    matches = []
    for projection in projections:
        if run_id and projection.get("run_id") and projection["run_id"] != run_id:
            continue
        for request in projection.get("pending_requests") or []:
            if invocation_id:
                if request.get("invocation_id") == invocation_id:
                    matches.append(request)
                continue
            if tool_call_id and request.get("tool_call_id") == tool_call_id:
                if run_id and request.get("run_id") and request["run_id"] != run_id:
                    continue
                matches.append(request)
    return matches


async def decide_tool_approval(data: Dict[str, Any], user_email: str) -> Tuple[Kind, Optional[Dict[str, Any]]]:
    """Classify one approval response.

    ``accepted`` means this response woke a local future or recorded the
    outcome for the executor replica to observe. ``duplicate`` carries the
    settled frame and must not resume the run. ``unknown`` carries the error
    frame.
    """
    manager = get_approval_manager()
    tool_call_id = data.get("tool_call_id")
    invocation_id = data.get("invocation_id")
    run_id = data.get("run_id")
    request = manager.find_open_request(tool_call_id, invocation_id, run_id)
    if request is not None:
        # A user who does not own the local request must not fall through to
        # the projection scan and write the shared outcome.
        if not _owns(request.user_email, user_email):
            return "unknown", _unknown(data)
        if not request.future.done():
            # The shared outcome is claimed before the local future. A rejection
            # that already won on another replica is what this future resolves
            # to, including when this response said approved.
            outcome = _outcome(data, request, user_email)
            key = approval_storage_key(
                outcome.get("invocation_id"), outcome.get("tool_call_id"), user_email,
            )
            stored = await _put(key, outcome)
            if stored and stored.get("already_resolved"):
                request.set_response(
                    bool(stored.get("approved")),
                    stored.get("arguments"),
                    stored.get("reason"),
                )
                return "duplicate", _frame(stored, data)
            result = manager.handle_approval_response(
                tool_call_id=tool_call_id or request.tool_call_id or "",
                approved=bool(data.get("approved")),
                arguments=data.get("arguments"),
                reason=data.get("reason"),
                user_email=user_email,
                invocation_id=invocation_id or request.invocation_id,
                run_id=run_id or request.run_id,
            )
            if result is False:
                return "unknown", _unknown(data)
            return "accepted", None
        remembered = manager.handle_approval_response(
            tool_call_id=tool_call_id or request.tool_call_id or "",
            approved=bool(data.get("approved")),
            arguments=data.get("arguments"),
            reason=data.get("reason"),
            user_email=user_email,
            invocation_id=invocation_id or request.invocation_id,
            run_id=run_id or request.run_id,
        )
        if isinstance(remembered, dict) and remembered.get("already_resolved"):
            return "duplicate", _frame(remembered, data)
        return "unknown", _unknown(data)

    result = manager.handle_approval_response(
        tool_call_id=tool_call_id or "",
        approved=bool(data.get("approved")),
        arguments=data.get("arguments"),
        reason=data.get("reason"),
        user_email=user_email,
        invocation_id=invocation_id,
        run_id=run_id,
    )
    if result is True:
        found = manager.find_open_request(tool_call_id, invocation_id, run_id)
        outcome = _outcome(data, found, user_email)
        key = approval_storage_key(
            outcome.get("invocation_id"), outcome.get("tool_call_id"), user_email,
        )
        await _put(key, outcome)
        return "accepted", None
    if isinstance(result, dict) and result.get("already_resolved"):
        return "duplicate", _frame(result, data)

    key = approval_storage_key(invocation_id, tool_call_id, user_email)
    existing = await _read(key)
    if existing and _owns(existing.get("user_email"), user_email):
        return "duplicate", _frame(existing, data)

    matches = await _pending_matches(user_email, data)
    if len(matches) != 1:
        return "unknown", _unknown(data)
    request = matches[0]
    outcome = _outcome(data, user_email=user_email)
    for field in ("invocation_id", "run_id", "tool_call_id"):
        if not outcome.get(field) and request.get(field):
            outcome[field] = request[field]
    storage = approval_storage_key(
        outcome.get("invocation_id"), outcome.get("tool_call_id"), user_email,
    )
    stored = await _put(storage, outcome)
    if stored and stored.get("already_resolved"):
        return "duplicate", _frame(stored, data)
    return "accepted", None
