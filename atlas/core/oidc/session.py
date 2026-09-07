"""Server-side session store for OIDC logins.

The browser receives only a signed cookie holding an opaque session id. Access
tokens, refresh tokens, and ID token claims stay in this process, inside the
Atlas credential boundary -- an explicit requirement of the delegated-credential
design: agents and MCP servers must never see the user's refresh token or the
primary credentials behind it.

The store is in-process and therefore per-worker. That is deliberate for a
first implementation: it means a restart or a second uvicorn worker forces a
fresh (cheap, silent) IdP round trip rather than putting long-lived
credentials into shared storage. Deployments that need sticky multi-worker
sessions should run a single worker or terminate login at the proxy using the
existing trusted-header mode.
"""

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Cookie key holding the opaque session id.
SESSION_COOKIE_KEY = "atlas_oidc_sid"

# Ceiling on live sessions. The store is memory-resident and a login is
# unauthenticated up to the callback, so an unbounded map is a growth lever.
MAX_SESSIONS = 10000

# Access tokens are refreshed once fewer than this many seconds remain.
ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 60


@dataclass
class OIDCSession:
    """One logged-in user's server-side state."""

    session_id: str
    user_id: str
    subject: Optional[str] = None
    id_token_claims: Dict[str, Any] = field(default_factory=dict)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    access_token_expires_at: Optional[float] = None
    scope: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Whether the login session itself has aged out."""
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def access_token_needs_refresh(self, now: Optional[float] = None) -> bool:
        """Whether the access token is at or near expiry."""
        if self.access_token_expires_at is None:
            return False
        current = now if now is not None else time.time()
        return current >= (self.access_token_expires_at - ACCESS_TOKEN_REFRESH_MARGIN_SECONDS)

    def to_public_dict(self) -> Dict[str, Any]:
        """Status projection safe to return over the API -- no token material."""
        return {
            "user": self.user_id,
            "subject": self.subject,
            "scope": self.scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "has_refresh_token": bool(self.refresh_token),
        }


class OIDCSessionStore:
    """Thread-safe in-process map of session id to :class:`OIDCSession`."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._sessions: Dict[str, OIDCSession] = {}
        self._lock = threading.Lock()
        self._max_sessions = max_sessions

    def create(
        self,
        *,
        user_id: str,
        subject: Optional[str] = None,
        id_token_claims: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        access_token_expires_at: Optional[float] = None,
        scope: str = "",
        max_age_seconds: Optional[int] = None,
    ) -> OIDCSession:
        """Register a new login session and return it."""
        session = OIDCSession(
            session_id=secrets.token_urlsafe(32),
            user_id=user_id,
            subject=subject,
            id_token_claims=id_token_claims or {},
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=access_token_expires_at,
            scope=scope,
            expires_at=(time.time() + max_age_seconds) if max_age_seconds else None,
        )
        with self._lock:
            self._prune_locked()
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: Optional[str]) -> Optional[OIDCSession]:
        """Look up a live session, dropping it if it has expired."""
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired():
                del self._sessions[session_id]
                return None
            return session

    def remove(self, session_id: Optional[str]) -> bool:
        """Delete a session. Returns whether one was present."""
        if not session_id:
            return False
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def update_tokens(
        self,
        session_id: str,
        *,
        access_token: Optional[str],
        refresh_token: Optional[str] = None,
        access_token_expires_at: Optional[float] = None,
        scope: Optional[str] = None,
    ) -> Optional[OIDCSession]:
        """Store refreshed token material on an existing session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.access_token = access_token
            # A refresh response may omit the refresh token, which means "keep
            # using the one you have"; overwriting with None would silently
            # end the session's ability to refresh.
            if refresh_token:
                session.refresh_token = refresh_token
            session.access_token_expires_at = access_token_expires_at
            if scope is not None:
                session.scope = scope
            return session

    def iter_sessions(self):
        """Snapshot of the live sessions.

        A list copy, not a live view: callers iterate outside the lock, and a
        concurrent login must not mutate the map mid-iteration.
        """
        with self._lock:
            now = time.time()
            return [s for s in self._sessions.values() if not s.is_expired(now)]

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _prune_locked(self) -> None:
        now = time.time()
        for key in [k for k, s in self._sessions.items() if s.is_expired(now)]:
            del self._sessions[key]
        while len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            del self._sessions[oldest]
            logger.warning("OIDC session store at capacity; evicted the oldest session")


_store = OIDCSessionStore()


def get_session_store() -> OIDCSessionStore:
    """Return the process-wide OIDC session store."""
    return _store
