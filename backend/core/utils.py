"""
Minimal utilities for basic chat functionality.
"""

import logging
from fastapi import Depends, Request

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> str:
    """Get current user from request state (set by middleware)."""
    return getattr(request.state, 'user_email', 'test@test.com')