"""WebSocket frames for runs controlled on another replica (issue #1128).

A stop or steer that this replica routed to a run's owner answers the client
with the same frames the local path sends, so the client cannot tell which
replica executes the run. A command the owner could not be reached for is
reported as ``run_control_unavailable``; it is never turned into a new turn
for a conversation another replica is still executing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from atlas.domain.conversation_classification import ERROR_CODE as CONVERSATION_CLASSIFICATION_ERROR

logger = logging.getLogger(__name__)

RUN_CONTROL_UNAVAILABLE = "run_control_unavailable"
_UNAVAILABLE_MESSAGE = (
    "This conversation's run is executing on another server that did not answer. "
    "Try again in a moment, or stop the run."
)
_APPROVAL_NUDGES: "set[asyncio.Task]" = set()


def _ids(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"run_id": entry.get("run_id"), "conversation_id": entry.get("conversation_id")}


def steer_frame(answer: Dict[str, Any], entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The frame for a routed steer, or None when the run has finished.

    ``None`` tells the caller to handle the message as a new turn: the run it
    would have steered is gone.
    """
    code = answer.get("code")
    if answer.get("ok"):
        return {"type": "agent_update", "update_type": "steering_queued", **_ids(entry)}
    if code == "run_not_found":
        return None
    if code == "classification_refused":
        return {
            "type": "error",
            "message": answer.get("message"),
            "error_type": CONVERSATION_CLASSIFICATION_ERROR,
            "steering": True,
            **_ids(entry),
        }
    if code in ("steering_queue_full", "conversation_busy"):
        return {"type": "error", "message": answer.get("message"), "error_type": code, **_ids(entry)}
    return {
        "type": "error",
        "message": _UNAVAILABLE_MESSAGE,
        "error_type": RUN_CONTROL_UNAVAILABLE,
        **_ids(entry),
    }


def stop_frame(answer: Dict[str, Any], data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The frame for a routed stop: the run's new status, or an error."""
    payload = answer.get("payload") or {}
    if answer.get("ok") and isinstance(payload.get("run"), dict):
        return {"type": "run_status", "run": payload["run"]}
    if answer.get("code") == "run_not_found":
        # It finished on its own before the stop arrived: nothing to report.
        return None
    return {
        "type": "error",
        "message": "The run is executing on another server that did not answer. Try again in a moment.",
        "error_type": RUN_CONTROL_UNAVAILABLE,
        "run_id": data.get("run_id"),
        "conversation_id": data.get("conversation_id"),
    }


def nudge_approval_owner(user_email: str, outcome: Optional[Dict[str, Any]]) -> None:
    """Wake the executor of an approval this replica just recorded.

    Fire and forget: the executor also polls the shared outcome, so a lost
    nudge only delays the tool by one poll interval.
    """
    if not isinstance(outcome, dict) or not outcome.get("run_id"):
        return
    from atlas.application.chat.runs.run_control import route_approval

    async def _nudge() -> None:
        try:
            answer = await route_approval(
                user_email, outcome.get("run_id"), outcome.get("invocation_id"), outcome.get("tool_call_id"),
            )
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
