"""Built-in ``atlas_agent_sleep`` pseudo-tool (issue #779).

Async agent work often has to wait: a simulation is running, a job was
submitted, a remote system needs time to settle. A sleep implemented as a
normal MCP server would be killed by ``MCP_CALL_TIMEOUT`` (120s by default),
so the wait is handled in-process instead -- there is no MCP round trip and no
transport timeout to hit.

Cancellation needs no extra plumbing: a stopped run cancels the asyncio task
that drives the turn, and ``asyncio.sleep`` raises ``CancelledError``
immediately, so an in-flight sleep aborts with the rest of the turn.
"""

import asyncio
import logging
import math
from typing import Any, Dict, Optional

from atlas.domain.messages.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)

SLEEP_SERVER_NAME = "atlas_agent"
SLEEP_TOOL_NAME = "atlas_agent_sleep"

# Key under which the agent loop hands the tool a per-turn scratchpad. The dict
# is created once per turn and mutated here, which is what makes the cumulative
# budget below a *turn* budget rather than a per-call one.
TURN_BUDGET_KEY = "turn_sleep_budget"

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
    await asyncio.sleep(seconds)
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
