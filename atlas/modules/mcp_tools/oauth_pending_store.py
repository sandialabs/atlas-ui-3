"""Server-side store for in-flight MCP OAuth authorization requests.

Starlette sessions are *signed*, not encrypted: the cookie's contents are
readable by anyone holding it. The PKCE ``code_verifier`` is the secret that
binds the authorization code to this client, so it does not belong there.

The split used here keeps the browser holding only values it is already
allowed to see:

- the server-side record (verifier, redirect URI, user, server) lives in this
  process, keyed by the ``state``;
- the session cookie carries only the set of ``state`` values this browser
  started, which the provider sees anyway as a query parameter.

The session is still what binds the callback to the browser -- a state absent
from the cookie is rejected -- so CSRF protection is unchanged while the
verifier stops travelling to the client.

Records are single-use and expire, so an abandoned flow leaves nothing behind.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# How long a user has to complete the provider's consent screen.
PENDING_TTL_SECONDS = 600

# Ceiling on concurrent in-flight authorizations, so a script hammering
# /oauth/start cannot grow this store without bound.
MAX_PENDING_RECORDS = 2048


@dataclass
class PendingAuthorization:
    """One in-flight authorization request."""

    server_name: str
    user: str
    code_verifier: str
    redirect_uri: str
    created_at: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.created_at >= PENDING_TTL_SECONDS


class PendingAuthorizationStore:
    """In-process, TTL-bounded store of pending authorizations."""

    def __init__(self) -> None:
        self._records: Dict[str, PendingAuthorization] = {}
        self._lock = threading.Lock()

    def _purge_locked(self, now: float) -> None:
        expired = [
            state for state, record in self._records.items() if record.is_expired(now)
        ]
        for state in expired:
            self._records.pop(state, None)

        if len(self._records) <= MAX_PENDING_RECORDS:
            return
        # Oldest first: a flood of new starts must not evict a record whose
        # user is part-way through consenting any sooner than necessary.
        ordered = sorted(self._records.items(), key=lambda item: item[1].created_at)
        for state, _ in ordered[: len(self._records) - MAX_PENDING_RECORDS]:
            self._records.pop(state, None)

    def put(self, state: str, record: PendingAuthorization) -> None:
        with self._lock:
            # Insert first, then purge, so the cap counts the new record too.
            self._records[state] = record
            self._purge_locked(time.time())

    def take(self, state: str) -> Optional[PendingAuthorization]:
        """Return and remove the record for ``state``. Single-use by design."""
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            record = self._records.pop(state, None)
        if record is None or record.is_expired(now):
            return None
        return record

    def discard(self, state: str) -> None:
        with self._lock:
            self._records.pop(state, None)

    def clear(self) -> int:
        with self._lock:
            count = len(self._records)
            self._records.clear()
        return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


_store: Optional[PendingAuthorizationStore] = None
_store_lock = threading.Lock()


def get_pending_store() -> PendingAuthorizationStore:
    """Return the process-wide pending-authorization store."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = PendingAuthorizationStore()
    return _store


def reset_pending_store() -> None:
    """Drop the singleton. Used by tests."""
    global _store
    with _store_lock:
        _store = None
