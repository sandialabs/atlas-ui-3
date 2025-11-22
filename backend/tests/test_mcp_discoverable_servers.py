"""Test get_discoverable_servers functionality."""

import pytest
from modules.mcp_tools.client import MCPToolManager


@pytest.mark.asyncio
async def test_get_discoverable_servers_basic():
    """Test that get_discoverable_servers returns servers user can discover but not access."""

    # Create a mock MCPToolManager with test server config
    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "public_server": {
            "enabled": True,
            "groups": [],
            "allow_discovery": True,
            "description": "Public server",
            "author": "Test Team",
            "help_email": "help@test.com"
        },
        "admin_server": {
            "enabled": True,
            "groups": ["admin"],
            "allow_discovery": True,
            "description": "Admin only server",
            "author": "Test Team",
            "short_description": "Admin tools",
            "help_email": "admin@test.com",
            "compliance_level": "SOC2"
        },
        "hidden_server": {
            "enabled": True,
            "groups": ["superusers"],
            "allow_discovery": False,  # Not discoverable
            "description": "Hidden server",
            "author": "Test Team",
            "help_email": "hidden@test.com"
        },
        "user_server": {
            "enabled": True,
            "groups": ["users"],
            "allow_discovery": True,
            "description": "User server",
            "author": "Test Team",
            "help_email": "users@test.com"
        }
    }

    # Mock async auth function - user has no group access
    async def mock_auth_check(user_email: str, group: str) -> bool:
        """Mock auth check that returns False for all groups."""
        return False

    # Test with user who has no access
    discoverable = await mcp_manager.get_discoverable_servers("user@test.com", mock_auth_check)

    # Should include admin_server and user_server (discoverable, user lacks access)
    # Should NOT include public_server (no groups required, so user has access)
    # Should NOT include hidden_server (allow_discovery is False)
    assert "admin_server" in discoverable
    assert "user_server" in discoverable
    assert "public_server" not in discoverable
    assert "hidden_server" not in discoverable

    # Check that discoverable servers have the right structure
    assert discoverable["admin_server"]["server"] == "admin_server"
    assert discoverable["admin_server"]["description"] == "Admin only server"
    assert discoverable["admin_server"]["author"] == "Test Team"
    assert discoverable["admin_server"]["help_email"] == "admin@test.com"
    assert discoverable["admin_server"]["groups"] == ["admin"]
    assert discoverable["admin_server"]["compliance_level"] == "SOC2"
    assert discoverable["admin_server"]["is_discoverable"] is True
    assert discoverable["admin_server"]["has_access"] is False


@pytest.mark.asyncio
async def test_get_discoverable_servers_with_partial_access():
    """Test discoverable servers when user has access to some servers."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["users"],
            "allow_discovery": True,
            "description": "Server 1",
            "author": "Test Team",
            "help_email": "help1@test.com"
        },
        "server2": {
            "enabled": True,
            "groups": ["admin"],
            "allow_discovery": True,
            "description": "Server 2",
            "author": "Test Team",
            "help_email": "help2@test.com"
        },
        "server3": {
            "enabled": True,
            "groups": ["superusers"],
            "allow_discovery": True,
            "description": "Server 3",
            "author": "Test Team",
            "help_email": "help3@test.com"
        }
    }

    # User has access to 'users' group only
    async def mock_auth_check(user_email: str, group: str) -> bool:
        return group == "users"

    discoverable = await mcp_manager.get_discoverable_servers("user@test.com", mock_auth_check)

    # Should include server2 and server3 (discoverable, user lacks access)
    # Should NOT include server1 (user has access via 'users' group)
    assert "server1" not in discoverable
    assert "server2" in discoverable
    assert "server3" in discoverable


@pytest.mark.asyncio
async def test_get_discoverable_servers_disabled_servers():
    """Test that disabled servers are not discoverable."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "enabled_server": {
            "enabled": True,
            "groups": ["admin"],
            "allow_discovery": True,
            "description": "Enabled server",
            "author": "Test Team",
            "help_email": "help@test.com"
        },
        "disabled_server": {
            "enabled": False,
            "groups": ["admin"],
            "allow_discovery": True,
            "description": "Disabled server",
            "author": "Test Team",
            "help_email": "disabled@test.com"
        }
    }

    async def mock_auth_check(user_email: str, group: str) -> bool:
        return False

    discoverable = await mcp_manager.get_discoverable_servers("user@test.com", mock_auth_check)

    # Should include only enabled_server
    # Should NOT include disabled_server
    assert "enabled_server" in discoverable
    assert "disabled_server" not in discoverable


@pytest.mark.asyncio
async def test_get_discoverable_servers_empty():
    """Test when no servers are discoverable."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["admin"],
            "allow_discovery": False,  # Not discoverable
            "description": "Server 1",
            "author": "Test Team",
            "help_email": "help@test.com"
        },
        "server2": {
            "enabled": True,
            "groups": [],  # No groups required
            "allow_discovery": True,
            "description": "Server 2",
            "author": "Test Team",
            "help_email": "help2@test.com"
        }
    }

    async def mock_auth_check(user_email: str, group: str) -> bool:
        return False

    discoverable = await mcp_manager.get_discoverable_servers("user@test.com", mock_auth_check)

    # Should be empty - server1 is not discoverable, server2 has no groups
    assert discoverable == {}


@pytest.mark.asyncio
async def test_get_discoverable_servers_all_access():
    """Test when user has access to all servers."""

    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "server1": {
            "enabled": True,
            "groups": ["admin"],
            "allow_discovery": True,
            "description": "Server 1",
            "author": "Test Team",
            "help_email": "help@test.com"
        },
        "server2": {
            "enabled": True,
            "groups": ["users"],
            "allow_discovery": True,
            "description": "Server 2",
            "author": "Test Team",
            "help_email": "help2@test.com"
        }
    }

    # User has access to all groups
    async def mock_auth_check(user_email: str, group: str) -> bool:
        return True

    discoverable = await mcp_manager.get_discoverable_servers("user@test.com", mock_auth_check)

    # Should be empty - user has access to all servers
    assert discoverable == {}
