"""Repository for conversation persistence operations.

Handles all conversation CRUD, search, and tag operations.
Referential integrity is enforced here rather than via database FK constraints
for DuckDB compatibility.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc
from sqlalchemy.orm import Session, sessionmaker

from .models import ConversationRecord, ConversationTagLink, MessageRecord, TagRecord

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Handles all conversation CRUD, search, and tag operations."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        return self._session_factory()

    def save_conversation(
        self,
        conversation_id: str,
        user_email: str,
        title: Optional[str],
        model: Optional[str],
        messages: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ConversationRecord]:
        """Save or update a conversation with all its messages.

        Upsert: if conversation exists for this user, replaces all messages.
        Returns None if the conversation_id belongs to a different user.
        """
        with self._get_session() as session:
            existing = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()

            if existing:
                existing.title = title or existing.title
                existing.model = model or existing.model
                existing.updated_at = datetime.now(timezone.utc)
                existing.message_count = len(messages)
                if metadata:
                    existing.metadata_json = json.dumps(metadata)

                # Delete old messages
                session.execute(
                    delete(MessageRecord).where(
                        MessageRecord.conversation_id == conversation_id
                    )
                )
                session.flush()

                # Insert new messages
                for i, msg in enumerate(messages):
                    record = MessageRecord(
                        id=msg.get("id", str(uuid.uuid4())),
                        conversation_id=conversation_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        message_type=msg.get("message_type"),
                        timestamp=_parse_timestamp(msg.get("timestamp")),
                        sequence_number=i,
                        metadata_json=json.dumps(msg.get("metadata")) if msg.get("metadata") else None,
                    )
                    session.add(record)

                session.commit()
                return existing
            else:
                # Reject if the id already exists for a different user
                other = session.get(ConversationRecord, conversation_id)
                if other:
                    logger.warning(
                        "Rejected save: conversation %s belongs to a different user",
                        conversation_id,
                    )
                    return None

                conv = ConversationRecord(
                    id=conversation_id,
                    user_email=user_email,
                    title=title,
                    model=model,
                    message_count=len(messages),
                    metadata_json=json.dumps(metadata) if metadata else None,
                )
                session.add(conv)

                for i, msg in enumerate(messages):
                    record = MessageRecord(
                        id=msg.get("id", str(uuid.uuid4())),
                        conversation_id=conversation_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        message_type=msg.get("message_type"),
                        timestamp=_parse_timestamp(msg.get("timestamp")),
                        sequence_number=i,
                        metadata_json=json.dumps(msg.get("metadata")) if msg.get("metadata") else None,
                    )
                    session.add(record)

                session.commit()
                return conv

    def list_conversations(
        self,
        user_email: str,
        limit: int = 50,
        offset: int = 0,
        tag_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List conversations for a user, most recent first."""
        with self._get_session() as session:
            query = session.query(ConversationRecord).filter(
                ConversationRecord.user_email == user_email
            )

            if tag_name:
                tag = session.query(TagRecord).filter(
                    TagRecord.name == tag_name,
                    TagRecord.user_email == user_email,
                ).first()
                if tag:
                    conv_ids = [
                        r.conversation_id
                        for r in session.query(ConversationTagLink).filter(
                            ConversationTagLink.tag_id == tag.id
                        ).all()
                    ]
                    query = query.filter(ConversationRecord.id.in_(conv_ids))
                else:
                    return []

            query = query.order_by(desc(ConversationRecord.updated_at))
            conversations = query.offset(offset).limit(limit).all()

            results = []
            for conv in conversations:
                # Get first assistant message as preview (title already shows user question)
                first_reply = session.query(MessageRecord).filter(
                    MessageRecord.conversation_id == conv.id,
                    MessageRecord.role == "assistant",
                ).order_by(MessageRecord.sequence_number).first()

                preview = ""
                if first_reply and first_reply.content:
                    preview = first_reply.content[:300]

                tag_names = self._get_tag_names(session, conv.id)

                results.append({
                    "id": conv.id,
                    "title": conv.title or preview[:200] or "Untitled",
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": conv.message_count,
                    "preview": preview,
                    "tags": tag_names,
                })

            return results

    def get_conversation(self, conversation_id: str, user_email: str) -> Optional[Dict[str, Any]]:
        """Get a full conversation with all messages."""
        with self._get_session() as session:
            conv = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()

            if not conv:
                return None

            msgs = session.query(MessageRecord).filter(
                MessageRecord.conversation_id == conversation_id,
            ).order_by(MessageRecord.sequence_number).all()

            messages = []
            for msg in msgs:
                msg_data = {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content or "",
                    "message_type": msg.message_type,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                    "sequence_number": msg.sequence_number,
                }
                if msg.metadata_json:
                    try:
                        msg_data["metadata"] = json.loads(msg.metadata_json)
                    except json.JSONDecodeError:
                        msg_data["metadata"] = {}
                else:
                    msg_data["metadata"] = {}
                messages.append(msg_data)

            conv_metadata = {}
            if conv.metadata_json:
                try:
                    conv_metadata = json.loads(conv.metadata_json)
                except json.JSONDecodeError:
                    logger.warning("Corrupt metadata_json for conversation %s", conv.id)

            return {
                "id": conv.id,
                "user_email": conv.user_email,
                "title": conv.title,
                "model": conv.model,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "message_count": conv.message_count,
                "metadata": conv_metadata,
                "messages": messages,
                "tags": self._get_tag_names(session, conv.id),
            }

    def delete_conversation(self, conversation_id: str, user_email: str) -> bool:
        """Delete a single conversation with messages and tag associations."""
        with self._get_session() as session:
            conv = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if not conv:
                return False
            self._delete_conv_cascade(session, conversation_id)
            session.commit()
            return True

    def delete_conversations(self, conversation_ids: List[str], user_email: str) -> int:
        """Delete multiple conversations. Returns count of deleted."""
        with self._get_session() as session:
            convs = session.query(ConversationRecord).filter(
                ConversationRecord.id.in_(conversation_ids),
                ConversationRecord.user_email == user_email,
            ).all()
            count = len(convs)
            for conv in convs:
                self._delete_conv_cascade(session, conv.id)
            session.commit()
            return count

    def export_all_conversations(self, user_email: str) -> List[Dict[str, Any]]:
        """Export all conversations with their full messages for a user."""
        with self._get_session() as session:
            convs = session.query(ConversationRecord).filter(
                ConversationRecord.user_email == user_email,
            ).order_by(desc(ConversationRecord.updated_at)).all()

            results = []
            for conv in convs:
                msgs = session.query(MessageRecord).filter(
                    MessageRecord.conversation_id == conv.id,
                ).order_by(MessageRecord.sequence_number).all()

                messages = []
                for msg in msgs:
                    msg_data = {
                        "role": msg.role,
                        "content": msg.content or "",
                        "message_type": msg.message_type,
                        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                    }
                    if msg.metadata_json:
                        try:
                            msg_data["metadata"] = json.loads(msg.metadata_json)
                        except json.JSONDecodeError:
                            pass
                    messages.append(msg_data)

                conv_metadata = {}
                if conv.metadata_json:
                    try:
                        conv_metadata = json.loads(conv.metadata_json)
                    except json.JSONDecodeError:
                        pass

                results.append({
                    "id": conv.id,
                    "title": conv.title,
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": conv.message_count,
                    "metadata": conv_metadata,
                    "tags": self._get_tag_names(session, conv.id),
                    "messages": messages,
                })

            return results

    def delete_all_conversations(self, user_email: str) -> int:
        """Delete all conversations for a user. Returns count deleted."""
        with self._get_session() as session:
            convs = session.query(ConversationRecord).filter(
                ConversationRecord.user_email == user_email,
            ).all()
            count = len(convs)
            for conv in convs:
                self._delete_conv_cascade(session, conv.id)
            session.commit()
            return count

    def search_conversations(
        self,
        user_email: str,
        query: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search conversations by title or message content."""
        search_pattern = f"%{query}%"
        with self._get_session() as session:
            # Find conversation IDs with matching messages
            msg_conv_ids = [
                r[0] for r in session.query(MessageRecord.conversation_id).join(
                    ConversationRecord,
                    MessageRecord.conversation_id == ConversationRecord.id,
                ).filter(
                    ConversationRecord.user_email == user_email,
                    MessageRecord.content.ilike(search_pattern),
                ).distinct().all()
            ]

            # Find conversation IDs with matching titles
            title_conv_ids = [
                r[0] for r in session.query(ConversationRecord.id).filter(
                    ConversationRecord.user_email == user_email,
                    ConversationRecord.title.ilike(search_pattern),
                ).all()
            ]

            all_ids = list(set(msg_conv_ids + title_conv_ids))
            if not all_ids:
                return []

            conversations = session.query(ConversationRecord).filter(
                ConversationRecord.id.in_(all_ids),
            ).order_by(desc(ConversationRecord.updated_at)).limit(limit).all()

            results = []
            for conv in conversations:
                first_reply = session.query(MessageRecord).filter(
                    MessageRecord.conversation_id == conv.id,
                    MessageRecord.role == "assistant",
                ).order_by(MessageRecord.sequence_number).first()
                preview = first_reply.content[:300] if first_reply and first_reply.content else ""

                results.append({
                    "id": conv.id,
                    "title": conv.title or preview[:200] or "Untitled",
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": conv.message_count,
                    "preview": preview,
                    "tags": self._get_tag_names(session, conv.id),
                })

            return results

    def add_tag(self, conversation_id: str, tag_name: str, user_email: str) -> Optional[str]:
        """Add a tag to a conversation. Creates the tag if it doesn't exist."""
        with self._get_session() as session:
            conv = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if not conv:
                return None

            tag = session.query(TagRecord).filter(
                TagRecord.name == tag_name,
                TagRecord.user_email == user_email,
            ).first()

            if not tag:
                tag = TagRecord(
                    id=str(uuid.uuid4()),
                    name=tag_name,
                    user_email=user_email,
                )
                session.add(tag)
                session.flush()

            # Check if link already exists
            existing = session.query(ConversationTagLink).filter(
                ConversationTagLink.conversation_id == conversation_id,
                ConversationTagLink.tag_id == tag.id,
            ).first()

            if not existing:
                link = ConversationTagLink(
                    conversation_id=conversation_id,
                    tag_id=tag.id,
                )
                session.add(link)

            session.commit()
            return tag.id

    def remove_tag(self, conversation_id: str, tag_id: str, user_email: str) -> bool:
        """Remove a tag from a conversation."""
        with self._get_session() as session:
            conv = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if not conv:
                return False

            tag = session.query(TagRecord).filter(
                TagRecord.id == tag_id,
                TagRecord.user_email == user_email,
            ).first()
            if not tag:
                return False

            session.execute(
                delete(ConversationTagLink).where(
                    ConversationTagLink.conversation_id == conversation_id,
                    ConversationTagLink.tag_id == tag_id,
                )
            )
            session.commit()
            return True

    def list_tags(self, user_email: str) -> List[Dict[str, Any]]:
        """List all tags for a user with conversation counts."""
        with self._get_session() as session:
            tags = session.query(TagRecord).filter(
                TagRecord.user_email == user_email,
            ).order_by(TagRecord.name).all()

            results = []
            for tag in tags:
                count = session.query(ConversationTagLink).filter(
                    ConversationTagLink.tag_id == tag.id,
                ).count()
                results.append({
                    "id": tag.id,
                    "name": tag.name,
                    "conversation_count": count,
                })
            return results

    def update_title(self, conversation_id: str, title: str, user_email: str) -> bool:
        """Update the title of a conversation."""
        with self._get_session() as session:
            conv = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()
            if not conv:
                return False
            conv.title = title
            conv.updated_at = datetime.now(timezone.utc)
            session.commit()
            return True

    def _get_tag_names(self, session: Session, conversation_id: str) -> List[str]:
        """Get tag names for a conversation."""
        links = session.query(ConversationTagLink).filter(
            ConversationTagLink.conversation_id == conversation_id,
        ).all()
        if not links:
            return []
        tag_ids = [link.tag_id for link in links]
        tags = session.query(TagRecord).filter(TagRecord.id.in_(tag_ids)).all()
        return [t.name for t in tags]

    def _delete_conv_cascade(self, session: Session, conversation_id: str) -> None:
        """Delete a conversation and all associated data (manual cascade)."""
        # Delete junction table entries
        session.execute(
            delete(ConversationTagLink).where(
                ConversationTagLink.conversation_id == conversation_id
            )
        )
        # Delete messages
        session.execute(
            delete(MessageRecord).where(
                MessageRecord.conversation_id == conversation_id
            )
        )
        # Delete conversation
        session.execute(
            delete(ConversationRecord).where(
                ConversationRecord.id == conversation_id
            )
        )


def _parse_timestamp(value) -> datetime:
    """Parse a timestamp from various formats."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)
