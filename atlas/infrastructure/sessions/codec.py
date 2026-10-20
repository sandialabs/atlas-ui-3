"""Serialize a ``Session`` to and from the JSON a shared store keeps (issue #1091).

A session mixes two kinds of state:

* **Serializable session data** -- identity, owner, history, and the
  JSON-shaped ``context`` entries (conversation binding, file map, workspace,
  classification binding, incognito flag). This is what another replica needs
  to pick the session up, and what the codec writes.
* **Process-local runtime handles** -- objects that only mean something inside
  the process that made them (a turn's citation register, task or channel
  objects). They cannot move between replicas, so the codec leaves them out.

Known runtime keys are listed in ``PROCESS_LOCAL_CONTEXT_KEYS``. Any other
context entry that does not survive a JSON round trip is dropped with a
warning naming the key, so a new runtime handle never breaks a write and the
omission is visible in the log.

Incognito sessions are stored without their messages (see ``encode_session``).
"""

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict
from uuid import UUID

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.messages.models import ConversationHistory, Message
from atlas.domain.sessions.models import Session

logger = logging.getLogger(__name__)

PAYLOAD_FORMAT = 1

# Context entries that are per-process by construction and never stored.
PROCESS_LOCAL_CONTEXT_KEYS = frozenset({"citation_register"})


def _json_default(value: Any) -> Any:
    """Encode the few non-JSON scalars that legitimately appear in session data."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _serializable_context(session: Session) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    for key, value in session.context.items():
        if key in PROCESS_LOCAL_CONTEXT_KEYS:
            continue
        try:
            context[key] = json.loads(json.dumps(value, default=_json_default))
        except (TypeError, ValueError):
            logger.warning(
                "Session %s context key %s holds a process-local value (%s); "
                "it is not written to the shared session store",
                sanitize_for_logging(str(session.id)),
                sanitize_for_logging(str(key)),
                type(value).__name__,
            )
    return context


def encode_session(session: Session) -> bytes:
    """Return the stored form of ``session``.

    An incognito session's messages are never written: incognito promises the
    conversation is not stored, and the shared store is storage. Its context
    (bindings, save floor) is, so a resumed incognito session stays incognito
    and keeps its classification binding; it resumes with an empty history,
    as a reconnect always did before shared sessions.
    """
    incognito = session.context.get("incognito") is True
    payload = {
        "format": PAYLOAD_FORMAT,
        "id": str(session.id),
        "user_email": session.user_email,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "active": session.active,
        "connection_owner": session.connection_owner,
        "history": [] if incognito else [m.to_dict() for m in session.history.messages],
        "history_omitted": incognito,
        "context": _serializable_context(session),
    }
    # Message metadata is free-form; anything left that JSON cannot express is
    # stored as its string form rather than failing the whole write.
    return json.dumps(
        payload, default=_safe_default, separators=(",", ":")
    ).encode("utf-8")


def _safe_default(value: Any) -> Any:
    try:
        return _json_default(value)
    except TypeError:
        return str(value)


def decode_session(raw: bytes, version: int) -> Session:
    """Rebuild a ``Session`` from its stored form, tagged with ``version``."""
    payload = json.loads(raw)
    if payload.get("format") != PAYLOAD_FORMAT:
        raise ValueError(f"Unsupported session payload format {payload.get('format')!r}")
    session = Session(
        id=UUID(payload["id"]),
        user_email=payload.get("user_email"),
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
        history=ConversationHistory(
            messages=[Message.from_dict(m) for m in payload.get("history", [])]
        ),
        context=payload.get("context") or {},
        active=bool(payload.get("active", True)),
        connection_owner=payload.get("connection_owner"),
    )
    session.store_version = version
    return session
