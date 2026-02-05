"""In-memory session repository implementation."""

import logging
from typing import Dict, Optional
from uuid import UUID

from atlas.domain.errors import SessionNotFoundError
from atlas.domain.sessions.models import Session

logger = logging.getLogger(__name__)


class InMemorySessionRepository:
    """
    In-memory implementation of SessionRepository.

    Stores sessions in a dictionary. Suitable for single-instance deployments
    or testing. For distributed systems, use Redis or database-backed implementation.
    """

    def __init__(self):
        """Initialize empty session storage."""
        self._sessions: Dict[UUID, Session] = {}

    async def get(self, session_id: UUID) -> Optional[Session]:
        """Retrieve a session by ID."""
        return self._sessions.get(session_id)

    async def create(self, session: Session) -> Session:
        """Create and store a new session."""
        self._sessions[session.id] = session
        logger.info(f"Created session {session.id} for user {session.user_email}")
        return session

    async def update(self, session: Session) -> Session:
        """Update an existing session."""
        if session.id not in self._sessions:
            raise SessionNotFoundError(
                f"Session {session.id} not found",
                code="SESSION_NOT_FOUND"
            )
        self._sessions[session.id] = session
        return session

    async def delete(self, session_id: UUID) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            self._sessions[session_id].active = False
            logger.info(f"Deleted session {session_id}")
            del self._sessions[session_id]
            return True
        return False

    async def exists(self, session_id: UUID) -> bool:
        """Check if a session exists."""
        return session_id in self._sessions
