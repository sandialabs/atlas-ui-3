"""
Minimal auth utilities stub for basic chat functionality.
This is a temporary implementation for testing.
"""

import logging
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)


def create_authorization_manager(auth_check_func: Optional[Callable] = None) -> Any:
    """
    Create a simple authorization manager stub.
    For basic chat, this just allows all operations.
    """
    class SimpleAuthManager:
        def __init__(self, auth_func):
            self.auth_func = auth_func or (lambda *args, **kwargs: True)
        
        def check_authorization(self, *args, **kwargs) -> bool:
            """Simple auth check - allows everything for basic chat."""
            return True
        
        def filter_authorized_servers(self, user_email, servers_config, get_server_groups_func):
            """Filter servers based on authorization - for basic chat, allow all."""
            # For now, return all server names as a list
            # logger.info(f"DEBUG AUTH: user={user_email}, servers_config type={type(servers_config)}, has .servers={hasattr(servers_config, 'servers')}")
            if hasattr(servers_config, 'servers'):
                result = list(servers_config.servers.keys())
                # logger.info(f"DEBUG AUTH: Returning servers from .servers attribute: {result}")
                return result
            elif isinstance(servers_config, dict):
                result = list(servers_config.keys())
                # logger.info(f"DEBUG AUTH: Returning servers from dict keys: {result}")
                return result
            else:
                # logger.warning(f"DEBUG AUTH: servers_config is neither dict nor has .servers, returning empty list. Type: {type(servers_config)}")
                return []
        
        def __call__(self, *args, **kwargs):
            return self.check_authorization(*args, **kwargs)
    
    return SimpleAuthManager(auth_check_func)