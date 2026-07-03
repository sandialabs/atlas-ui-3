"""Error-detection helpers for MCP tool calls.

Extracted from client.py to keep failure-classification logic in one place.
These match on message text because the concrete exception classes vary
across fastmcp/mcp versions.
"""
from typing import Any, Optional

_TASK_FORBIDDEN_MARKER = "does not support task-augmented execution"


def _is_task_forbidden_error(exc: BaseException) -> bool:
    """Return True when an MCP call failed because the specific tool refuses
    task-augmented execution (fastmcp `tasks.mode="forbidden"`).

    A server may advertise task capability overall while individual tools
    opt out. fastmcp surfaces that as an `McpError` whose message contains
    "does not support task-augmented execution"
    (see fastmcp/server/tasks/routing.py). We match on the message text
    because the concrete exception class varies across fastmcp versions.
    """
    return _TASK_FORBIDDEN_MARKER in str(exc)


def _is_task_forbidden_result(result: Any) -> bool:
    """Return True when a CallToolResult-shaped object carries the
    task-forbidden marker as an error.

    fastmcp does not always raise on this condition: when the server-side
    McpError is wrapped as a ToolError, the low-level MCP handler converts
    it to a CallToolResult with isError=True instead of propagating an
    exception. The client's _call_tool_as_task then returns a ToolTask whose
    immediate_result is that error result, so the call site sees no
    exception and our fallback never runs.
    """
    if not getattr(result, "is_error", False):
        return False
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", "") or ""
        if _TASK_FORBIDDEN_MARKER in text:
            return True
    return False

_SESSION_TERMINATED_MARKERS = ("session terminated", "session not found", "invalid session id")

def _is_session_terminated_error(exc: BaseException) -> bool:
    """Return True when an MCP call failed because the server-side session
    was terminated or invalidated.

    This can happen when the backing process for a stateful MCP server
    restarts and invalidates its session ID while the transport-level
    connection still appears alive (e.g. HTTP socket open, 404 response).

    The exception chain is walked (``__cause__`` / ``__context__``) because
    FastMCP may wrap the underlying error before surfacing it.
    """
    cur: Optional[BaseException] = exc
    while cur is not None:
        if any(m in str(cur).lower() for m in _SESSION_TERMINATED_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False
