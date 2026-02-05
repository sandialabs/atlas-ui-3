"""Domain models for sessions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from ..messages.models import ConversationHistory


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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_email": self.user_email,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "history": self.history.to_dict(),
            "context": self.context,
            "active": self.active
        }
    
    def update_timestamp(self) -> None:
        """Update the last modified timestamp."""
        self.updated_at = datetime.now(timezone.utc)
