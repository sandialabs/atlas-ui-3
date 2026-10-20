"""In-memory session repository implementation."""

import logging
from typing import Dict, Optional
from uuid import UUID

from atlas.domain.errors import SessionConflictError, SessionNotFoundError
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
        """Create and store a session, replacing one this connection owns.

        A record another connection has resumed (a different, set
        ``connection_owner``) is not replaced: a superseded connection's New
        Chat must not take the session back from the one now driving it.
        """
        existing = self._sessions.get(session.id)
        if (
            existing is not None
            and existing.connection_owner
            and session.connection_owner
            and existing.connection_owner != session.connection_owner
        ):
            raise SessionConflictError(
                f"Session {session.id} was taken over by another connection",
                code="SESSION_CONFLICT",
            )
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
        if self._sessions[session.id] is not session:
            # A superseded object: the session was recreated or resumed with
            # a new object since this one was handed out. Writing it back
            # would replace the live session with a stale one.
            raise SessionConflictError(
                f"Session {session.id} was replaced since this copy was read",
                code="SESSION_CONFLICT",
            )
        self._sessions[session.id] = session
        return session

    async def adopt(self, session: Session) -> Session:
        """Install ``session`` as the live object (a resumed session's detached copy)."""
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
