"""SQLAlchemy models for chat history persistence.

Uses String(36) UUIDs and Text for JSON to maximize DuckDB compatibility.
No database-level foreign key constraints since DuckDB does not support
CASCADE or UPDATE on FK-constrained tables. Referential integrity is
enforced in the repository layer.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


def _uuid_default():
    return str(uuid.uuid4())


def _now_utc():
    return datetime.now(timezone.utc)


class ConversationRecord(Base):
    """A saved conversation (session snapshot)."""

    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    model = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)
    message_count = Column(Integer, default=0, nullable=False)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_conversations_user_updated", "user_email", "updated_at"),
    )


class MessageRecord(Base):
    """A single message within a conversation."""

    __tablename__ = "conversation_messages"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    conversation_id = Column(String(36), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=True)
    message_type = Column(String(50), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    sequence_number = Column(Integer, nullable=False, default=0)
    metadata_json = Column(Text, nullable=True)


class ConversationTagLink(Base):
    """Junction table for conversation-tag many-to-many relationship."""

    __tablename__ = "conversation_tags"

    conversation_id = Column(String(36), primary_key=True)
    tag_id = Column(String(36), primary_key=True)


class TagRecord(Base):
    """A user-defined tag for organizing conversations."""

    __tablename__ = "tags"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    name = Column(String(100), nullable=False)
    user_email = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "user_email", name="uq_tag_name_user"),
    )
