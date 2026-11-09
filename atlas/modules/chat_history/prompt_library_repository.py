"""Persistence for the team-shared prompt library (issue #1169).

This layer stores and loads rows; it makes **no** authorization decisions.
Every caller goes through ``atlas.modules.prompts.prompt_library_service``,
which decides who may see or change what before calling in here.

Invariants kept here:

* Published versions are insert-only. Nothing in this module updates or
  deletes a ``prompt_library_versions`` row, so a pinned version id always
  resolves to the text it was published with.
* Version numbers are unique per prompt (database constraint). ``publish``
  retries on the constraint violation a concurrent publisher causes, so two
  replicas publishing at once get consecutive numbers rather than a duplicate.
* Identities are normalized at the entry point, like the other chat-history
  repositories, and referential integrity is enforced here rather than with
  foreign keys (DuckDB compatibility).
"""

import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import desc, func, or_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from atlas.core.user_identity import normalize_user_email

from .models import (
    PromptLibraryAuditRecord,
    PromptLibraryGrantRecord,
    PromptLibraryPromptRecord,
    PromptLibraryVersionRecord,
)

logger = logging.getLogger(__name__)

GRANTEE_GROUP = "group"
GRANTEE_USER = "user"

# Attempts a publish makes when concurrent publishers keep taking the number,
# with a short jittered pause between them so racing writers spread out.
_PUBLISH_ATTEMPTS = 20
_PUBLISH_BACKOFF_SECONDS = 0.005


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_group_name(group: str) -> str:
    """Group names compare case- and whitespace-insensitively, as in auth."""
    return (group or "").strip().lower()


class PromptLibraryRepository:
    """Row-level storage for prompts, versions, grants and audit events."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def prompt_to_dict(record: PromptLibraryPromptRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "owner_email": record.owner_email,
            "title": record.title,
            "description": record.description,
            "draft_content": record.draft_content,
            "latest_version_id": record.latest_version_id,
            "latest_version_number": record.latest_version_number or 0,
            "forked_from_prompt_id": record.forked_from_prompt_id,
            "forked_from_version_id": record.forked_from_version_id,
            "archived_at": _iso(record.archived_at),
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    @staticmethod
    def version_to_dict(record: PromptLibraryVersionRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "prompt_id": record.prompt_id,
            "version_number": record.version_number,
            "title": record.title,
            "content": record.content,
            "change_description": record.change_description,
            "author_email": record.author_email,
            "created_at": _iso(record.created_at),
        }

    @staticmethod
    def grant_to_dict(record: PromptLibraryGrantRecord) -> Dict[str, Any]:
        return {
            "grantee_type": record.grantee_type,
            "grantee": record.grantee,
            "permission": record.permission,
            "created_by": record.created_by,
            "created_at": _iso(record.created_at),
        }

    @staticmethod
    def audit_to_dict(record: PromptLibraryAuditRecord) -> Dict[str, Any]:
        try:
            details = json.loads(record.details_json) if record.details_json else {}
        except (TypeError, ValueError):
            details = {}
        return {
            "id": record.id,
            "prompt_id": record.prompt_id,
            "version_id": record.version_id,
            "event_type": record.event_type,
            "actor_email": record.actor_email,
            "details": details,
            "created_at": _iso(record.created_at),
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_prompt(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Return a prompt row by id (archived included), or None."""
        if not prompt_id:
            return None
        with self._session() as session:
            record = session.get(PromptLibraryPromptRecord, prompt_id)
            return self.prompt_to_dict(record) if record else None

    def get_prompts(self, prompt_ids: Iterable[str]) -> List[Dict[str, Any]]:
        """Return the non-archived prompts among ``prompt_ids``, newest first."""
        ids = [p for p in set(prompt_ids) if p]
        if not ids:
            return []
        with self._session() as session:
            records = (
                session.query(PromptLibraryPromptRecord)
                .filter(
                    PromptLibraryPromptRecord.id.in_(ids),
                    PromptLibraryPromptRecord.archived_at.is_(None),
                )
                .order_by(desc(PromptLibraryPromptRecord.updated_at))
                .all()
            )
            return [self.prompt_to_dict(r) for r in records]

    def list_owned_prompt_ids(self, owner_email: str) -> List[str]:
        owner_email = normalize_user_email(owner_email)
        with self._session() as session:
            rows = (
                session.query(PromptLibraryPromptRecord.id)
                .filter(
                    PromptLibraryPromptRecord.owner_email == owner_email,
                    PromptLibraryPromptRecord.archived_at.is_(None),
                )
                .all()
            )
            return [r[0] for r in rows]

    def search_prompt_ids(self, prompt_ids: Sequence[str], query: str) -> List[str]:
        """Narrow ``prompt_ids`` to those whose title or description match.

        Only ever narrows an already-authorized id set: the match runs in SQL
        but cannot widen what the caller may see.
        """
        ids = [p for p in set(prompt_ids) if p]
        needle = (query or "").strip().lower()
        if not ids or not needle:
            return ids
        pattern = f"%{needle}%"
        with self._session() as session:
            rows = (
                session.query(PromptLibraryPromptRecord.id)
                .filter(
                    PromptLibraryPromptRecord.id.in_(ids),
                    or_(
                        func.lower(PromptLibraryPromptRecord.title).like(pattern),
                        func.lower(func.coalesce(PromptLibraryPromptRecord.description, "")).like(pattern),
                    ),
                )
                .all()
            )
            return [r[0] for r in rows]

    def create_prompt(
        self,
        owner_email: str,
        title: str,
        description: Optional[str],
        draft_content: str,
        forked_from_prompt_id: Optional[str] = None,
        forked_from_version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner_email = normalize_user_email(owner_email)
        with self._session() as session:
            record = PromptLibraryPromptRecord(
                owner_email=owner_email,
                title=title.strip(),
                description=(description or "").strip() or None,
                draft_content=draft_content,
                latest_version_number=0,
                forked_from_prompt_id=forked_from_prompt_id,
                forked_from_version_id=forked_from_version_id,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self.prompt_to_dict(record)

    def update_prompt(
        self,
        prompt_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        draft_content: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update draft fields. Published versions are never touched."""
        with self._session() as session:
            record = session.get(PromptLibraryPromptRecord, prompt_id)
            if record is None or record.archived_at is not None:
                return None
            if title is not None:
                record.title = title.strip()
            if description is not None:
                record.description = description.strip() or None
            if draft_content is not None:
                record.draft_content = draft_content
            record.updated_at = _now()
            session.commit()
            session.refresh(record)
            return self.prompt_to_dict(record)

    def archive_prompt(self, prompt_id: str) -> bool:
        with self._session() as session:
            record = session.get(PromptLibraryPromptRecord, prompt_id)
            if record is None or record.archived_at is not None:
                return False
            record.archived_at = _now()
            record.updated_at = record.archived_at
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def publish_version(
        self,
        prompt_id: str,
        author_email: str,
        change_description: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Snapshot the prompt's current draft as the next immutable version.

        Returns None when the prompt is missing or archived. The number is
        computed from the versions table (not the denormalized counter), and
        the unique constraint arbitrates a concurrent publication: the loser
        rolls back and tries again with a fresh read. DuckDB reports a write
        conflict between its connections as a transaction error rather than
        a constraint violation, so any driver error is retried the same way.
        """
        author_email = normalize_user_email(author_email)
        last_error: Optional[Exception] = None
        for attempt in range(_PUBLISH_ATTEMPTS):
            if attempt:
                time.sleep(random.uniform(0, _PUBLISH_BACKOFF_SECONDS * min(attempt, 8)))
            with self._session() as session:
                query = session.query(PromptLibraryPromptRecord).filter(
                    PromptLibraryPromptRecord.id == prompt_id
                )
                if session.get_bind().dialect.name == "postgresql":
                    # Serialize publishers of one prompt; the constraint below
                    # is still the guarantee on every backend.
                    query = query.with_for_update()
                prompt = query.first()
                if prompt is None or prompt.archived_at is not None:
                    return None
                current = (
                    session.query(func.max(PromptLibraryVersionRecord.version_number))
                    .filter(PromptLibraryVersionRecord.prompt_id == prompt_id)
                    .scalar()
                ) or 0
                version = PromptLibraryVersionRecord(
                    prompt_id=prompt_id,
                    version_number=current + 1,
                    title=prompt.title,
                    content=prompt.draft_content,
                    change_description=(change_description or "").strip() or None,
                    author_email=author_email,
                )
                session.add(version)
                try:
                    session.flush()
                except DBAPIError as e:
                    session.rollback()
                    last_error = e
                    continue
                # Only move the pointer forward: a slower publisher that lost
                # the race to a higher number must not rewind it.
                if (prompt.latest_version_number or 0) < version.version_number:
                    prompt.latest_version_number = version.version_number
                    prompt.latest_version_id = version.id
                prompt.updated_at = _now()
                try:
                    session.commit()
                except DBAPIError as e:
                    session.rollback()
                    last_error = e
                    continue
                session.refresh(version)
                return self.version_to_dict(version)
        logger.error("Publishing prompt %s failed after retries: %s", prompt_id, last_error)
        raise RuntimeError("Could not allocate a version number; try again")

    def list_versions(self, prompt_id: str) -> List[Dict[str, Any]]:
        """All published versions of a prompt, newest first."""
        with self._session() as session:
            records = (
                session.query(PromptLibraryVersionRecord)
                .filter(PromptLibraryVersionRecord.prompt_id == prompt_id)
                .order_by(desc(PromptLibraryVersionRecord.version_number))
                .all()
            )
            return [self.version_to_dict(r) for r in records]

    def get_version(self, prompt_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """A version, only if it belongs to ``prompt_id``."""
        if not prompt_id or not version_id:
            return None
        with self._session() as session:
            record = session.get(PromptLibraryVersionRecord, version_id)
            if record is None or record.prompt_id != prompt_id:
                return None
            return self.version_to_dict(record)

    def get_latest_version(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            record = (
                session.query(PromptLibraryVersionRecord)
                .filter(PromptLibraryVersionRecord.prompt_id == prompt_id)
                .order_by(desc(PromptLibraryVersionRecord.version_number))
                .first()
            )
            return self.version_to_dict(record) if record else None

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------

    def list_grants(self, prompt_id: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            records = (
                session.query(PromptLibraryGrantRecord)
                .filter(PromptLibraryGrantRecord.prompt_id == prompt_id)
                .order_by(PromptLibraryGrantRecord.grantee_type, PromptLibraryGrantRecord.grantee)
                .all()
            )
            return [self.grant_to_dict(r) for r in records]

    def grants_for_prompts(self, prompt_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        ids = [p for p in set(prompt_ids) if p]
        result: Dict[str, List[Dict[str, Any]]] = {p: [] for p in ids}
        if not ids:
            return result
        with self._session() as session:
            records = (
                session.query(PromptLibraryGrantRecord)
                .filter(PromptLibraryGrantRecord.prompt_id.in_(ids))
                .all()
            )
            for r in records:
                result.setdefault(r.prompt_id, []).append(self.grant_to_dict(r))
        return result

    def replace_grants(
        self, prompt_id: str, grants: Sequence[Dict[str, str]], actor_email: str
    ) -> List[Dict[str, Any]]:
        """Atomically replace a prompt's grant set.

        One transaction, so a reader never sees a half-applied change and two
        concurrent updates resolve to one of the two complete sets.
        """
        actor_email = normalize_user_email(actor_email)
        with self._session() as session:
            session.query(PromptLibraryGrantRecord).filter(
                PromptLibraryGrantRecord.prompt_id == prompt_id
            ).delete(synchronize_session=False)
            for g in grants:
                session.add(
                    PromptLibraryGrantRecord(
                        prompt_id=prompt_id,
                        grantee_type=g["grantee_type"],
                        grantee=g["grantee"],
                        permission=g["permission"],
                        created_by=actor_email,
                    )
                )
            prompt = session.get(PromptLibraryPromptRecord, prompt_id)
            if prompt is not None:
                prompt.updated_at = _now()
            session.commit()
        return self.list_grants(prompt_id)

    def prompt_ids_granted_to_user(self, user_email: str) -> Dict[str, str]:
        """``{prompt_id: permission}`` for direct user grants."""
        user_email = normalize_user_email(user_email)
        with self._session() as session:
            rows = (
                session.query(PromptLibraryGrantRecord.prompt_id, PromptLibraryGrantRecord.permission)
                .filter(
                    PromptLibraryGrantRecord.grantee_type == GRANTEE_USER,
                    PromptLibraryGrantRecord.grantee == user_email,
                )
                .all()
            )
            return {r[0]: r[1] for r in rows}

    def distinct_granted_groups(self) -> List[str]:
        """Every group that appears in any grant (bounded by sharing use)."""
        with self._session() as session:
            rows = (
                session.query(PromptLibraryGrantRecord.grantee)
                .filter(PromptLibraryGrantRecord.grantee_type == GRANTEE_GROUP)
                .distinct()
                .all()
            )
            return sorted({r[0] for r in rows})

    def prompt_ids_granted_to_groups(self, groups: Iterable[str]) -> Dict[str, List[str]]:
        """``{prompt_id: [group, ...]}`` for group grants among ``groups``."""
        names = [g for g in set(groups) if g]
        if not names:
            return {}
        with self._session() as session:
            rows = (
                session.query(PromptLibraryGrantRecord.prompt_id, PromptLibraryGrantRecord.grantee)
                .filter(
                    PromptLibraryGrantRecord.grantee_type == GRANTEE_GROUP,
                    PromptLibraryGrantRecord.grantee.in_(names),
                )
                .all()
            )
        result: Dict[str, List[str]] = {}
        for prompt_id, group in rows:
            result.setdefault(prompt_id, []).append(group)
        return result

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def add_audit_event(
        self,
        prompt_id: str,
        event_type: str,
        actor_email: str,
        version_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._session() as session:
            session.add(
                PromptLibraryAuditRecord(
                    prompt_id=prompt_id,
                    version_id=version_id,
                    event_type=event_type,
                    actor_email=normalize_user_email(actor_email),
                    details_json=json.dumps(details, sort_keys=True) if details else None,
                )
            )
            session.commit()

    def list_audit_events(self, prompt_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._session() as session:
            records = (
                session.query(PromptLibraryAuditRecord)
                .filter(PromptLibraryAuditRecord.prompt_id == prompt_id)
                .order_by(desc(PromptLibraryAuditRecord.created_at))
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            return [self.audit_to_dict(r) for r in records]
