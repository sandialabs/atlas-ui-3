"""Append-only JSONL audit sink for the Agent Portal.

The DB-backed ``agent_portal_audit`` table (see ``models.py``) is one
half of this; a sibling JSONL file is the other half. The JSONL exists
so a compliance reader can tail a single file without joining tables,
and so an external SIEM can pick the events up via filebeat / fluentd
without database access.

Both writers are idempotent — a single ``record_event`` call writes to
both sinks. Failures are logged but never raise; auditing is best-
effort from the caller's perspective and never blocks user actions.

Schema
------
    {
        "ts": "<ISO8601 UTC>",
        "user": "<email>",
        "event": "<verb>",      # launch, cancel, group_create, etc.
        "group_id": "<id|null>",
        "process_id": "<id|null>",
        "executor": "local|container|remote",
        "detail": { ... event-specific ... }
    }

The ``executor`` field is included from day one so future container /
remote backends can fan into the same log without a schema bump
(see Action plan, executor-seam discipline).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The audit file lives next to the DuckDB file (data/agent_portal.db),
# unless overridden by AGENT_PORTAL_AUDIT_PATH. Default keeps the data
# directory cohesive — backup one location, get the whole portal state.
DEFAULT_AUDIT_PATH = "data/agent_portal_audit.jsonl"

_lock = threading.Lock()
_resolved_path: Optional[Path] = None


def _resolve_audit_path() -> Path:
    """Resolve the audit log path the same way the DB resolver does:
    relative paths land under the project root, absolute paths win."""
    global _resolved_path
    if _resolved_path is not None:
        return _resolved_path
    raw = os.environ.get("AGENT_PORTAL_AUDIT_PATH", DEFAULT_AUDIT_PATH)
    p = Path(raw)
    if not p.is_absolute():
        # atlas/modules/agent_portal/audit_log.py → up four to project root
        project_root = Path(__file__).parent.parent.parent.parent
        p = project_root / p
    p.parent.mkdir(parents=True, exist_ok=True)
    _resolved_path = p
    return p


def reset_path_cache_for_tests() -> None:
    """Test-only — clear the memoized path so a temp env var takes effect."""
    global _resolved_path
    _resolved_path = None


def _write_jsonl(record: Dict[str, Any]) -> None:
    path = _resolve_audit_path()
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        # Open + close per record so a long-running server doesn't keep
        # the fd open if the file is rotated out from under us.
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        logger.warning("audit log write failed (%s): %s", path, exc)


def record_event(
    user_email: str,
    event: str,
    *,
    group_id: Optional[str] = None,
    process_id: Optional[str] = None,
    executor: str = "local",
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write one audit record to both the DuckDB table and the JSONL
    file. Returns the record dict (mostly for tests)."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user_email,
        "event": event,
        "group_id": group_id,
        "process_id": process_id,
        "executor": executor,
        "detail": detail or None,
    }
    # JSONL first — even if the DB write fails, the JSONL line lands.
    _write_jsonl(record)
    # DB write is best-effort and isolated from the JSONL write — a
    # corrupt PortalStore singleton must not silence the JSONL log.
    try:
        from atlas.modules.agent_portal.portal_store import get_portal_store
        store = get_portal_store()
        store.append_audit(
            user_email,
            event,
            group_id=group_id,
            process_id=process_id,
            executor=executor,
            detail=detail,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audit DB write failed: %s", exc)
    return record
