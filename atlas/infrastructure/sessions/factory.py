"""Factory for session repository implementations.

The session store is the extension point called out by issue #760 as a
prerequisite for decoupling session identity from the WebSocket connection:
the default ``InMemorySessionRepository`` is process-local, so a clustered
deployment must either run sticky sessions or plug in a distributed
implementation (Redis, shared DB, etc.) through ``SESSION_REPOSITORY_TYPE``.

Two implementations ship in-tree: ``"memory"`` (process-local, the default
for single-instance development) and ``"redis"`` (shared across replicas,
issue #1091). Further stores register here; the ``SessionRepository``
protocol in ``atlas.interfaces.sessions`` is the contract each must satisfy.
"""

import logging
from typing import Optional

from atlas.infrastructure.sessions.in_memory_repository import (
    InMemorySessionRepository,
)
from atlas.interfaces.sessions import SessionRepository

logger = logging.getLogger(__name__)


def create_session_repository(
    repository_type: str = "memory",
    redis=None,
    session_ttl_seconds: Optional[int] = None,
) -> SessionRepository:
    """Build the session repository selected by ``SESSION_REPOSITORY_TYPE``.

    Args:
        repository_type: ``"memory"`` (default) for the process-local store.
            Unknown values raise ``ValueError`` so a misconfigured deployment
            fails loudly at startup instead of silently falling back to the
            in-memory store (which would re-introduce the sticky-session
            requirement the setting is meant to remove). ``"redis"`` uses
            the shared Redis pool.
        redis: The process's ``RedisClientManager``; required for ``"redis"``.
        session_ttl_seconds: Idle lifetime of a stored session (``"redis"``).

    Returns:
        An instance satisfying the ``SessionRepository`` protocol.
    """
    if repository_type == "memory":
        logger.info("Using InMemorySessionRepository (process-local)")
        return InMemorySessionRepository()

    if repository_type == "redis":
        if redis is None:
            raise ValueError("SESSION_REPOSITORY_TYPE=redis requires a configured REDIS_URL")
        from atlas.infrastructure.sessions.redis_repository import (
            RedisSessionRepository,
        )

        ttl = session_ttl_seconds if session_ttl_seconds is not None else 86400
        logger.info("Using RedisSessionRepository (shared, ttl=%ds)", ttl)
        return RedisSessionRepository(redis, ttl_seconds=ttl)

    raise ValueError(
        f"Unknown SESSION_REPOSITORY_TYPE={repository_type!r}. "
        "Built-in options: 'memory', 'redis'. Another implementation must be "
        "registered in "
        "atlas.infrastructure.sessions.factory.create_session_repository."
    )
