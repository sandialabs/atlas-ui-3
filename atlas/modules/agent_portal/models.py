"""SQLAlchemy models for the Agent Portal server-side state store.

Mirrors the pattern in ``atlas/modules/chat_history/models.py``: String(36)
UUID PKs, Text JSON blobs, no DB-level FK constraints (DuckDB does not
support CASCADE), and per-user scoping enforced in the repository layer.

The PortalStore holds *configuration / UI state*, not running processes.
Process state stays in the in-memory ``ProcessManager``. See
``AGENT_PORTAL_ACTION_PLAN.md`` Phase 1.5.
"""

from __future__ import annotations

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
    """Base class for Agent Portal models. Kept distinct from chat_history's
    Base so the two stores can use independent metadata / migrations."""
    pass


def _uuid_default() -> str:
    return str(uuid.uuid4())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class LaunchHistoryRecord(Base):
    """Recent-launch entries that used to live in localStorage under
    ``atlas.agentPortal.launchHistory.v1``.

    Key shape preserved so the frontend's de-dup-by-content logic still
    works without round-tripping through the user's browser.
    """

    __tablename__ = "agent_portal_launch_history"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)
    # JSON blob holding the same UI shape the frontend already uses
    # (command, argsString, cwd, sandboxMode, extraWritablePaths, ...).
    # Storing as opaque JSON keeps the schema stable even when the launch
    # form gains new fields.
    payload_json = Column(Text, nullable=False)
    # Stable hash of the launch (command + args + cwd + sandboxMode) so
    # we can de-dupe inserts without re-parsing the JSON every time.
    dedup_key = Column(String(512), nullable=False)
    last_used_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_email", "dedup_key", name="uq_launch_history_user_key"),
        Index("ix_launch_history_user_used", "user_email", "last_used_at"),
    )


class LaunchConfigRecord(Base):
    """Saved-config entries that used to live in localStorage under
    ``atlas.agentPortal.launchConfigs.v1``.

    These pre-date the server-side preset library and are kept as a
    distinct collection so the migration path stays simple: one-shot
    upload from localStorage on first server fetch returning empty,
    then read-on-startup, write-through-to-server.
    """

    __tablename__ = "agent_portal_launch_configs"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_launch_configs_user_updated", "user_email", "updated_at"),
    )


class LayoutRecord(Base):
    """Last-known multi-pane layout per user.

    One row per user — the layout is a single JSON blob describing the
    grid mode (single / 2x2 / 3x2 / focus+strip), the slot ordering, and
    the slot->process_id mapping. Stored as opaque JSON so layout schema
    changes don't require migrations.
    """

    __tablename__ = "agent_portal_layouts"

    user_email = Column(String(255), primary_key=True)
    layout_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)


class GroupRecord(Base):
    """Group definitions (Phase 3 fills in semantics; schema is stubbed
    here so PortalStore is one consistent migration).

    A group bundles several panes under shared budgets and a parent
    cgroup. ``owner`` is fixed at create time — see Open Questions #1
    in the action plan.
    """

    __tablename__ = "agent_portal_groups"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    owner = Column(String(255), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    max_panes = Column(Integer, nullable=True)
    mem_budget_bytes = Column(Integer, nullable=True)
    cpu_budget_pct = Column(Integer, nullable=True)
    idle_kill_seconds = Column(Integer, nullable=True)
    audit_tag = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)

    __table_args__ = (
        Index("ix_groups_owner_name", "owner", "name"),
    )


class BundleRecord(Base):
    """Preset bundles — multiple presets launched into one group as a
    single click. Phase 4 fills in the launch logic; schema is stubbed
    here so all PortalStore tables exist from day one."""

    __tablename__ = "agent_portal_bundles"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    owner = Column(String(255), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    # JSON: { "group_template": {...}, "members": [{"preset_id": "...", "display_name_override": "..."}, ...] }
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now_utc, onupdate=_now_utc, nullable=False)


class AuditEventRecord(Base):
    """Append-only audit log for portal actions (Phase 4 wires up
    writes; the table is created now so schema drift is one phase
    instead of two)."""

    __tablename__ = "agent_portal_audit"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    ts = Column(DateTime(timezone=True), default=_now_utc, nullable=False, index=True)
    user_email = Column(String(255), nullable=False, index=True)
    event = Column(String(100), nullable=False)
    group_id = Column(String(36), nullable=True, index=True)
    process_id = Column(String(36), nullable=True, index=True)
    executor = Column(String(50), nullable=True)  # local | container | remote
    # JSON blob for event-specific fields so the schema doesn't need to
    # know every event variant up front.
    detail_json = Column(Text, nullable=True)
