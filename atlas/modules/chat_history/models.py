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


class UserPromptRecord(Base):
    """A user-authored custom system prompt (prompt library entry).

    Each row is one reusable prompt belonging to a single user. When selected
    as the active prompt for a chat, its ``content`` replaces the default
    system prompt for that turn (see issue #153).
    """

    __tablename__ = "user_prompts"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_user_prompts_user_updated", "user_email", "updated_at"),
    )


class UserWorkspaceRecord(Base):
    """A user-defined workspace: a named bundle of chat context selections.

    A workspace snapshots the prompt, RAG data sources, and MCP tool selections
    that belong together for one context (e.g. "Work", "Home", "Project A") so
    the user can switch between them in one click. The selections themselves
    live in ``config_json`` rather than in columns because their shape is owned
    by the frontend selection model and grows over time; the repository
    normalizes the payload on the way in so a malformed blob can never reach
    the UI.
    """

    __tablename__ = "user_workspaces"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(String(500), nullable=True)
    config_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_user_workspaces_user_updated", "user_email", "updated_at"),
    )


# ---------------------------------------------------------------------------
# Team-shared prompt library (issue #1169)
#
# A registry entry is private to its owner until it is shared. Sharing is a set
# of grants: a group grant gives the members of an Atlas group (checked with
# ``atlas.core.auth.is_user_in_group``) permission to view and use published
# versions; a user grant names one identity and may also give edit or manage
# rights. Published versions are immutable rows -- the repository never issues
# an UPDATE against ``prompt_library_versions`` -- so a conversation that
# pinned a version id can always be shown the exact text it ran with.
# ---------------------------------------------------------------------------


class PromptLibraryPromptRecord(Base):
    """A prompt-library entry: metadata plus the owner's editable draft."""

    __tablename__ = "prompt_library_prompts"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    owner_email = Column(String(255), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(String(1000), nullable=True)
    draft_content = Column(Text, nullable=False)
    # Denormalized pointer to the newest published version; the versions table
    # stays authoritative (unique on prompt_id + version_number).
    latest_version_id = Column(String(36), nullable=True)
    latest_version_number = Column(Integer, nullable=False, default=0)
    forked_from_prompt_id = Column(String(36), nullable=True)
    forked_from_version_id = Column(String(36), nullable=True)
    # Soft delete: an archived prompt is hidden and unusable for new turns,
    # but its versions stay readable for the conversations that pinned them.
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_prompt_library_prompts_owner_updated", "owner_email", "updated_at"),
    )


class PromptLibraryVersionRecord(Base):
    """One immutable published version of a prompt-library entry."""

    __tablename__ = "prompt_library_versions"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    prompt_id = Column(String(36), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    change_description = Column(String(1000), nullable=True)
    author_email = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)

    __table_args__ = (
        # Two concurrent publishes (two replicas, two tabs) race for the same
        # number; the loser's insert fails here and it retries with the next.
        UniqueConstraint("prompt_id", "version_number", name="uq_prompt_library_version_number"),
    )


class PromptLibraryGrantRecord(Base):
    """A sharing grant on a prompt-library entry.

    ``grantee_type`` is ``group`` (an Atlas group name, permission always
    ``use``) or ``user`` (a normalized identity, permission ``use``, ``edit`` or
    ``manage``). The owner is never a grant.
    """

    __tablename__ = "prompt_library_grants"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    prompt_id = Column(String(36), nullable=False, index=True)
    grantee_type = Column(String(10), nullable=False)
    grantee = Column(String(255), nullable=False)
    permission = Column(String(10), nullable=False)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_prompt_library_grants_grantee", "grantee_type", "grantee"),
    )


class PromptLibraryAuditRecord(Base):
    """A durable governance event for a prompt-library entry.

    ``details_json`` carries non-sensitive change details only (field names,
    group names, version numbers) -- never prompt text.
    """

    __tablename__ = "prompt_library_audit_events"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    prompt_id = Column(String(36), nullable=False, index=True)
    version_id = Column(String(36), nullable=True)
    event_type = Column(String(50), nullable=False)
    actor_email = Column(String(255), nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_prompt_library_audit_prompt_created", "prompt_id", "created_at"),
    )
