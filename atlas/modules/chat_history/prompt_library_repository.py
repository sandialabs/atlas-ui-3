"""Persistence for the team-shared prompt library (issue #1169).

This layer stores and loads rows; it makes **no** authorization decisions.
Every caller goes through ``atlas.modules.prompts.prompt_library_service``,
which decides who may see or change what before calling in here. Methods are
synchronous and open their own session; the service runs them in a worker
thread so they never block the event loop.

Invariants kept here:

* Published versions are insert-only. Nothing in this package updates or
  deletes a ``prompt_library_versions`` row, so a pinned version id always
  resolves to the text it was published with.
* Version numbers are unique per prompt (database constraint). ``publish``
  retries only transient conflicts -- a concurrent publisher taking the same
  number, a serialization failure or deadlock, a DuckDB write conflict -- and
  raises ``PromptConflictError("publish")`` once retries run out. Any other
  database failure surfaces at once as ``PromptStorageError``.
* Drafts change only through a compare-and-set on ``draft_revision``, and
  publishing names the revision it expects, so nobody publishes text they did
  not review. Grants: see ``prompt_library_grants``.
* Database exception text never reaches a log (see ``prompt_library_storage``).
"""

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import case, func, or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased, defer, sessionmaker

from atlas.core.user_identity import normalize_user_email

from .models import PromptLibraryPromptRecord, PromptLibraryVersionRecord
from .prompt_library_grants import GRANTEE_GROUP, GRANTEE_USER, PromptLibraryGrantStore
from .prompt_library_storage import (
    VERSION_CONSTRAINT,
    PromptConflictError,
    PromptStorageError,
    guarded,
    is_transient_conflict,
    storage_guard,
)

__all__ = [
    "GRANTEE_GROUP",
    "GRANTEE_USER",
    "PromptConflictError",
    "PromptLibraryRepository",
    "PromptStorageError",
    "clean_group_name",
]

logger = logging.getLogger(__name__)

# Attempts a publish makes while concurrent publishers keep winning, with a
# short jittered pause between them (in the worker thread, not the loop).
_PUBLISH_ATTEMPTS = 20
_PUBLISH_BACKOFF_SECONDS = 0.005
_LIKE_ESCAPE = "\\"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_group_name(group: str) -> str:
    """Group names keep their spelling: only surrounding whitespace is dropped.

    An external authorizer receives the name exactly as configured and may
    match case-sensitively, so lower-casing would ask it about another group.
    """
    return (group or "").strip()


def escape_like(text: str) -> str:
    """Make ``text`` match literally inside a LIKE pattern."""
    return (
        text.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


class PromptLibraryRepository(PromptLibraryGrantStore):
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
            "draft_revision": record.draft_revision or 1,
            "sharing_revision": record.sharing_revision or 0,
            "latest_version_id": record.latest_version_id,
            "latest_version_number": record.latest_version_number or 0,
            "forked_from_prompt_id": record.forked_from_prompt_id,
            "forked_from_version_id": record.forked_from_version_id,
            "archived_at": _iso(record.archived_at),
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    @staticmethod
    def prompt_summary_to_dict(record: PromptLibraryPromptRecord, has_unpublished_changes: bool) -> Dict[str, Any]:
        """A listing row: everything but the draft text (never loaded for lists)."""
        return {
            "id": record.id,
            "owner_email": record.owner_email,
            "title": record.title,
            "description": record.description,
            "draft_revision": record.draft_revision or 1,
            "sharing_revision": record.sharing_revision or 0,
            "latest_version_id": record.latest_version_id,
            "latest_version_number": record.latest_version_number or 0,
            "forked_from_prompt_id": record.forked_from_prompt_id,
            "forked_from_version_id": record.forked_from_version_id,
            "archived_at": _iso(record.archived_at),
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
            "has_unpublished_changes": bool(has_unpublished_changes),
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

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    @guarded("read prompt")
    def get_prompt(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Return a prompt row by id (archived included), or None."""
        if not prompt_id:
            return None
        with self._session() as session:
            record = session.get(PromptLibraryPromptRecord, prompt_id)
            return self.prompt_to_dict(record) if record else None

    @guarded("list prompts")
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

    @guarded("list prompts")
    def page_prompts(
        self,
        full_access_ids: Iterable[str],
        published_only_ids: Iterable[str],
        query: Optional[str],
        limit: int,
        offset: int,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """One page of the caller's visible prompts, newest first, and the total.

        ``full_access_ids`` are visible whatever their state (owned, edit or
        manage); ``published_only_ids`` only once something is published
        (use-only callers). The search narrows that already-authorized set
        and matches title or description literally (LIKE wildcards escaped),
        so it can never widen what a caller sees.
        """
        full = {p for p in full_access_ids if p}
        published_only = {p for p in published_only_ids if p} - full
        if not full and not published_only:
            return 0, []
        conditions = [PromptLibraryPromptRecord.archived_at.is_(None)]
        visibility = []
        if full:
            visibility.append(PromptLibraryPromptRecord.id.in_(full))
        if published_only:
            visibility.append(
                PromptLibraryPromptRecord.id.in_(published_only)
                & (PromptLibraryPromptRecord.latest_version_number > 0)
            )
        conditions.append(or_(*visibility))
        needle = (query or "").strip().lower()
        if needle:
            pattern = f"%{escape_like(needle)}%"
            conditions.append(or_(
                func.lower(PromptLibraryPromptRecord.title).like(pattern, escape=_LIKE_ESCAPE),
                func.lower(func.coalesce(PromptLibraryPromptRecord.description, "")).like(
                    pattern, escape=_LIKE_ESCAPE
                ),
            ))
        # The rows come back without any prompt text: the draft is deferred,
        # and whether it differs from the latest published version (the only
        # reason a list needed either text) is compared in the database.
        latest = aliased(PromptLibraryVersionRecord)
        unpublished = case(
            (latest.id.is_(None), True),
            else_=or_(
                latest.content != PromptLibraryPromptRecord.draft_content,
                latest.title != PromptLibraryPromptRecord.title,
            ),
        ).label("has_unpublished_changes")
        with self._session() as session:
            total = session.query(func.count(PromptLibraryPromptRecord.id)).filter(*conditions).scalar() or 0
            rows = (
                session.query(PromptLibraryPromptRecord, unpublished)
                .options(defer(PromptLibraryPromptRecord.draft_content))
                .outerjoin(latest, latest.id == PromptLibraryPromptRecord.latest_version_id)
                .filter(*conditions)
                .order_by(PromptLibraryPromptRecord.updated_at.desc(), PromptLibraryPromptRecord.id)
                .limit(limit)
                .offset(offset)
                .all()
            )
            return total, [self.prompt_summary_to_dict(record, flag) for record, flag in rows]

    @guarded("create prompt")
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
                draft_revision=1,
                sharing_revision=0,
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
        expected_draft_revision: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        draft_content: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Compare-and-set the draft. Published versions are never touched.

        Returns the prompt exactly as this write left it -- read inside the
        compare-and-set transaction, so its ``draft_revision`` and
        ``draft_content`` are this save's even when another editor saves a
        moment later. A caller that publishes with that revision therefore
        publishes this save's text or gets a conflict, never someone else's
        unseen change. Returns None when the prompt is missing or archived,
        or raises ``PromptConflictError("draft")`` when the draft moved past
        ``expected_draft_revision`` (someone else saved).
        """
        values: Dict[str, Any] = {
            "draft_revision": PromptLibraryPromptRecord.draft_revision + 1,
            "updated_at": _now(),
        }
        if title is not None:
            values["title"] = title.strip()
        if description is not None:
            values["description"] = description.strip() or None
        if draft_content is not None:
            values["draft_content"] = draft_content
        with storage_guard("update draft"):
            try:
                with self._session() as session:
                    result = session.execute(
                        update(PromptLibraryPromptRecord)
                        .where(
                            PromptLibraryPromptRecord.id == prompt_id,
                            PromptLibraryPromptRecord.draft_revision == expected_draft_revision,
                            PromptLibraryPromptRecord.archived_at.is_(None),
                        )
                        .values(**values)
                        # RETURNING, not rowcount: DuckDB's driver reports
                        # rowcount -1 for UPDATE, so only the returned rows
                        # say whether the compare-and-set matched.
                        .returning(PromptLibraryPromptRecord.id)
                        .execution_options(synchronize_session=False)
                    )
                    if len(result.fetchall()) != 1:
                        record = session.get(PromptLibraryPromptRecord, prompt_id)
                        if record is None or record.archived_at is not None:
                            return None
                        raise PromptConflictError("draft")
                    # Still inside the transaction that made the write (and,
                    # on PostgreSQL, holds its row lock): this is the state the
                    # write produced, not whatever lands after it.
                    record = session.get(
                        PromptLibraryPromptRecord, prompt_id, populate_existing=True
                    )
                    snapshot = self.prompt_to_dict(record)
                    session.commit()
            except SQLAlchemyError as e:
                if is_transient_conflict(e):
                    raise PromptConflictError("draft") from None
                raise
        return snapshot

    @guarded("archive prompt")
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
        expected_draft_revision: int,
    ) -> Optional[Dict[str, Any]]:
        """Snapshot the draft at ``expected_draft_revision`` as the next version.

        Returns None when the prompt is missing or archived; raises
        ``PromptConflictError("draft")`` when the draft changed since the
        caller reviewed it, ``PromptConflictError("publish")`` when concurrent
        publishers kept winning, and ``PromptStorageError`` for anything else.
        """
        author_email = normalize_user_email(author_email)
        last_conflict = "none"
        with storage_guard("publish"):
            for attempt in range(_PUBLISH_ATTEMPTS):
                if attempt:
                    time.sleep(random.uniform(0, _PUBLISH_BACKOFF_SECONDS * min(attempt, 8)))
                try:
                    return self._publish_once(prompt_id, author_email, change_description, expected_draft_revision)
                except SQLAlchemyError as e:
                    if not is_transient_conflict(e, VERSION_CONSTRAINT):
                        raise
                    last_conflict = type(e).__name__
        logger.warning(
            "Publishing gave up after %d conflicting attempts (%s)", _PUBLISH_ATTEMPTS, last_conflict
        )
        raise PromptConflictError("publish")

    def _publish_once(
        self, prompt_id: str, author_email: str, change_description: Optional[str], expected_draft_revision: int
    ) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            query = session.query(PromptLibraryPromptRecord).filter(PromptLibraryPromptRecord.id == prompt_id)
            if session.get_bind().dialect.name == "postgresql":
                # Serialize publishers (and draft saves) of one prompt; the
                # unique constraint is still the guarantee on every backend.
                query = query.with_for_update()
            prompt = query.first()
            if prompt is None or prompt.archived_at is not None:
                return None
            if (prompt.draft_revision or 1) != expected_draft_revision:
                raise PromptConflictError("draft")
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
            session.flush()
            # Only move the pointer forward: a slower publisher that lost the
            # race to a higher number must not rewind it.
            if (prompt.latest_version_number or 0) < version.version_number:
                prompt.latest_version_number = version.version_number
                prompt.latest_version_id = version.id
            prompt.updated_at = _now()
            session.commit()
            session.refresh(version)
            return self.version_to_dict(version)

    @guarded("list versions")
    def list_versions(self, prompt_id: str) -> List[Dict[str, Any]]:
        """All published versions of a prompt, newest first."""
        with self._session() as session:
            records = (
                session.query(PromptLibraryVersionRecord)
                .filter(PromptLibraryVersionRecord.prompt_id == prompt_id)
                .order_by(PromptLibraryVersionRecord.version_number.desc())
                .all()
            )
            return [self.version_to_dict(r) for r in records]

    @guarded("list versions")
    def page_versions(
        self, prompt_id: str, limit: int, offset: int, include_content: bool
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """``(total, versions)``: one page of a prompt's versions, newest first.

        Without ``include_content`` the version text is not even loaded, so a
        long history of large prompts stays cheap to list.
        """
        with self._session() as session:
            base = session.query(PromptLibraryVersionRecord).filter(
                PromptLibraryVersionRecord.prompt_id == prompt_id
            )
            total = base.count()
            query = base.order_by(PromptLibraryVersionRecord.version_number.desc())
            if not include_content:
                query = query.options(defer(PromptLibraryVersionRecord.content))
            records = query.offset(offset).limit(limit).all()
            result = []
            for record in records:
                if include_content:
                    result.append(self.version_to_dict(record))
                else:
                    result.append({
                        "id": record.id,
                        "prompt_id": record.prompt_id,
                        "version_number": record.version_number,
                        "title": record.title,
                        "content": None,
                        "change_description": record.change_description,
                        "author_email": record.author_email,
                        "created_at": _iso(record.created_at),
                    })
            return total, result

    @guarded("read version")
    def get_version(self, prompt_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """A version, only if it belongs to ``prompt_id``."""
        if not prompt_id or not version_id:
            return None
        with self._session() as session:
            record = session.get(PromptLibraryVersionRecord, version_id)
            if record is None or record.prompt_id != prompt_id:
                return None
            return self.version_to_dict(record)

    @guarded("read version")
    def get_latest_version(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            record = (
                session.query(PromptLibraryVersionRecord)
                .filter(PromptLibraryVersionRecord.prompt_id == prompt_id)
                .order_by(PromptLibraryVersionRecord.version_number.desc())
                .first()
            )
            return self.version_to_dict(record) if record else None

    @guarded("read versions")
    def versions_by_id(self, version_ids: Iterable[str], include_content: bool = True) -> Dict[str, Dict[str, Any]]:
        """Several versions in one query (the list view's latest versions).

        ``include_content=False`` leaves the text unloaded and out of the
        result (the list view needs only the version's metadata).
        """
        ids = [v for v in set(version_ids) if v]
        if not ids:
            return {}
        with self._session() as session:
            query = session.query(PromptLibraryVersionRecord).filter(PromptLibraryVersionRecord.id.in_(ids))
            if include_content:
                return {r.id: self.version_to_dict(r) for r in query.all()}
            records = query.options(defer(PromptLibraryVersionRecord.content)).all()
            return {
                r.id: {
                    "id": r.id,
                    "prompt_id": r.prompt_id,
                    "version_number": r.version_number,
                    "title": r.title,
                    "change_description": r.change_description,
                    "author_email": r.author_email,
                    "created_at": _iso(r.created_at),
                }
                for r in records
            }
