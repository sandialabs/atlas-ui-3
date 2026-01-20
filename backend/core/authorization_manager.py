"""Authorization utilities for managing access to resources."""

import logging
from typing import Callable, Awaitable

from modules.config.config_manager import get_app_settings

logger = logging.getLogger(__name__)

AuthCheckFunc = Callable[[str, str], Awaitable[bool]]


class AuthorizationManager:
    """Manages authorization logic for admin access."""

    def __init__(self, auth_check_func: AuthCheckFunc):
        self.auth_check_func = auth_check_func
        self.app_settings = get_app_settings()

    async def is_admin(self, user_email: str) -> bool:
        """Check if a user has admin privileges."""
        return await self.auth_check_func(user_email, self.app_settings.admin_group)


def create_authorization_manager(auth_check_func: AuthCheckFunc) -> AuthorizationManager:
    """Factory function to create an AuthorizationManager."""
    return AuthorizationManager(auth_check_func)