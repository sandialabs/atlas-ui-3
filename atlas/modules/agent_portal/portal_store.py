"""Repository for the Agent Portal server-side state store.

Houses the per-user CRUD for launch history, launch configs, layouts,
groups, bundles, and audit events. Presets continue to live in the
existing JSON-file ``PresetStore``; that is intentional (see Phase 1.5
in ``AGENT_PORTAL_ACTION_PLAN.md`` — co-existence is fine and avoids
churn for a working store).

Per-user scoping is enforced here on every read and write. Cross-user
reads return empty / NotFound; cross-user writes are no-ops or raise.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AuditEventRecord,
    BundleRecord,
    GroupRecord,
    LaunchConfigRecord,
    LaunchHistoryRecord,
    LayoutRecord,
)

logger = logging.getLogger(__name__)


# Caps mirror the localStorage caps the frontend already enforced so we
# don't unbounded-grow the table on a stuck client.
LAUNCH_HISTORY_MAX_PER_USER = 50
LAUNCH_CONFIGS_MAX_PER_USER = 200


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_dedup_key(payload: Dict[str, Any]) -> str:
    """Hash the launch-identity fields so re-launching the same command
    bumps ``last_used_at`` instead of inserting a new row."""
    parts = [
        str(payload.get("command", "")),
        str(payload.get("argsString", "")),
        str(payload.get("cwd", "")),
        str(payload.get("sandboxMode") or payload.get("sandbox_mode") or "off"),
    ]
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PortalStore:
    """Per-user CRUD over the agent-portal state tables."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # ------------------------------------------------------------------
    # Launch history
    # ------------------------------------------------------------------

    def list_launch_history(self, user_email: str) -> List[Dict[str, Any]]:
        """Return launch-history entries newest-first as a list of UI-shape
        dicts (so the frontend can drop them straight into its existing
        state)."""
        with self._session() as session:
            rows = (
                session.execute(
                    select(LaunchHistoryRecord)
                    .where(LaunchHistoryRecord.user_email == user_email)
                    .order_by(desc(LaunchHistoryRecord.last_used_at))
                )
                .scalars()
                .all()
            )
            out: List[Dict[str, Any]] = []
            for r in rows:
                try:
                    payload = json.loads(r.payload_json)
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                # Stamp the server's last_used_at into the payload so the
                # frontend's own "lastUsed" field is authoritative.
                payload["lastUsed"] = int(r.last_used_at.timestamp() * 1000) if r.last_used_at else 0
                out.append(payload)
            return out

    def upsert_launch_history(
        self, user_email: str, entry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Insert or bump a launch-history entry. The dedup key is derived
        from the launch identity so re-running the same command updates
        ``last_used_at`` rather than duplicating the row."""
        if not isinstance(entry, dict):
            raise ValueError("entry must be a dict")
        dedup = _make_dedup_key(entry)
        with self._session() as session:
            existing = (
                session.execute(
                    select(LaunchHistoryRecord).where(
                        LaunchHistoryRecord.user_email == user_email,
                        LaunchHistoryRecord.dedup_key == dedup,
                    )
                )
                .scalar_one_or_none()
            )
            now = _now_utc()
            if existing:
                existing.payload_json = json.dumps(entry)
                existing.last_used_at = now
            else:
                session.add(
                    LaunchHistoryRecord(
                        user_email=user_email,
                        payload_json=json.dumps(entry),
                        dedup_key=dedup,
                        last_used_at=now,
                    )
                )
            session.commit()
            # Soft cap: trim the oldest if over the per-user limit.
            self._trim_launch_history(session, user_email)
        entry = dict(entry)
        entry["lastUsed"] = int(now.timestamp() * 1000)
        return entry

    def _trim_launch_history(self, session: Session, user_email: str) -> None:
        """Drop oldest rows over ``LAUNCH_HISTORY_MAX_PER_USER``.

        Caller must commit. Kept private since it's only ever called
        right after an insert/update inside the same transaction.
        """
        ids_to_keep = (
            session.execute(
                select(LaunchHistoryRecord.id)
                .where(LaunchHistoryRecord.user_email == user_email)
                .order_by(desc(LaunchHistoryRecord.last_used_at))
                .limit(LAUNCH_HISTORY_MAX_PER_USER)
            )
            .scalars()
            .all()
        )
        if not ids_to_keep:
            return
        session.execute(
            delete(LaunchHistoryRecord).where(
                LaunchHistoryRecord.user_email == user_email,
                LaunchHistoryRecord.id.notin_(ids_to_keep),
            )
        )
        session.commit()

    def delete_launch_history_entry(self, user_email: str, dedup_key: str) -> bool:
        """Remove a single history entry by its dedup key. Returns True if
        a row was deleted.

        Uses an explicit existence check before delete because DuckDB does
        not always populate ``ResultProxy.rowcount`` on DELETE — relying
        on it gives false negatives.
        """
        with self._session() as session:
            existing = session.execute(
                select(LaunchHistoryRecord).where(
                    LaunchHistoryRecord.user_email == user_email,
                    LaunchHistoryRecord.dedup_key == dedup_key,
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            session.execute(
                delete(LaunchHistoryRecord).where(
                    LaunchHistoryRecord.user_email == user_email,
                    LaunchHistoryRecord.dedup_key == dedup_key,
                )
            )
            session.commit()
            return True

    def replace_launch_history(
        self, user_email: str, entries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Bulk-replace this user's launch history. Used by the migration
        path on first server fetch when localStorage holds the only copy.
        """
        with self._session() as session:
            session.execute(
                delete(LaunchHistoryRecord).where(
                    LaunchHistoryRecord.user_email == user_email
                )
            )
            now = _now_utc()
            # Preserve order: first entry is most recent, so timestamps
            # decrease from there.
            for offset, e in enumerate(entries[:LAUNCH_HISTORY_MAX_PER_USER]):
                if not isinstance(e, dict):
                    continue
                # Use a strictly-decreasing fake timestamp so the
                # newest-first order is preserved, even though all entries
                # come in within the same upload millisecond.
                ts = datetime.fromtimestamp(
                    now.timestamp() - offset, tz=timezone.utc
                )
                session.add(
                    LaunchHistoryRecord(
                        user_email=user_email,
                        payload_json=json.dumps(e),
                        dedup_key=_make_dedup_key(e),
                        last_used_at=ts,
                    )
                )
            session.commit()
        return self.list_launch_history(user_email)

    # ------------------------------------------------------------------
    # Launch configs
    # ------------------------------------------------------------------
    #
    # These are distinct from server-side presets (which still live in
    # presets_store.py). They are the legacy "launchConfigs" bag that
    # used to live in localStorage — kept as their own collection so the
    # migration path is one-way and deterministic.

    def list_launch_configs(self, user_email: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            rows = (
                session.execute(
                    select(LaunchConfigRecord)
                    .where(LaunchConfigRecord.user_email == user_email)
                    .order_by(desc(LaunchConfigRecord.updated_at))
                )
                .scalars()
                .all()
            )
            out: List[Dict[str, Any]] = []
            for r in rows:
                try:
                    payload = json.loads(r.payload_json)
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                payload["id"] = r.id
                payload["name"] = r.name
                out.append(payload)
            return out

    def replace_launch_configs(
        self, user_email: str, configs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Bulk-replace this user's launch configs. Migration path."""
        with self._session() as session:
            session.execute(
                delete(LaunchConfigRecord).where(
                    LaunchConfigRecord.user_email == user_email
                )
            )
            for cfg in configs[:LAUNCH_CONFIGS_MAX_PER_USER]:
                if not isinstance(cfg, dict):
                    continue
                name = (cfg.get("name") or "").strip() or "Untitled"
                # Strip the id so a fresh server-side id is used.
                payload = {k: v for k, v in cfg.items() if k != "id"}
                session.add(
                    LaunchConfigRecord(
                        user_email=user_email,
                        name=name,
                        payload_json=json.dumps(payload),
                    )
                )
            session.commit()
        return self.list_launch_configs(user_email)

    # ------------------------------------------------------------------
    # Layouts
    # ------------------------------------------------------------------

    def get_layout(self, user_email: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            row = session.get(LayoutRecord, user_email)
            if row is None:
                return None
            try:
                payload = json.loads(row.layout_json)
            except (TypeError, ValueError):
                return None
            if not isinstance(payload, dict):
                return None
            return payload

    def put_layout(self, user_email: str, layout: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(layout, dict):
            raise ValueError("layout must be a dict")
        with self._session() as session:
            row = session.get(LayoutRecord, user_email)
            if row is None:
                session.add(
                    LayoutRecord(
                        user_email=user_email,
                        layout_json=json.dumps(layout),
                    )
                )
            else:
                row.layout_json = json.dumps(layout)
                row.updated_at = _now_utc()
            session.commit()
        return layout

    # ------------------------------------------------------------------
    # Groups (Phase 3 fills in the launch-time semantics; CRUD is here)
    # ------------------------------------------------------------------

    def list_groups(self, owner: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            rows = (
                session.execute(
                    select(GroupRecord)
                    .where(GroupRecord.owner == owner)
                    .order_by(GroupRecord.name)
                )
                .scalars()
                .all()
            )
            return [_group_to_dict(r) for r in rows]

    def create_group(self, owner: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("group name is required")
        with self._session() as session:
            row = GroupRecord(
                owner=owner,
                name=name,
                max_panes=_int_or_none(data.get("max_panes")),
                mem_budget_bytes=_int_or_none(data.get("mem_budget_bytes")),
                cpu_budget_pct=_int_or_none(data.get("cpu_budget_pct")),
                idle_kill_seconds=_int_or_none(data.get("idle_kill_seconds")),
                audit_tag=(data.get("audit_tag") or None),
            )
            session.add(row)
            session.commit()
            return _group_to_dict(row)

    def get_group(self, owner: str, group_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            row = session.get(GroupRecord, group_id)
            if row is None or row.owner != owner:
                return None
            return _group_to_dict(row)

    def update_group(
        self, owner: str, group_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            row = session.get(GroupRecord, group_id)
            if row is None or row.owner != owner:
                return None
            for k in (
                "name",
                "max_panes",
                "mem_budget_bytes",
                "cpu_budget_pct",
                "idle_kill_seconds",
                "audit_tag",
            ):
                if k in data and data[k] is not None:
                    if k in ("max_panes", "mem_budget_bytes", "cpu_budget_pct", "idle_kill_seconds"):
                        setattr(row, k, _int_or_none(data[k]))
                    else:
                        setattr(row, k, data[k])
            row.updated_at = _now_utc()
            session.commit()
            return _group_to_dict(row)

    def delete_group(self, owner: str, group_id: str) -> bool:
        with self._session() as session:
            row = session.get(GroupRecord, group_id)
            if row is None or row.owner != owner:
                return False
            session.delete(row)
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Bundles (Phase 4)
    # ------------------------------------------------------------------

    def list_bundles(self, owner: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            rows = (
                session.execute(
                    select(BundleRecord)
                    .where(BundleRecord.owner == owner)
                    .order_by(BundleRecord.name)
                )
                .scalars()
                .all()
            )
            return [_bundle_to_dict(r) for r in rows]

    def create_bundle(self, owner: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("bundle name is required")
        payload = {
            "group_template": data.get("group_template") or {},
            "members": list(data.get("members") or []),
        }
        with self._session() as session:
            row = BundleRecord(
                owner=owner,
                name=name,
                payload_json=json.dumps(payload),
            )
            session.add(row)
            session.commit()
            return _bundle_to_dict(row)

    def get_bundle(self, owner: str, bundle_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            row = session.get(BundleRecord, bundle_id)
            if row is None or row.owner != owner:
                return None
            return _bundle_to_dict(row)

    def delete_bundle(self, owner: str, bundle_id: str) -> bool:
        with self._session() as session:
            row = session.get(BundleRecord, bundle_id)
            if row is None or row.owner != owner:
                return False
            session.delete(row)
            session.commit()
            return True

    # ------------------------------------------------------------------
    # Audit (Phase 4 starts writing; a thin sink lives here so all
    # writes flow through one path that can later tee to a JSONL file).
    # ------------------------------------------------------------------

    def append_audit(
        self,
        user_email: str,
        event: str,
        *,
        group_id: Optional[str] = None,
        process_id: Optional[str] = None,
        executor: Optional[str] = "local",
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._session() as session:
            row = AuditEventRecord(
                user_email=user_email,
                event=event,
                group_id=group_id,
                process_id=process_id,
                executor=executor,
                detail_json=json.dumps(detail) if detail else None,
            )
            session.add(row)
            session.commit()
            return _audit_to_dict(row)

    def list_audit(
        self,
        user_email: str,
        *,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._session() as session:
            rows = (
                session.execute(
                    select(AuditEventRecord)
                    .where(AuditEventRecord.user_email == user_email)
                    .order_by(desc(AuditEventRecord.ts))
                    .limit(max(1, min(int(limit), 1000)))
                )
                .scalars()
                .all()
            )
            return [_audit_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _int_or_none(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _group_to_dict(r: GroupRecord) -> Dict[str, Any]:
    return {
        "id": r.id,
        "owner": r.owner,
        "name": r.name,
        "max_panes": r.max_panes,
        "mem_budget_bytes": r.mem_budget_bytes,
        "cpu_budget_pct": r.cpu_budget_pct,
        "idle_kill_seconds": r.idle_kill_seconds,
        "audit_tag": r.audit_tag,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _bundle_to_dict(r: BundleRecord) -> Dict[str, Any]:
    try:
        payload = json.loads(r.payload_json) if r.payload_json else {}
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": r.id,
        "owner": r.owner,
        "name": r.name,
        "group_template": payload.get("group_template", {}),
        "members": payload.get("members", []),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _audit_to_dict(r: AuditEventRecord) -> Dict[str, Any]:
    detail: Any = None
    if r.detail_json:
        try:
            detail = json.loads(r.detail_json)
        except (TypeError, ValueError):
            detail = None
    return {
        "id": r.id,
        "ts": r.ts.isoformat() if r.ts else None,
        "user": r.user_email,
        "event": r.event,
        "group_id": r.group_id,
        "process_id": r.process_id,
        "executor": r.executor,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------

_singleton: Optional[PortalStore] = None


def get_portal_store() -> PortalStore:
    """Get or create the process-wide PortalStore.

    Lazily initializes the database (create_all) on first use. Single
    user / dev convenience — see ``database.init_database`` notes.
    """
    global _singleton
    if _singleton is None:
        from .database import get_session_factory, init_database
        init_database()
        _singleton = PortalStore(get_session_factory())
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test-only helper; do not call from production code."""
    global _singleton
    _singleton = None
