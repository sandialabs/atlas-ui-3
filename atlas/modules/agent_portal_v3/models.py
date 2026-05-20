"""SQLAlchemy models for Agent Portal V3 (K8s Job-backed agent runs).

Each row in agent_portal_v3_runs maps 1:1 to a Kubernetes Job. Events
are append-only and back the runs detail view.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _uuid_default() -> str:
    return str(uuid.uuid4())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Lifecycle states for the run record. Strings (not enum) so DuckDB
# migrations stay simple.
RUN_STATUS_PENDING = "pending"        # created in DB, Job not submitted yet
RUN_STATUS_LAUNCHING = "launching"    # Job submitted, no pod yet
RUN_STATUS_RUNNING = "running"        # pod is running
RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_UNKNOWN = "unknown"

ACTIVE_STATUSES = {
    RUN_STATUS_PENDING,
    RUN_STATUS_LAUNCHING,
    RUN_STATUS_RUNNING,
}

TERMINAL_STATUSES = {
    RUN_STATUS_SUCCEEDED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
}


class AgentRunRecord(Base):
    __tablename__ = "agent_portal_v3_runs"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    user_email = Column(String(255), nullable=False, index=True)

    display_name = Column(String(200), nullable=False, default="")
    prompt = Column(Text, nullable=False)

    # JSON list of MCP server names selected from atlas mcp.json
    mcp_servers_json = Column(Text, nullable=False, default="[]")
    # Resolved MCP config (server name -> {transport, url, ...}) at launch time
    mcp_resolved_json = Column(Text, nullable=False, default="{}")

    llm_provider = Column(String(50), nullable=False, default="anthropic")
    llm_model = Column(String(200), nullable=False, default="")

    status = Column(String(32), nullable=False, default=RUN_STATUS_PENDING, index=True)
    exit_code = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    namespace = Column(String(100), nullable=False, default="atlas")
    job_name = Column(String(200), nullable=True, index=True)
    pod_name = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now_utc, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=_now_utc,
        onupdate=_now_utc,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_v3_runs_user_created", "user_email", "created_at"),
        Index("ix_v3_runs_status_updated", "status", "updated_at"),
    )


class AgentRunEventRecord(Base):
    """Append-only event log for a run.

    kind: 'system' (orchestrator notes), 'stdout' (pod stdout chunk),
    'stderr', 'status' (lifecycle transition).
    """

    __tablename__ = "agent_portal_v3_run_events"

    id = Column(String(36), primary_key=True, default=_uuid_default)
    run_id = Column(String(36), nullable=False, index=True)
    ts = Column(DateTime(timezone=True), default=_now_utc, nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_v3_events_run_ts", "run_id", "ts"),
    )
