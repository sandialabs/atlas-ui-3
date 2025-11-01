"""Session repository interface."""

from typing import Protocol, Optional
from uuid import UUID

from domain.sessions.models import Session


class SessionRepository(Protocol):
    """
    Port for session storage and retrieval.
    
    Abstracts session persistence from the application layer,
    allowing different storage implementations (in-memory, Redis, DB, etc.).
    """
    
    async def get(self, session_id: UUID) -> Optional[Session]:
        """
        Retrieve a session by ID.
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            Session if found, None otherwise
        """
        ...
    
    async def create(self, session: Session) -> Session:
        """
        Create and store a new session.
        
        Args:
            session: Session to create
            
        Returns:
            Created session
        """
        ...
    
    async def update(self, session: Session) -> Session:
        """
        Update an existing session.
        
        Args:
            session: Session to update
            
        Returns:
            Updated session
        """
        ...
    
    async def delete(self, session_id: UUID) -> bool:
        """
        Delete a session.
        
        Args:
            session_id: ID of session to delete
            
        Returns:
            True if deleted, False if not found
        """
        ...
    
    async def exists(self, session_id: UUID) -> bool:
        """
        Check if a session exists.
        
        Args:
            session_id: Session ID to check
            
        Returns:
            True if session exists, False otherwise
        """
        ...
