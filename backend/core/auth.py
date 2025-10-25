"""Authentication and authorization module."""

from typing import Optional


def is_user_in_group(user_id: str, group_id: str) -> bool:
    """
    Mock authorization function to check if user is in a group.
    
    Args:
        user_id: User email/identifier
        group_id: Group identifier
        
    Returns:
        True if user is authorized for the group
    """
    # Check if this is debug mode and test user should have admin access
    from modules.config.manager import config_manager
    app_settings = config_manager.app_settings
    
    if (app_settings.debug_mode and 
        user_id == app_settings.test_user and 
        group_id == app_settings.admin_group):
        return True
    
    # Mock implementation - in production this would query actual auth system
    mock_groups = {
        "test@test.com": ["users", "mcp_basic", "admin"],
        "user@example.com": ["users", "mcp_basic"],
        "admin@example.com": ["admin", "users", "mcp_basic", "mcp_advanced"]
    }
    
    user_groups = mock_groups.get(user_id, [])
    return group_id in user_groups


def get_user_from_header(x_email_header: Optional[str]) -> Optional[str]:
    """Extract user email from X-User-Email header."""
    if not x_email_header:
        return None
    return x_email_header.strip()