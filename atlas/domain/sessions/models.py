"""Domain models for sessions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from ..messages.models import ConversationHistory

# Session-context key holding the incognito save floor: how many leading
# history messages were sent while incognito and must never be stored.
SAVE_FLOOR_CONTEXT_KEY = "incognito_save_floor"


@dataclass
class Session:
    """Domain model for a chat session."""
    id: UUID = field(default_factory=uuid4)
    user_email: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history: ConversationHistory = field(default_factory=ConversationHistory)
    context: Dict[str, Any] = field(default_factory=dict)
    active: bool = True
    # The WebSocket connection currently driving this session. A client that
    # reconnects (to this replica or another) and resumes the session takes it
    # over; the old connection's teardown sees it no longer owns the session
    # and leaves it alone (issue #1091).
    connection_owner: Optional[str] = None
    # Version of the stored record this copy was read from or last written as.
    # A versioned shared store refuses a write whose version is stale; the
    # in-memory store ignores it. Not part of the session's data.
    store_version: int = field(default=0, compare=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_email": self.user_email,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "history": self.history.to_dict(),
            "context": self.context,
            "active": self.active,
            "connection_owner": self.connection_owner,
        }

    def update_timestamp(self) -> None:
        """Update the last modified timestamp."""
        self.updated_at = datetime.now(timezone.utc)
