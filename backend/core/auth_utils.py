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
            if hasattr(servers_config, 'servers'):
                return list(servers_config.servers.keys())
            elif isinstance(servers_config, dict):
                return list(servers_config.keys())
            else:
                return []
        
        def __call__(self, *args, **kwargs):
            return self.check_authorization(*args, **kwargs)
    
    return SimpleAuthManager(auth_check_func)