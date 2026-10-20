"""Repository for conversation persistence operations.

Handles all conversation CRUD, search, and tag operations.
Referential integrity is enforced here rather than via database FK constraints
for DuckDB compatibility.

User identity invariant: every public method that takes ``user_email``
normalizes it via ``normalize_user_email`` at the entry point. This is the
single chokepoint, so callers can pass mixed-case emails (different SSO/
proxy paths sometimes deliver ``Alice@Test.com`` vs ``alice@test.com``)
and still hit the same row. ``get_conversation_owner`` also normalizes its
return value so legacy rows written before normalization still compare
correctly against the authenticated user.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc
from sqlalchemy.orm import Session, sessionmaker

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email
from atlas.domain.conversation_classification import (
    CLASSIFICATION_METADATA_KEY,
    public_fields,
)
from atlas.domain.conversation_lineage import (
    PARENT_CONVERSATION_METADATA_KEY,
    iter_recorded_links,
    lineage_fields,
    parent_conversation_id_from_metadata,
)

from .models import ConversationRecord, ConversationTagLink, MessageRecord, TagRecord

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Handles all conversation CRUD, search, and tag operations."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        return self._session_factory()

    def _visible_parent_ids(
        self,
        session: Session,
        user_email: str,
        conversations: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Parent links the caller is allowed to see, keyed by child id.

        A recorded parent is only published when the parent row exists *and*
        belongs to the same user. A deleted parent, a filtered one, or an id
        that belongs to someone else therefore reads exactly like a conversation
        that never had a parent, and the child renders as a top-level entry.
        The parent's title/model/classification are never consulted here, so no
        information beyond the id the child already records can leak.
        """
        links = dict(iter_recorded_links(conversations))
        if not links:
            return {}
        parent_ids = set(links.values())
        owned = {
            row[0]
            for row in session.query(ConversationRecord.id).filter(
                ConversationRecord.id.in_(parent_ids),
                ConversationRecord.user_email == user_email,
            ).all()
        }
        return {
            child_id: parent_id
            for child_id, parent_id in links.items()
            if parent_id in owned
        }

    def _lineage_fields_by_id(
        self,
        session: Session,
        user_email: str,
        metadata_by_id: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """The exact lineage payload a listing may expose, keyed by child id.

        Every list/search/get/export path goes through this one helper, so the
        ownership rule is applied in one place and a new path cannot publish a
        link that the others would withhold.
        """
        visible_parents = self._visible_parent_ids(
            session,
            user_email,
            [
                {"id": conversation_id, "metadata": metadata}
                for conversation_id, metadata in metadata_by_id.items()
            ],
        )
        return {
            conversation_id: lineage_fields(
                metadata, visible_parent_id=visible_parents.get(conversation_id)
            )
            for conversation_id, metadata in metadata_by_id.items()
        }

    @staticmethod
    def _sanitize_lineage_metadata(
        metadata: Dict[str, Any], visible_parent_id: Optional[str]
    ) -> Dict[str, Any]:
        """Drop a parent link the caller may not see from a full record."""
        recorded = parent_conversation_id_from_metadata(metadata)
        if recorded and recorded != visible_parent_id:
            metadata.pop(PARENT_CONVERSATION_METADATA_KEY, None)
        return metadata

    def save_conversation(
        self,
        conversation_id: str,
        user_email: str,
        title: Optional[str],
        model: Optional[str],
        messages: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        allow_shrink: bool = False,
    ) -> Optional[ConversationRecord]:
        """Save or update a conversation with all its messages.

        Upsert: if conversation exists for this user, replaces all messages.
        Returns None if the conversation_id belongs to a different user, or if
        the write would shrink the conversation and ``allow_shrink`` is not set.

        Because the update replaces the whole message set, a caller holding a
        partial history would silently destroy the rest of the conversation.
        That is not hypothetical: a WebSocket reconnect used to leave the server
        with an empty session still bound to the old conversation_id, and the
        next turn wrote two messages over fifty. Callers are rehydrated before
        their turn runs so this should be unreachable, but the guard is the
        durable backstop -- it is the layer that actually owns the data.

        ``allow_shrink=True`` is for the legitimate shrink: rewind /
        edit-and-resubmit drops a prompt and everything after it.

        Normalizes ``user_email`` so callers using mixed case (e.g. one
        connection arrives as ``Alice@Test.com`` and a later connection as
        ``alice@test.com`` because of reverse-proxy or OAuth provider
        differences) still match the existing row instead of silently
        dropping the save.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            existing = session.query(ConversationRecord).filter(
                ConversationRecord.id == conversation_id,
                ConversationRecord.user_email == user_email,
            ).first()

            if existing:
                # Count the rows rather than trusting ``message_count``: the
                # rows are what the replace below deletes, so they are what the
                # guard has to reason about. A stored count of zero therefore
                # fails open, which is the right direction -- an empty
                # conversation has nothing to lose.
                stored_count = session.query(MessageRecord).filter(
                    MessageRecord.conversation_id == conversation_id
                ).count()
                if not allow_shrink and len(messages) < stored_count:
                    logger.error(
                        "Refusing to save conversation %s: the write holds %d "
                        "message(s) but %d are stored, and replacing them would "
                        "lose the difference. This turn was not persisted.",
                        sanitize_for_logging(conversation_id),
                        len(messages),
                        stored_count,
                    )
                    return None

                # The conversation's data classification (issue #1042) is set
                # once, when the conversation is created, and never changes:
                # history written under one level must not be relabelled as
                # another, and a legacy record must not be stamped by an
                # ordinary save. An explicit operator stamp goes through
                # ``stamp_legacy_classification``.
                if not _classification_unchanged(existing.metadata_json, metadata):
                    logger.error(
                        "Refusing to save conversation %s: the write would change "
                        "its recorded data classification. This turn was not "
                        "persisted.",
                        sanitize_for_logging(conversation_id),
                    )
                    return None

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

    def _scoped_query(self, session: Session, user_email: str, tag_name: Optional[str]):
        """The conversation query for one user, narrowed to a tag when asked.

        The user scope is the same on every page of a listing, so a caller
        that pages through the history never sees another account's rows
        (issue #1085). A tag that does not exist yields no rows at all.
        """
        query = session.query(ConversationRecord).filter(
            ConversationRecord.user_email == user_email
        )
        if not tag_name:
            return query
        tag = session.query(TagRecord).filter(
            TagRecord.name == tag_name,
            TagRecord.user_email == user_email,
        ).first()
        if not tag:
            return None
        conv_ids = [
            r.conversation_id
            for r in session.query(ConversationTagLink).filter(
                ConversationTagLink.tag_id == tag.id
            ).all()
        ]
        return query.filter(ConversationRecord.id.in_(conv_ids))

    def count_conversations(
        self,
        user_email: str,
        tag_name: Optional[str] = None,
    ) -> int:
        """How many conversations a listing can page through (issue #1085).

        The count is scoped exactly like the listing it belongs to -- the
        same user, and the same tag when one is given -- so a client can
        tell "there is an older page" from "that is all of them" without
        fetching the whole history.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            query = self._scoped_query(session, user_email, tag_name)
            if query is None:
                return 0
            return query.count()

    def list_conversations(
        self,
        user_email: str,
        limit: int = 50,
        offset: int = 0,
        tag_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List conversations for a user, most recent first."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            query = self._scoped_query(session, user_email, tag_name)
            if query is None:
                return []

            # The id breaks ties between conversations saved in the same
            # instant, so the order is total and a page boundary cannot
            # hand the same row out twice or skip one (issue #1085).
            query = query.order_by(
                desc(ConversationRecord.updated_at), desc(ConversationRecord.id)
            )
            conversations = query.offset(offset).limit(limit).all()

            metadata_by_id = {
                conv.id: _load_metadata(conv.metadata_json) for conv in conversations
            }
            lineage_by_id = self._lineage_fields_by_id(
                session, user_email, metadata_by_id
            )

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
                metadata = metadata_by_id[conv.id]

                results.append({
                    "id": conv.id,
                    "title": conv.title or preview[:200] or "Untitled",
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": conv.message_count,
                    "preview": preview,
                    "tags": tag_names,
                    **public_fields(metadata),
                    **lineage_by_id[conv.id],
                })

            return results

    def get_conversation(self, conversation_id: str, user_email: str) -> Optional[Dict[str, Any]]:
        """Get a full conversation with all messages.

        Normalizes ``user_email`` to keep restore symmetrical with
        ``save_conversation``: a lookup with different email casing must
        still hit a row written under a different casing.
        """
        user_email = normalize_user_email(user_email)
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

            lineage = self._lineage_fields_by_id(
                session, user_email, {conv.id: conv_metadata}
            )[conv.id]
            # A full record is only readable by its owner, but a corrupt or
            # legacy link can still name another user's conversation. Strip it
            # from both the metadata blob and the public field so the two views
            # never disagree.
            self._sanitize_lineage_metadata(
                conv_metadata, lineage.get("parent_conversation_id")
            )

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
                # Classification reads the *raw* blob so an unreadable one
                # still fails closed as "invalid"; ``conv_metadata`` has been
                # normalized to a dict for the response payload.
                **public_fields(_load_metadata(conv.metadata_json)),
                **lineage,
            }

    def get_conversation_owner(self, conversation_id: str) -> Optional[str]:
        """Return the owner email for a conversation, or None if it does not exist.

        Result is normalized so callers comparing against an authenticated user
        get a consistent answer even for legacy rows written before the
        repository normalized on write.
        """
        with self._get_session() as session:
            conv = session.get(ConversationRecord, conversation_id)
            return normalize_user_email(conv.user_email) if conv else None

    def delete_conversation(self, conversation_id: str, user_email: str) -> bool:
        """Delete a single conversation with messages and tag associations."""
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            convs = session.query(ConversationRecord).filter(
                ConversationRecord.user_email == user_email,
            ).order_by(desc(ConversationRecord.updated_at)).all()

            lineage_by_id = self._lineage_fields_by_id(
                session,
                user_email,
                {
                    conv.id: _load_metadata(conv.metadata_json)
                    for conv in convs
                },
            )

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
                self._sanitize_lineage_metadata(
                    conv_metadata,
                    lineage_by_id[conv.id].get("parent_conversation_id"),
                )

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
                    **lineage_by_id[conv.id],
                })

            return results

    def delete_all_conversations(self, user_email: str) -> int:
        """Delete all conversations for a user. Returns count deleted."""
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
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

            metadata_by_id = {
                conv.id: _load_metadata(conv.metadata_json) for conv in conversations
            }
            lineage_by_id = self._lineage_fields_by_id(
                session, user_email, metadata_by_id
            )

            results = []
            for conv in conversations:
                first_reply = session.query(MessageRecord).filter(
                    MessageRecord.conversation_id == conv.id,
                    MessageRecord.role == "assistant",
                ).order_by(MessageRecord.sequence_number).first()
                preview = first_reply.content[:300] if first_reply and first_reply.content else ""
                metadata = metadata_by_id[conv.id]

                results.append({
                    "id": conv.id,
                    "title": conv.title or preview[:200] or "Untitled",
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                    "message_count": conv.message_count,
                    "preview": preview,
                    "tags": self._get_tag_names(session, conv.id),
                    **public_fields(metadata),
                    **lineage_by_id[conv.id],
                })

            return results

    def add_tag(self, conversation_id: str, tag_name: str, user_email: str) -> Optional[str]:
        """Add a tag to a conversation. Creates the tag if it doesn't exist."""
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
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
        user_email = normalize_user_email(user_email)
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

    def stamp_legacy_classification(
        self,
        level: Optional[str],
        user_email: Optional[str] = None,
        conversation_ids: Optional[List[str]] = None,
    ) -> int:
        """Record a data classification on legacy conversations (issue #1042).

        The explicit migration step for conversations saved before the record
        existed: an operator who knows what they hold assigns ``level``
        (``None`` records them as unclassified). Only conversations with no
        record are touched -- a recorded classification is never rewritten.
        Scoped to one user and/or a list of ids when given. Returns the number
        of conversations stamped.
        """
        with self._get_session() as session:
            query = session.query(ConversationRecord)
            if user_email:
                query = query.filter(
                    ConversationRecord.user_email == normalize_user_email(user_email)
                )
            if conversation_ids is not None:
                query = query.filter(ConversationRecord.id.in_(conversation_ids))
            stamped = 0
            for conv in query.all():
                if conv.metadata_json:
                    try:
                        current = json.loads(conv.metadata_json)
                    except json.JSONDecodeError:
                        # Unreadable metadata is left alone: it may hold a
                        # record this method cannot see.
                        continue
                    if not isinstance(current, dict):
                        continue
                else:
                    current = {}
                if CLASSIFICATION_METADATA_KEY in current:
                    continue
                current[CLASSIFICATION_METADATA_KEY] = level
                conv.metadata_json = json.dumps(current)
                stamped += 1
                # Audit trail for the explicit relabel: id, owner and level.
                logger.warning(
                    "Stamped data classification %s on legacy conversation %s "
                    "(user %s)",
                    sanitize_for_logging(str(level)),
                    sanitize_for_logging(conv.id),
                    sanitize_for_logging(conv.user_email),
                )
            session.commit()
            return stamped

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


_MISSING = object()


def _load_metadata(metadata_json: Optional[str]) -> Any:
    """Decoded conversation metadata; an unreadable blob decodes to ``_MISSING``."""
    if not metadata_json:
        return None
    try:
        return json.loads(metadata_json)
    except json.JSONDecodeError:
        return _MISSING


def _classification_unchanged(
    stored_metadata_json: Optional[str], incoming: Optional[Dict[str, Any]]
) -> bool:
    """Whether a save keeps the stored data classification record as it is.

    Both sides must agree on whether a record exists and, if so, on its value.
    Unreadable stored metadata fails closed: it may hold a record.
    """
    stored = _load_metadata(stored_metadata_json)
    if stored is None:
        stored = {}
    if not isinstance(stored, dict):
        return False
    incoming = incoming if isinstance(incoming, dict) else {}
    stored_has = CLASSIFICATION_METADATA_KEY in stored
    incoming_has = CLASSIFICATION_METADATA_KEY in incoming
    if stored_has != incoming_has:
        return False
    return not stored_has or (
        _comparable(stored[CLASSIFICATION_METADATA_KEY])
        == _comparable(incoming[CLASSIFICATION_METADATA_KEY])
    )


def _comparable(value: Any) -> Any:
    """A recorded level as the binding reads it (surrounding whitespace ignored)."""
    return value.strip() if isinstance(value, str) else value


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
