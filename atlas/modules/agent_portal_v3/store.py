"""Repository layer for Agent Portal V3.

User-scoped reads/writes -- callers MUST pass user_email so the layer
above (route handlers) cannot accidentally leak runs across users.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import sessionmaker

from .database import get_session_factory, init_database
from .models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AgentRunEventRecord,
    AgentRunRecord,
    RUN_STATUS_PENDING,
)

logger = logging.getLogger(__name__)


class RunNotFoundError(LookupError):
    pass


class AgentRunStore:
    def __init__(self, session_factory: Optional[sessionmaker] = None) -> None:
        if session_factory is None:
            init_database()
            session_factory = get_session_factory()
        self._sf = session_factory

    # ---- mutations ----

    def create_run(
        self,
        *,
        user_email: str,
        display_name: str,
        prompt: str,
        mcp_servers: List[str],
        mcp_resolved: Dict[str, Any],
        llm_provider: str,
        llm_model: str,
        namespace: str = "atlas",
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            user_email=user_email,
            display_name=display_name or llm_model or "agent-run",
            prompt=prompt,
            mcp_servers_json=json.dumps(mcp_servers or []),
            mcp_resolved_json=json.dumps(mcp_resolved or {}),
            llm_provider=llm_provider,
            llm_model=llm_model,
            namespace=namespace,
            status=RUN_STATUS_PENDING,
        )
        with self._sf() as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
        return record

    def update_run(
        self,
        *,
        run_id: str,
        user_email: Optional[str] = None,
        **fields: Any,
    ) -> AgentRunRecord:
        with self._sf() as session:
            q = session.query(AgentRunRecord).filter(AgentRunRecord.id == run_id)
            if user_email is not None:
                q = q.filter(AgentRunRecord.user_email == user_email)
            record = q.one_or_none()
            if record is None:
                raise RunNotFoundError(run_id)
            for k, v in fields.items():
                if hasattr(record, k):
                    setattr(record, k, v)
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            session.expunge(record)
        return record

    def mark_status(
        self,
        run_id: str,
        status: str,
        *,
        error: Optional[str] = None,
        exit_code: Optional[int] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        job_name: Optional[str] = None,
        pod_name: Optional[str] = None,
    ) -> AgentRunRecord:
        fields: Dict[str, Any] = {"status": status}
        if error is not None:
            fields["error"] = error
        if exit_code is not None:
            fields["exit_code"] = exit_code
        if started_at is not None:
            fields["started_at"] = started_at
        if finished_at is not None:
            fields["finished_at"] = finished_at
        if job_name is not None:
            fields["job_name"] = job_name
        if pod_name is not None:
            fields["pod_name"] = pod_name
        return self.update_run(run_id=run_id, **fields)

    def append_event(self, run_id: str, kind: str, message: str) -> None:
        evt = AgentRunEventRecord(run_id=run_id, kind=kind, message=message)
        with self._sf() as session:
            session.add(evt)
            session.commit()

    def delete_run(self, run_id: str, user_email: str) -> bool:
        with self._sf() as session:
            q = (
                session.query(AgentRunRecord)
                .filter(
                    AgentRunRecord.id == run_id,
                    AgentRunRecord.user_email == user_email,
                )
            )
            record = q.one_or_none()
            if record is None:
                return False
            session.delete(record)
            # cascade events manually (no DB FKs)
            session.query(AgentRunEventRecord).filter(
                AgentRunEventRecord.run_id == run_id
            ).delete(synchronize_session=False)
            session.commit()
            return True

    # ---- reads ----

    def get_run(self, run_id: str, user_email: Optional[str] = None) -> AgentRunRecord:
        with self._sf() as session:
            q = session.query(AgentRunRecord).filter(AgentRunRecord.id == run_id)
            if user_email is not None:
                q = q.filter(AgentRunRecord.user_email == user_email)
            record = q.one_or_none()
            if record is None:
                raise RunNotFoundError(run_id)
            session.expunge(record)
        return record

    def list_runs(
        self,
        user_email: str,
        *,
        limit: int = 100,
        include_terminal: bool = True,
    ) -> List[AgentRunRecord]:
        with self._sf() as session:
            q = session.query(AgentRunRecord).filter(
                AgentRunRecord.user_email == user_email
            )
            if not include_terminal:
                q = q.filter(AgentRunRecord.status.in_(ACTIVE_STATUSES))
            q = q.order_by(desc(AgentRunRecord.created_at)).limit(limit)
            records = q.all()
            for r in records:
                session.expunge(r)
        return records

    def list_active_runs(self) -> List[AgentRunRecord]:
        """All non-terminal runs across all users (for the watcher)."""
        with self._sf() as session:
            records = (
                session.query(AgentRunRecord)
                .filter(AgentRunRecord.status.in_(ACTIVE_STATUSES))
                .all()
            )
            for r in records:
                session.expunge(r)
        return records

    def list_events(self, run_id: str, *, limit: int = 1000) -> List[AgentRunEventRecord]:
        with self._sf() as session:
            records = (
                session.query(AgentRunEventRecord)
                .filter(AgentRunEventRecord.run_id == run_id)
                .order_by(AgentRunEventRecord.ts.asc())
                .limit(limit)
                .all()
            )
            for r in records:
                session.expunge(r)
        return records


_store: Optional[AgentRunStore] = None


def get_agent_run_store() -> AgentRunStore:
    global _store
    if _store is None:
        _store = AgentRunStore()
    return _store


def reset_store() -> None:
    global _store
    _store = None
