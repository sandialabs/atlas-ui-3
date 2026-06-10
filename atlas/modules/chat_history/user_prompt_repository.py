"""Repository for user prompt library persistence (issue #153).

Handles CRUD for per-user custom system prompts. Mirrors the conventions in
``conversation_repository``: every public method normalizes ``user_email`` at
the entry point so mixed-case identities from different SSO/proxy paths still
hit the same rows, and referential integrity is enforced here rather than via
database FK constraints (DuckDB compatibility).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from atlas.core.user_identity import normalize_user_email

from .models import UserPromptRecord

logger = logging.getLogger(__name__)


class UserPromptRepository:
    """Handles CRUD for per-user custom prompts."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        return self._session_factory()

    @staticmethod
    def _to_dict(record: UserPromptRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "title": record.title,
            "content": record.content,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def list_prompts(self, user_email: str) -> List[Dict[str, Any]]:
        """Return all prompts for a user, most recently updated first."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            records = (
                session.query(UserPromptRecord)
                .filter(UserPromptRecord.user_email == user_email)
                .order_by(desc(UserPromptRecord.updated_at))
                .all()
            )
            return [self._to_dict(r) for r in records]

    def get_prompt(self, prompt_id: str, user_email: str) -> Optional[Dict[str, Any]]:
        """Return a single prompt owned by the user, or None."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = (
                session.query(UserPromptRecord)
                .filter(
                    UserPromptRecord.id == prompt_id,
                    UserPromptRecord.user_email == user_email,
                )
                .first()
            )
            return self._to_dict(record) if record else None

    def create_prompt(
        self, user_email: str, title: str, content: str
    ) -> Dict[str, Any]:
        """Create a new prompt for the user and return it."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = UserPromptRecord(
                user_email=user_email,
                title=title.strip(),
                content=content,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            logger.info("Created user prompt %s for %s", record.id, user_email)
            return self._to_dict(record)

    def update_prompt(
        self,
        prompt_id: str,
        user_email: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a prompt owned by the user. Returns None if not found."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = (
                session.query(UserPromptRecord)
                .filter(
                    UserPromptRecord.id == prompt_id,
                    UserPromptRecord.user_email == user_email,
                )
                .first()
            )
            if not record:
                return None
            if title is not None:
                record.title = title.strip()
            if content is not None:
                record.content = content
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            return self._to_dict(record)

    def delete_prompt(self, prompt_id: str, user_email: str) -> bool:
        """Delete a prompt owned by the user. Returns True if a row was removed."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = (
                session.query(UserPromptRecord)
                .filter(
                    UserPromptRecord.id == prompt_id,
                    UserPromptRecord.user_email == user_email,
                )
                .first()
            )
            if not record:
                return False
            session.delete(record)
            session.commit()
            logger.info("Deleted user prompt %s for %s", prompt_id, user_email)
            return True
