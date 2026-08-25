"""Factory for session repository implementations.

The session store is the extension point called out by issue #760 as a
prerequisite for decoupling session identity from the WebSocket connection:
the default ``InMemorySessionRepository`` is process-local, so a clustered
deployment must either run sticky sessions or plug in a distributed
implementation (Redis, shared DB, etc.) through ``SESSION_REPOSITORY_TYPE``.

Only the ``"memory"`` implementation ships in-tree. A deployment that needs a
distributed store registers it by extending this factory -- the
``SessionRepository`` protocol in ``atlas.interfaces.sessions`` is the
contract every implementation must satisfy.
"""

import logging

from atlas.infrastructure.sessions.in_memory_repository import (
    InMemorySessionRepository,
)
from atlas.interfaces.sessions import SessionRepository

logger = logging.getLogger(__name__)


def create_session_repository(
    repository_type: str = "memory",
) -> SessionRepository:
    """Build the session repository selected by ``SESSION_REPOSITORY_TYPE``.

    Args:
        repository_type: ``"memory"`` (default) for the process-local store.
            Unknown values raise ``ValueError`` so a misconfigured deployment
            fails loudly at startup instead of silently falling back to the
            in-memory store (which would re-introduce the sticky-session
            requirement the setting is meant to remove).

    Returns:
        An instance satisfying the ``SessionRepository`` protocol.
    """
    if repository_type == "memory":
        logger.info("Using InMemorySessionRepository (process-local)")
        return InMemorySessionRepository()

    raise ValueError(
        f"Unknown SESSION_REPOSITORY_TYPE={repository_type!r}. "
        "Built-in options: 'memory'. A distributed implementation (Redis, "
        "shared DB, etc.) must be registered in "
        "atlas.infrastructure.sessions.factory.create_session_repository."
    )
