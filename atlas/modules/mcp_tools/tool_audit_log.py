"""Append-only audit records for chat-driven tool approval decisions.

The tool executor already emits execution telemetry. This module records the
approval decision that precedes execution so operators can correlate the two
using ``tool_call_id`` without storing raw tool arguments.

Audit writes are best-effort and must never block or fail a user action.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_PATH = "data/tool_call_audit.jsonl"

_lock = threading.Lock()


def _resolve_audit_path() -> Path:
    """Resolve the configured audit path relative to the project root."""
    raw = os.environ.get("TOOL_CALL_AUDIT_PATH", DEFAULT_AUDIT_PATH)
    path = Path(raw)
    if not path.is_absolute():
        project_root = Path(__file__).parent.parent.parent.parent
        path = project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def hash_arguments(arguments: Dict[str, Any]) -> str:
    """Return a deterministic SHA-256 for the canonical JSON representation."""
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_tool_decision(
    *,
    user_email: str,
    tool_call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    decision: str,
    decision_origin: str = "approval_response",
    arguments_edited: bool = False,
    reason_present: bool = False,
) -> Dict[str, Any]:
    """Append one tool approval decision to the JSONL audit trail.

    Raw arguments and rejection reasons are intentionally excluded. The hash is
    of one canonical client-visible argument representation at the decision
    boundary; later execution may re-inject trusted fields before invoking the
    tool. Execution evidence should therefore be correlated by ``tool_call_id``
    rather than assuming the hashes are equal.

    This sink is deliberately best-effort: malformed/unserializable payloads,
    path resolution failures, and write failures must never affect approval or
    timeout behavior. In those cases no evidence row is emitted rather than
    inventing a fallback representation.
    """
    record: Dict[str, Any] = {}
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tool_approval_decision",
            "user": user_email,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "decision_args_sha256": hash_arguments(arguments),
            "decision": decision,
            "decision_origin": decision_origin,
            "arguments_edited": bool(arguments_edited),
            "reason_present": bool(reason_present),
        }
        path = _resolve_audit_path()
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception as exc:
        logger.warning("tool audit log write failed (%s)", type(exc).__name__)

    return record
