"""
Minimal utilities for basic chat functionality.
"""

import logging
from fastapi import Request

logger = logging.getLogger(__name__)


def sanitize_for_logging(value: str) -> str:
    """Sanitize user-controlled values for safe logging to prevent log injection attacks."""
    if isinstance(value, str):
        # Escape or remove control characters that could enable log injection
        return value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return str(value)


async def get_current_user(request: Request) -> str:
    """Get current user from request state (set by middleware)."""
    return getattr(request.state, 'user_email', 'test@test.com')
