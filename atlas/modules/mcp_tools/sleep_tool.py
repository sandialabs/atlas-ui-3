"""Built-in ``atlas_agent_sleep`` pseudo-tool (issue #779).

Async agent work often has to wait: a simulation is running, a job was
submitted, a remote system needs time to settle. A sleep implemented as a
normal MCP server would be killed by ``MCP_CALL_TIMEOUT`` (120s by default),
so the wait is handled in-process instead -- there is no MCP round trip and no
transport timeout to hit.

Cancellation needs no extra plumbing: a stopped run cancels the asyncio task
that drives the turn, and ``asyncio.sleep`` raises ``CancelledError``
immediately, so an in-flight sleep aborts with the rest of the turn.

Progress heartbeats: the wait is broken into heartbeat intervals (capped at
``HEARTBEAT_MAX_INTERVAL`` seconds) so the frontend receives regular
``tool_progress`` frames. Without them a long sleep (up to 2h by default)
sits in ``calling`` status with no signal that it is alive; if the WebSocket
drops the ``tool_complete`` frame is lost and the row spins forever (the
"79-minute 900-second sleep" hang).
"""

import asyncio
import logging
import math
from typing import Any, Awaitable, Callable, Dict, Optional

from atlas.domain.messages.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)

SLEEP_SERVER_NAME = "atlas_agent"
SLEEP_TOOL_NAME = "atlas_agent_sleep"

# Key under which the agent loop hands the tool a per-turn scratchpad. The dict
# is created once per turn and mutated here, which is what makes the cumulative
# budget below a *turn* budget rather than a per-call one.
TURN_BUDGET_KEY = "turn_sleep_budget"

# Heartbeat interval for progress events during a long sleep. The wait is
# broken into chunks of this size (or smaller for short sleeps) so the
# frontend receives regular ``tool_progress`` frames and knows the sleep is
# alive. Capped at 30s to match the default WebSocket ping interval; shorter
# sleeps get proportionally shorter intervals so even a 30s wait gets a few
# beats. Waits at or below the minimum threshold sleep in a single shot --
# they are too short for a heartbeat to matter.
HEARTBEAT_MAX_INTERVAL = 30.0
HEARTBEAT_MIN_SLEEP = 10.0

# Key for the update callback inside the tool execution context dict. The
# sleep tool receives the same ``context`` dict that MCP tools do, and the
# tool executor puts the WebSocket update callback there so MCP tools can
# emit progress. The sleep tool reads it the same way.
UPDATE_CALLBACK_KEY = "update_callback"

SLEEP_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": SLEEP_TOOL_NAME,
        "description": (
            "Pause for a number of seconds before continuing. Use this to wait "
            "for long-running external work (a simulation, a submitted job, a "
            "remote process) before checking on it again. Waits longer than the "
            "configured maximum are shortened to that maximum, so call this "
            "repeatedly to wait longer. The wait is aborted if the run is "
            "stopped."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "How long to wait, in seconds. Must be greater than 0.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional short note about what is being waited on.",
                },
            },
            "required": ["seconds"],
        },
    },
}


def sleep_tool_enabled(app_settings: Any) -> bool:
    """The tool is exposed only when a positive maximum wait is configured.

    ``AGENT_SLEEP_MAX_SECONDS=0`` is therefore the kill switch as well as the
    cap, so admins get both from one knob.
    """
    return get_max_sleep_seconds(app_settings) > 0


def get_max_sleep_seconds(app_settings: Any) -> float:
    try:
        return float(getattr(app_settings, "agent_sleep_max_seconds", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def get_max_turn_sleep_seconds(app_settings: Any) -> float:
    """Total seconds one turn may spend sleeping across all calls.

    Without this the per-call cap bounds nothing that matters: the clamp message
    invites the model to call again, so the shipped defaults would allow
    AGENT_MAX_STEPS x the per-call cap of held connection, session, and MCP
    client state in a single turn.
    """
    try:
        value = float(getattr(app_settings, "agent_sleep_max_turn_seconds", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _turn_budget_remaining(
    context: Optional[Dict[str, Any]], max_turn_seconds: float
) -> tuple[Optional[Dict[str, Any]], float]:
    """Return the turn scratchpad and how many seconds it has left.

    Returns ``(None, max_turn_seconds)`` when no scratchpad was supplied -- the
    caller is then outside the agent loop (tools mode, a direct call), where
    there is no turn to budget and the per-call cap is the only bound.
    """
    if not isinstance(context, dict):
        return None, max_turn_seconds
    budget = context.get(TURN_BUDGET_KEY)
    if not isinstance(budget, dict):
        return None, max_turn_seconds
    try:
        spent = float(budget.get("slept_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        spent = 0.0
    return budget, max(max_turn_seconds - spent, 0.0)


def _heartbeat_interval(total_seconds: float) -> float:
    """Seconds between progress heartbeats for a wait of ``total_seconds``.

    Short waits get proportionally short intervals (a 30s sleep beats every
    3s); long waits are capped at ``HEARTBEAT_MAX_INTERVAL`` so a 2h sleep
    does not flood the WebSocket. Waits at or below the minimum threshold
    return the full duration -- the caller sleeps in a single shot.
    """
    if total_seconds <= HEARTBEAT_MIN_SLEEP:
        return total_seconds
    return min(max(total_seconds / 10.0, HEARTBEAT_MIN_SLEEP / 2.0), HEARTBEAT_MAX_INTERVAL)


async def _sleep_with_heartbeats(
    total_seconds: float,
    tool_call_id: str,
    context: Optional[Dict[str, Any]],
) -> None:
    """Sleep for ``total_seconds``, emitting ``tool_progress`` heartbeats.

    The wait is broken into chunks so the frontend receives regular progress
    frames. Without them a long sleep sits in ``calling`` status with no
    signal that it is alive; if the WebSocket drops the ``tool_complete``
    frame is lost and the row spins forever.

    Cancellation is preserved: ``asyncio.sleep(chunk)`` raises
    ``CancelledError`` immediately on cancel, same as a single long sleep.
    """
    update_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    if isinstance(context, dict):
        cb = context.get(UPDATE_CALLBACK_KEY)
        if callable(cb):
            update_callback = cb

    if update_callback is None or total_seconds <= HEARTBEAT_MIN_SLEEP:
        await asyncio.sleep(total_seconds)
        return

    interval = _heartbeat_interval(total_seconds)
    elapsed = 0.0
    while elapsed < total_seconds:
        chunk = min(interval, total_seconds - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk
        try:
            from atlas.application.chat.utilities.event_notifier import notify_tool_progress
            await notify_tool_progress(
                tool_call_id,
                SLEEP_TOOL_NAME,
                elapsed,
                total_seconds,
                f"waiting {elapsed:g}/{total_seconds:g}s",
                update_callback,
            )
        except Exception:
            logger.debug("Sleep heartbeat failed (non-fatal)", exc_info=True)


async def execute_sleep_tool(
    tool_call: ToolCall,
    max_seconds: float,
    context: Optional[Dict[str, Any]] = None,
    max_turn_seconds: Optional[float] = None,
) -> ToolResult:
    """Wait for the requested duration, clamped to the per-call and turn caps."""
    arguments = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
    raw_seconds = arguments.get("seconds")

    # bool is an int subclass; True would otherwise sleep for one second.
    if isinstance(raw_seconds, bool):
        seconds = None
    else:
        try:
            seconds = float(raw_seconds)
        except (TypeError, ValueError):
            seconds = None

    if seconds is None or math.isnan(seconds) or seconds <= 0:
        error = "'seconds' must be a number greater than 0."
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Sleep failed: {error}",
            success=False,
            error=error,
        )

    if max_turn_seconds is None:
        max_turn_seconds = max_seconds
    budget, remaining = _turn_budget_remaining(context, max_turn_seconds)

    if remaining <= 0:
        # Terminal on purpose, and worded without the "call again" invitation
        # the clamp path uses: the turn has nothing left to spend.
        error = (
            f"This turn has used its total sleep budget of {max_turn_seconds:g} seconds. "
            f"Do not call {SLEEP_TOOL_NAME} again in this turn; continue or finish without waiting."
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Sleep refused: {error}",
            success=False,
            error=error,
        )

    requested = seconds
    cap = min(max_seconds, remaining)
    clamped = seconds > cap
    if clamped:
        seconds = cap

    reason = arguments.get("reason")
    logger.info(
        "atlas_agent_sleep: waiting %.3fs (requested %.3fs, per-call max %.3fs, "
        "turn budget remaining %.3fs)",
        seconds, requested, max_seconds, remaining,
    )
    await _sleep_with_heartbeats(seconds, tool_call.id, context)
    if budget is not None:
        budget["slept_seconds"] = max_turn_seconds - remaining + seconds

    message = f"Slept for {seconds:g} seconds."
    if clamped:
        left = remaining - seconds
        if left > 0:
            message += (
                f" The requested {requested:g} seconds exceeded the {cap:g} seconds "
                f"available for this call; {left:g} seconds of this turn's sleep "
                f"budget remain, so call this tool again to keep waiting."
            )
        else:
            message += (
                f" The requested {requested:g} seconds exceeded this turn's remaining "
                f"sleep budget. Do not call {SLEEP_TOOL_NAME} again in this turn; "
                f"continue or finish without waiting."
            )
    if isinstance(reason, str) and reason.strip():
        message += f" Reason: {reason.strip()}"

    return ToolResult(
        tool_call_id=tool_call.id,
        content=message,
        success=True,
    )
