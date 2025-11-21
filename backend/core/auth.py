"""Authentication and authorization module."""

import logging
from typing import Optional

import httpx
from modules.config.config_manager import config_manager

logger = logging.getLogger(__name__)


async def is_user_in_group(user_id: str, group_id: str) -> bool:
    """
    Check if a user is in a specified group.

    This function first checks for a configured external authorization endpoint.
    If available, it makes an HTTP request to check group membership.
    If not configured, it falls back to a mock implementation for local development.

    Args:
        user_id: User email/identifier.
        group_id: Group identifier.

    Returns:
        True if the user is in the group, False otherwise.
    """
    app_settings = config_manager.app_settings
    auth_url = app_settings.auth_group_check_url
    api_key = app_settings.auth_group_check_api_key

    if auth_url and api_key:
        # Use the external HTTP endpoint for authorization
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                payload = {"user_id": user_id, "group_id": group_id}
                response = await client.post(auth_url, json=payload, headers=headers, timeout=5.0)
                response.raise_for_status()
                # Assuming the endpoint returns a simple JSON like {"is_member": true}
                return response.json().get("is_member", False)
        except httpx.RequestError as e:
            logger.error(f"HTTP request to auth endpoint failed: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error during external auth check: {e}", exc_info=True)
            return False
    else:
        # Everybody is in the users group by default
        if (group_id == "users"):
            return True
        # Fallback to mock implementation if no external endpoint is configured
        if (app_settings.debug_mode and
                user_id == app_settings.test_user and
                group_id == app_settings.admin_group):
            return True

        mock_groups = {
            "test@test.com": ["users", "mcp_basic", "admin"],
            "user@example.com": ["users", "mcp_basic"],
            "admin@example.com": ["admin", "users", "mcp_basic", "mcp_advanced"]
        }
        user_groups = mock_groups.get(user_id, [])
        return group_id in user_groups


def get_user_from_header(x_email_header: Optional[str]) -> Optional[str]:
    """Extract user email from authentication header value."""
    if not x_email_header:
        return None
    return x_email_header.strip()