"""Pluggable session-state store for this MCP server.

Vendored from ``atlas.mcp.common.state`` so the container can be built
from this directory alone — without needing the rest of the Atlas
monorepo on ``PYTHONPATH``.

Environment variables:
    MCP_STATE_BACKEND: "memory" (default) or "redis"
    MCP_REDIS_URL:     Redis connection URL (only when backend=redis)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_state_store() -> Optional[Any]:
    """Return a session state store based on ``MCP_STATE_BACKEND``.

    Returns ``None`` for in-memory (FastMCP default), or a Redis-backed
    store for distributed deployments. Falls back to ``None`` on any
    failure to construct/connect (logged at error level).
    """
    backend = os.getenv("MCP_STATE_BACKEND", "memory")
    if backend != "redis":
        return None

    redis_url = os.getenv("MCP_REDIS_URL", "redis://localhost:6379/0")
    try:
        from key_value.aio.stores.redis import RedisStore  # type: ignore
    except ImportError:
        logger.error(
            "MCP_STATE_BACKEND=redis but pykeyvalue[redis] is not installed; "
            "falling back to in-memory state."
        )
        return None
    try:
        logger.info("Using Redis session state store: %s", redis_url)
        return RedisStore(url=redis_url)
    except Exception as e:
        logger.error(
            "Failed to construct RedisStore (%s): %s. Falling back to in-memory.",
            redis_url, e,
        )
        return None
