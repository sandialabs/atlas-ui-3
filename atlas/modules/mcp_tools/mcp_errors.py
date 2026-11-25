"""Error-detection helpers for MCP tool calls.

Extracted from client.py to keep failure-classification logic in one place.
These match on message text because the concrete exception classes vary
across fastmcp/mcp versions.
"""
from typing import Optional

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


def _is_unauthorized_error(exc: BaseException) -> bool:
    """Return True when an MCP call failed with an HTTP 401 from the server.

    The provider answers 401 when the access token presented by the transport
    has expired or been rotated/revoked server-side. The exception chain is
    walked (``__cause__`` / ``__context__``) because FastMCP may re-raise the
    underlying ``httpx.HTTPStatusError`` from a session task or wrap it in a
    non-httpx exception; the response status is matched for any exception type
    in the chain.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return False

    cur: Optional[BaseException] = exc
    while cur is not None:
        if isinstance(cur, httpx.HTTPStatusError):
            response = getattr(cur, "response", None)
            if getattr(response, "status_code", None) == 401:
                return True
        cur = cur.__cause__ or cur.__context__
    return False
