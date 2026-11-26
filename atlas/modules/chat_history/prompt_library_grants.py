"""Grant and audit storage for the prompt library (issue #1169).

A mixin of ``PromptLibraryRepository``. No authorization decisions here.

Sharing changes are optimistic-concurrency writes: the caller names the
``sharing_revision`` its desired grant set was computed from, and the write
starts with ``UPDATE ... SET sharing_revision = sharing_revision + 1 WHERE id
= ? AND sharing_revision = ?``. On PostgreSQL that statement takes the
prompt's row lock, so concurrent sharing updates for one prompt serialize; the
loser, once unblocked, re-evaluates the condition against the committed
revision and matches nothing. Either way a stale update is refused
(``PromptConflictError("sharing")``) instead of restoring a grant that was
just revoked. A unique constraint on (prompt, grantee type, grantee) is the
backstop against duplicate grants.

Grants are written as a diff (delete removed, update changed, insert added)
rather than delete-all-then-insert, so no transaction ever deletes and
re-inserts the same unique key -- a pattern DuckDB's constraint checking
rejects.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from atlas.core.user_identity import normalize_user_email

from .models import PromptLibraryAuditRecord, PromptLibraryGrantRecord, PromptLibraryPromptRecord
from .prompt_library_storage import (
    AUDIT_DEDUP_CONSTRAINT,
    GRANT_CONSTRAINT,
    PromptConflictError,
    guarded,
    is_transient_conflict,
    storage_guard,
)

GRANTEE_GROUP = "group"
GRANTEE_USER = "user"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def grant_to_dict(record: PromptLibraryGrantRecord) -> Dict[str, Any]:
    return {
        "grantee_type": record.grantee_type,
        "grantee": record.grantee,
        "permission": record.permission,
        "created_by": record.created_by,
        "created_at": _iso(record.created_at),
    }


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
        "occurrences": record.occurrences or 1,
        "last_occurred_at": _iso(record.last_occurred_at) if record.last_occurred_at else None,
    }


class PromptLibraryGrantStore:
    """Grant and audit persistence; mixed into ``PromptLibraryRepository``."""

    def _session(self) -> Session:  # provided by the repository
        raise NotImplementedError

    @staticmethod
    def _grants_query(session: Session, prompt_id: str):
        return (
            session.query(PromptLibraryGrantRecord)
            .filter(PromptLibraryGrantRecord.prompt_id == prompt_id)
            .order_by(PromptLibraryGrantRecord.grantee_type, PromptLibraryGrantRecord.grantee)
        )

    @guarded("list grants")
    def list_grants(self, prompt_id: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            return [grant_to_dict(r) for r in self._grants_query(session, prompt_id).all()]

    @guarded("read sharing")
    def get_sharing_state(self, prompt_id: str) -> Optional[Tuple[int, List[Dict[str, Any]]]]:
        """``(sharing_revision, grants)`` or None for a missing/archived prompt.

        The revision is read before the grants. If a change commits between
        the two reads, the grants are newer than the revision, so a write
        based on them names a stale revision and is refused -- never the
        other way round.
        """
        with self._session() as session:
            prompt = session.get(PromptLibraryPromptRecord, prompt_id)
            if prompt is None or prompt.archived_at is not None:
                return None
            revision = prompt.sharing_revision or 0
            grants = [grant_to_dict(r) for r in self._grants_query(session, prompt_id).all()]
            return revision, grants

    @guarded("list grants")
    def grants_for_prompts(self, prompt_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        ids = [p for p in set(prompt_ids) if p]
        result: Dict[str, List[Dict[str, Any]]] = {p: [] for p in ids}
        if not ids:
            return result
        with self._session() as session:
            for r in (
                session.query(PromptLibraryGrantRecord)
                .filter(PromptLibraryGrantRecord.prompt_id.in_(ids))
                .all()
            ):
                result.setdefault(r.prompt_id, []).append(grant_to_dict(r))
        return result

    def replace_grants(
        self,
        prompt_id: str,
        grants: Sequence[Dict[str, str]],
        actor_email: str,
        expected_revision: int,
    ) -> Optional[Tuple[int, List[Dict[str, Any]]]]:
        """Make the prompt's grants exactly ``grants``, if nobody changed them since.

        Returns ``(new_revision, grants)``, None for a missing/archived prompt,
        or raises ``PromptConflictError("sharing")`` when ``expected_revision``
        is stale or a concurrent writer conflicted.
        """
        actor_email = normalize_user_email(actor_email)
        desired = {(g["grantee_type"], g["grantee"]): g["permission"] for g in grants}
        with storage_guard("share"):
            try:
                with self._session() as session:
                    claimed = session.execute(
                        update(PromptLibraryPromptRecord)
                        .where(
                            PromptLibraryPromptRecord.id == prompt_id,
                            PromptLibraryPromptRecord.sharing_revision == expected_revision,
                            PromptLibraryPromptRecord.archived_at.is_(None),
                        )
                        .values(
                            sharing_revision=PromptLibraryPromptRecord.sharing_revision + 1,
                            updated_at=datetime.now(timezone.utc),
                        )
                        # RETURNING, not rowcount: DuckDB's driver reports
                        # rowcount -1 for UPDATE, so only the returned rows
                        # say whether the compare-and-set matched.
                        .returning(PromptLibraryPromptRecord.id)
                        .execution_options(synchronize_session=False)
                    )
                    if len(claimed.fetchall()) != 1:
                        prompt = session.get(PromptLibraryPromptRecord, prompt_id)
                        if prompt is None or prompt.archived_at is not None:
                            return None
                        raise PromptConflictError("sharing")
                    if not self._may_manage(session, prompt_id, actor_email):
                        # Re-checked under the row the claim above locked: the
                        # caller's manage permission was checked before this
                        # transaction. Every grant change bumps the sharing
                        # revision, so a revocation through the service is
                        # already caught by the compare-and-set; this also
                        # covers one that is not (a grant removed outside this
                        # service). Answered like any other loss of access.
                        session.rollback()
                        return None
                    existing = {(r.grantee_type, r.grantee): r for r in self._grants_query(session, prompt_id)}
                    for key, record in existing.items():
                        if key not in desired:
                            session.delete(record)
                        elif record.permission != desired[key]:
                            record.permission = desired[key]
                    for (grantee_type, grantee), permission in desired.items():
                        if (grantee_type, grantee) not in existing:
                            session.add(PromptLibraryGrantRecord(
                                prompt_id=prompt_id, grantee_type=grantee_type, grantee=grantee,
                                permission=permission, created_by=actor_email,
                            ))
                    session.commit()
                    revision = expected_revision + 1
            except SQLAlchemyError as e:
                if is_transient_conflict(e, GRANT_CONSTRAINT):
                    raise PromptConflictError("sharing") from None
                raise
        return revision, self.list_grants(prompt_id)

    @staticmethod
    def _may_manage(session: Session, prompt_id: str, user_email: str) -> bool:
        """Owner, or a direct manage grant, read in the caller's transaction."""
        owner_email = session.query(PromptLibraryPromptRecord.owner_email).filter(
            PromptLibraryPromptRecord.id == prompt_id
        ).scalar()
        if owner_email == user_email:
            return True
        return session.query(PromptLibraryGrantRecord.id).filter(
            PromptLibraryGrantRecord.prompt_id == prompt_id,
            PromptLibraryGrantRecord.grantee_type == GRANTEE_USER,
            PromptLibraryGrantRecord.grantee == user_email,
            PromptLibraryGrantRecord.permission == "manage",
        ).first() is not None

    @guarded("list grants")
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

    @guarded("list grants")
    def granted_groups_by_use(self, limit: int) -> List[str]:
        """Up to ``limit`` group names granted on live prompts, most-granted first.

        The listing checks the caller's membership in each, so it reads a
        bounded, deterministic set (ties broken by name) rather than every
        group ever named in a grant. Archived prompts do not count.
        """
        from sqlalchemy import func

        with self._session() as session:
            uses = func.count(PromptLibraryGrantRecord.id)
            rows = (
                session.query(PromptLibraryGrantRecord.grantee, uses)
                .join(
                    PromptLibraryPromptRecord,
                    PromptLibraryPromptRecord.id == PromptLibraryGrantRecord.prompt_id,
                )
                .filter(
                    PromptLibraryGrantRecord.grantee_type == GRANTEE_GROUP,
                    PromptLibraryPromptRecord.archived_at.is_(None),
                )
                .group_by(PromptLibraryGrantRecord.grantee)
                .order_by(uses.desc(), PromptLibraryGrantRecord.grantee)
                .limit(limit)
                .all()
            )
            return [r[0] for r in rows]

    @guarded("list grants")
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

    @guarded("audit")
    def add_audit_event(
        self,
        prompt_id: str,
        event_type: str,
        actor_email: str,
        version_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        dedup_window_s: Optional[int] = None,
    ) -> None:
        """Append an audit event.

        With ``dedup_window_s``, repeats of the same event by the same actor
        on the same prompt within one window are one row: the first insert
        wins the unique ``dedup_key`` (across replicas too), and every later
        one increments ``occurrences`` and moves ``last_occurred_at``. The
        row keeps the first occurrence's ``created_at`` and version.
        """
        actor = normalize_user_email(actor_email)
        now = datetime.now(timezone.utc)
        key = None
        if dedup_window_s:
            window = int(now.timestamp()) // dedup_window_s
            digest = hashlib.sha256(f"{event_type}\x00{prompt_id}\x00{actor}\x00{window}".encode()).hexdigest()
            key = f"{event_type}:{digest}"
        for attempt in range(5):
            try:
                with self._session() as session:
                    if key is not None:
                        counted = session.execute(
                            update(PromptLibraryAuditRecord)
                            .where(PromptLibraryAuditRecord.dedup_key == key)
                            .values(
                                occurrences=PromptLibraryAuditRecord.occurrences + 1,
                                last_occurred_at=now,
                            )
                            # RETURNING, not rowcount: DuckDB's driver reports
                            # rowcount -1 for UPDATE, which read as "counted"
                            # and dropped the window's first refusal.
                            .returning(PromptLibraryAuditRecord.id)
                            .execution_options(synchronize_session=False)
                        )
                        if counted.fetchall():
                            session.commit()
                            return
                    session.add(PromptLibraryAuditRecord(
                        prompt_id=prompt_id,
                        version_id=version_id,
                        event_type=event_type,
                        actor_email=actor,
                        details_json=json.dumps(details, sort_keys=True) if details else None,
                        created_at=now,
                        occurrences=1,
                        last_occurred_at=now if key is not None else None,
                        dedup_key=key,
                    ))
                    session.commit()
                    return
            except SQLAlchemyError as e:
                # Another replica inserted this window's row first (unique
                # key), or a concurrent writer conflicted: count again.
                if key is None or attempt == 4 or not is_transient_conflict(e, AUDIT_DEDUP_CONSTRAINT):
                    raise

    def list_audit_events(self, prompt_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """The newest ``limit`` events of a prompt (all kinds)."""
        return self.page_audit_events(prompt_id, limit)["events"]

    @guarded("read audit")
    def page_audit_events(
        self,
        prompt_id: str,
        limit: int = 200,
        offset: int = 0,
        event_types: Optional[Iterable[str]] = None,
        exclude_types: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """One page of a prompt's audit events, newest first, and the total."""
        with self._session() as session:
            query = session.query(PromptLibraryAuditRecord).filter(
                PromptLibraryAuditRecord.prompt_id == prompt_id
            )
            if event_types is not None:
                query = query.filter(PromptLibraryAuditRecord.event_type.in_(list(event_types)))
            if exclude_types:
                query = query.filter(PromptLibraryAuditRecord.event_type.notin_(list(exclude_types)))
            total = query.count()
            records = (
                query.order_by(PromptLibraryAuditRecord.created_at.desc(), PromptLibraryAuditRecord.id)
                .offset(max(0, offset))
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            return {"events": [audit_to_dict(r) for r in records], "total": total}
