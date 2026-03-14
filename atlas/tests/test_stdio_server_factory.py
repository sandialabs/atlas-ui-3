"""Tests for create_stdio_server helper."""

import pytest

from atlas.mcp_shared.server_factory import create_stdio_server


def test_creates_fastmcp_instance():
    mcp = create_stdio_server("TestServer")
    assert mcp.name == "TestServer"


def test_state_store_is_blocked():
    """The returned FastMCP instance must use BlockedStateStore."""
    mcp = create_stdio_server("TestServer")
    from atlas.mcp_shared.blocked_state import BlockedStateStore
    # FastMCP stores the raw storage in _state_storage
    assert isinstance(mcp._state_storage, BlockedStateStore)
