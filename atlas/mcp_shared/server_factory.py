"""Factory for creating MCP server instances with appropriate state stores."""

from __future__ import annotations

from fastmcp import FastMCP

from atlas.mcp_shared.blocked_state import BlockedStateStore


def create_stdio_server(name: str, **kwargs) -> FastMCP:
    """Create a FastMCP instance for STDIO transport with state blocked.

    STDIO servers share a single process across all users, so session state
    is not isolated. This factory injects a BlockedStateStore that raises
    RuntimeError if any tool attempts to use ctx.get_state/ctx.set_state.

    For stateful servers, use HTTP transport instead.

    Args:
        name: Server display name
        **kwargs: Additional arguments passed to FastMCP()

    Returns:
        FastMCP instance with BlockedStateStore
    """
    return FastMCP(name, session_state_store=BlockedStateStore(), **kwargs)
