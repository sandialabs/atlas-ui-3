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


async def execute_sleep_tool(
    tool_call: ToolCall,
    max_seconds: float,
    context: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    """Wait for the requested duration, clamped to *max_seconds*."""
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

    requested = seconds
    clamped = seconds > max_seconds
    if clamped:
        seconds = max_seconds

    reason = arguments.get("reason")
    logger.info(
        "atlas_agent_sleep: waiting %.3fs (requested %.3fs, max %.3fs)",
        seconds, requested, max_seconds,
    )
    await asyncio.sleep(seconds)

    message = f"Slept for {seconds:g} seconds."
    if clamped:
        message += (
            f" The requested {requested:g} seconds exceeded the maximum of "
            f"{max_seconds:g} seconds per call; call this tool again to keep waiting."
        )
    if isinstance(reason, str) and reason.strip():
        message += f" Reason: {reason.strip()}"

    return ToolResult(
        tool_call_id=tool_call.id,
        content=message,
        success=True,
    )
