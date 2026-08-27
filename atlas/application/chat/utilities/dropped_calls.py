"""Publishing the user-facing notice for tool calls the guard discarded.

One helper, called from every path that can produce an ``LLMResponse``:
streaming and non-streaming, tools mode and agent mode. A dropped call keeps the
turn alive, so if the notice is wired into only some of those paths the drop is
silent on the others -- which is the failure it exists to prevent.
"""

from typing import Any, Optional

from atlas.modules.llm.tool_call_guard import dropped_call_warning


async def publish_dropped_call_warning(event_publisher, response: Optional[Any]) -> None:
    """Warn about tool calls dropped from ``response``, if any."""
    if not event_publisher or response is None:
        return
    dropped = getattr(response, "dropped_tool_calls", None)
    if not dropped:
        return
    await event_publisher.publish_warning(
        message=dropped_call_warning(
            dropped,
            truncated=getattr(response, "dropped_tool_calls_truncated", False),
        ),
    )
