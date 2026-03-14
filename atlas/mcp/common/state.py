"""Pluggable session state store for MCP servers.

Usage in an MCP server:
    from atlas.mcp.common.state import get_state_store
    mcp = FastMCP("my-server", session_state_store=get_state_store())

Environment variables:
    MCP_STATE_BACKEND: "memory" (default) or "redis"
    MCP_REDIS_URL: Redis connection URL (only when backend=redis)
"""
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_state_store() -> Optional[Any]:
    """Return a session state store based on MCP_STATE_BACKEND env var.

    Returns None for in-memory (FastMCP default), or a RedisStore for
    distributed deployments.
    """
    backend = os.getenv("MCP_STATE_BACKEND", "memory")

    if backend == "redis":
        redis_url = os.getenv("MCP_REDIS_URL", "redis://localhost:6379/0")
        try:
            from key_value.aio.stores.redis import RedisStore
            logger.info("Using Redis session state store: %s", redis_url)
            return RedisStore(url=redis_url)
        except ImportError:
            logger.error(
                "MCP_STATE_BACKEND=redis but pykeyvalue[redis] not installed. "
                "Falling back to in-memory state."
            )
            return None
        except Exception as e:
            logger.error("Failed to connect to Redis (%s): %s. Falling back to in-memory.", redis_url, e)
            return None

    # "memory" or unrecognized → FastMCP default in-memory store
    return None
