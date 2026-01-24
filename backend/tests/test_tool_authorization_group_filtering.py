"""Test ToolAuthorizationService group filtering.

This test verifies that MCP server group restrictions are properly enforced
during tool authorization in chat execution.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from application.chat.policies.tool_authorization import ToolAuthorizationService


class MockToolManager:
    """Mock tool manager with configurable server configs."""

    def __init__(self, servers_config: dict):
        self.servers_config = servers_config

    async def get_authorized_servers(self, user_email: str, auth_check_func) -> list:
        """Get list of servers the user is authorized to use."""
        if auth_check_func is None:
            raise TypeError("auth_check_func cannot be None")

        authorized_servers = []
        for server_name, server_config in self.servers_config.items():
            if not server_config.get("enabled", True):
                continue

            required_groups = server_config.get("groups", [])
            if not required_groups:
                authorized_servers.append(server_name)
                continue

            # Check if user is in any of the required groups
            group_checks = [await auth_check_func(user_email, group) for group in required_groups]
            if any(group_checks):
                authorized_servers.append(server_name)
        return authorized_servers


@pytest.mark.asyncio
async def test_tool_authorization_enforces_group_restrictions():
    """
    Test that ToolAuthorizationService properly enforces group restrictions.

    This test verifies that:
    1. Tools from servers requiring specific groups are filtered out for unauthorized users
    2. The authorization service does NOT fail open (return all tools) when group check fails

    Bug context: Previously, ToolAuthorizationService passed None as the auth_check_func
    to get_authorized_servers(), causing a TypeError that was caught and resulted in
    returning all originally selected tools (fail-open behavior).
    """
    # Setup: Create servers with group restrictions
    servers_config = {
        "public_server": {
            "enabled": True,
            "groups": []  # No group restriction - available to all
        },
        "admin_server": {
            "enabled": True,
            "groups": ["admin"]  # Only admin group can access
        },
        "users_server": {
            "enabled": True,
            "groups": ["users"]  # Only users group can access
        }
    }

    tool_manager = MockToolManager(servers_config)
    auth_service = ToolAuthorizationService(tool_manager)

    # User selects tools from all servers
    selected_tools = [
        "public_server_tool1",
        "admin_server_tool1",
        "users_server_tool1",
        "canvas_canvas"
    ]

    # Mock is_user_in_group: user is in "users" group but not "admin"
    async def mock_auth_check(user: str, group: str) -> bool:
        return group == "users"

    with patch("application.chat.policies.tool_authorization.is_user_in_group", mock_auth_check):
        filtered_tools = await auth_service.filter_authorized_tools(
            selected_tools=selected_tools,
            user_email="regular@example.com"
        )

    # Assert: User should NOT have access to admin_server tools
    assert "admin_server_tool1" not in filtered_tools, \
        "Admin tools should be filtered out for non-admin users"

    # canvas_canvas should always be allowed
    assert "canvas_canvas" in filtered_tools, \
        "canvas_canvas should always be allowed"

    # public_server tools should be allowed (no group restriction)
    assert "public_server_tool1" in filtered_tools, \
        "Public server tools should be allowed for all users"

    # users_server tools should be allowed (user is in users group)
    assert "users_server_tool1" in filtered_tools, \
        "Users server tools should be allowed for users in the group"


@pytest.mark.asyncio
async def test_tool_authorization_does_not_fail_open():
    """
    Test that tool authorization does not return all tools when auth check fails.

    This specifically tests the fail-open bug where exceptions in authorization
    cause all originally selected tools to be returned.
    """
    servers_config = {
        "restricted_server": {
            "enabled": True,
            "groups": ["special_group"]
        }
    }

    tool_manager = MockToolManager(servers_config)
    auth_service = ToolAuthorizationService(tool_manager)

    selected_tools = ["restricted_server_secret_tool"]

    # Mock is_user_in_group: user is NOT in special_group
    async def mock_auth_check(user: str, group: str) -> bool:
        return False

    with patch("application.chat.policies.tool_authorization.is_user_in_group", mock_auth_check):
        filtered_tools = await auth_service.filter_authorized_tools(
            selected_tools=selected_tools,
            user_email="unauthorized@example.com"
        )

    # Assert: Restricted tools should NOT be accessible
    assert "restricted_server_secret_tool" not in filtered_tools, \
        "Restricted tools should not be accessible to unauthorized users (fail-open bug)"


@pytest.mark.asyncio
async def test_tool_authorization_with_real_mcp_tool_manager():
    """
    Integration test using the real MCPToolManager to verify auth function is passed.

    This test ensures the ToolAuthorizationService properly integrates with
    the real MCPToolManager.get_authorized_servers method.
    """
    from modules.mcp_tools.client import MCPToolManager

    # Create a real MCPToolManager with test config
    mcp_manager = MCPToolManager(None)
    mcp_manager.servers_config = {
        "public_server": {
            "enabled": True,
            "groups": []
        },
        "admin_server": {
            "enabled": True,
            "groups": ["admin"]
        }
    }

    auth_service = ToolAuthorizationService(mcp_manager)

    selected_tools = [
        "public_server_tool1",
        "admin_server_tool1"
    ]

    # Mock is_user_in_group: user is NOT in admin group
    async def mock_auth_check(user: str, group: str) -> bool:
        return False

    with patch("application.chat.policies.tool_authorization.is_user_in_group", mock_auth_check):
        filtered_tools = await auth_service.filter_authorized_tools(
            selected_tools=selected_tools,
            user_email="user@example.com"
        )

    # If we get here without the fix, the exception handler returns all tools
    # So admin_server_tool1 would incorrectly be included
    assert "admin_server_tool1" not in filtered_tools, \
        "Admin tools should be filtered - auth function must be properly passed"
    assert "public_server_tool1" in filtered_tools, \
        "Public server tools should be accessible"


@pytest.mark.asyncio
async def test_tool_authorization_passes_auth_function_not_none():
    """
    Regression test: Ensure is_user_in_group is passed, not None.

    This test will fail if None is passed to get_authorized_servers.
    """
    call_tracker = {"auth_func_received": None}

    class TrackingToolManager:
        def __init__(self):
            self.servers_config = {"test_server": {"enabled": True, "groups": []}}

        async def get_authorized_servers(self, user_email: str, auth_check_func):
            call_tracker["auth_func_received"] = auth_check_func
            if auth_check_func is None:
                raise TypeError("auth_check_func cannot be None - security vulnerability!")
            return ["test_server"]

    tool_manager = TrackingToolManager()
    auth_service = ToolAuthorizationService(tool_manager)

    await auth_service.filter_authorized_tools(
        selected_tools=["test_server_tool1"],
        user_email="test@example.com"
    )

    # Verify that an actual function was passed, not None
    assert call_tracker["auth_func_received"] is not None, \
        "auth_check_func must not be None - this is a security vulnerability"
    assert callable(call_tracker["auth_func_received"]), \
        "auth_check_func must be callable"
