"""Append-only audit records for chat-driven tool approval decisions.

The tool executor already emits execution telemetry. This module records the
approval decision that precedes execution so operators can correlate the two
using ``tool_call_id`` without storing raw tool arguments.

``decision_args_sha256`` is a SHA-256 of the canonical JSON for the arguments
seen at the *decision* boundary (the PresentedCall, or the approved edited
form). It is a correlation/integrity fingerprint, not a confidentiality
control: low-entropy arguments (filenames, flags, IDs) can be confirmed by
enumerating likely values. Access control therefore rests on the audit file
permissions (directory ``0o700``, file ``0o600``), not on the hash.

Trusted fields such as ``_atlas_user`` may be re-injected after the decision,
so this hash is intentionally not an execution hash. Correlate later
execution evidence by ``tool_call_id`` rather than assuming the hashes match.

The JSONL file is unbounded. Operators must rotate or archive
``TOOL_CALL_AUDIT_PATH`` (default ``data/tool_call_audit.jsonl``) according to
local retention policy.

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
    return path


def _append_audit_line(path: Path, line: str) -> None:
    """Append one JSONL line, creating an owner-only file when needed."""
    parent = path.parent
    created_parent = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if created_parent:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        handle.write(line)


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
    request_owner: str = "",
) -> Dict[str, Any]:
    """Append one tool approval decision to the JSONL audit trail.

    Raw arguments and rejection reasons are intentionally excluded. The hash is
    of one canonical client-visible argument representation at the decision
    boundary; later execution may re-inject trusted fields before invoking the
    tool. Execution evidence should therefore be correlated by ``tool_call_id``
    rather than assuming the hashes are equal.

    ``user`` is the actor who produced this decision row (the responder).
    ``request_owner`` is the user bound to the pending call. They differ on
    cross-user ownership failures.

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
            "request_owner": request_owner,
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
        with _lock:
            _append_audit_line(path, line)
    except Exception as exc:
        logger.warning("tool audit log write failed (%s)", type(exc).__name__)

    return record
