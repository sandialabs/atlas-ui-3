"""Test get_authorized_servers with async auth function."""

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.mark.asyncio
async def test_get_authorized_servers_with_async_auth():
    """Test that get_authorized_servers properly handles async auth_check_func."""

    # Create a mock MCPToolManager with test server config
    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["admin", "users"]
        },
        "server2": {
            "enabled": True,
            "groups": ["admin"]
        },
        "server3": {
            "enabled": True,
            "groups": []  # No groups required
        },
        "server4": {
            "enabled": False,
            "groups": ["admin"]
        }
    }

    # Mock async auth function
    async def mock_auth_check(user_email: str, group: str) -> bool:
        """Mock auth check that returns True for admin group."""
        return group == "admin"

    # Test with user who has admin access
    authorized = await mcp_manager.get_authorized_servers("admin@test.com", mock_auth_check)

    # Should include server1 (has admin), server2 (has admin), server3 (no groups required)
    # Should NOT include server4 (disabled)
    assert set(authorized) == {"server1", "server2", "server3"}


@pytest.mark.asyncio
async def test_get_authorized_servers_with_multiple_groups():
    """Test authorization with multiple group checks."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["users", "developers"]
        },
        "server2": {
            "enabled": True,
            "groups": ["admin"]
        }
    }

    # User is in 'users' group but not 'admin'
    async def mock_auth_check(user_email: str, group: str) -> bool:
        return group in ["users", "developers"]

    authorized = await mcp_manager.get_authorized_servers("user@test.com", mock_auth_check)

    # Should include server1 (user is in 'users' group)
    # Should NOT include server2 (user not in 'admin' group)
    assert authorized == ["server1"]


@pytest.mark.asyncio
async def test_get_authorized_servers_no_access():
    """Test when user has no access to any servers."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["admin"]
        },
        "server2": {
            "enabled": True,
            "groups": ["superusers"]
        }
    }

    # User has no group memberships
    async def mock_auth_check(user_email: str, group: str) -> bool:
        return False

    authorized = await mcp_manager.get_authorized_servers("user@test.com", mock_auth_check)

    assert authorized == []
