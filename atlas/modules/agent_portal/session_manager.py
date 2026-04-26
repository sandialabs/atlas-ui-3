"""In-memory SessionManager with an explicit state machine.

v0 scope: single-process, single-host, in-memory. A future follow-up
may persist sessions to survive backend restarts; the adapter handle
field is already a plain dict so that migration is a serialization
concern, not a model change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from atlas.modules.agent_portal.models import (
    TERMINAL_STATES,
    LaunchSpec,
    Session,
    SessionState,
)

# Forward-only transitions. Any state may fall through to `failed`; the
# reaper moves `running` -> `ending` -> `reaped` on timeout.
_ALLOWED_TRANSITIONS: Dict[SessionState, frozenset[SessionState]] = {
    SessionState.pending: frozenset({SessionState.authenticating, SessionState.launching, SessionState.failed}),
    SessionState.authenticating: frozenset({SessionState.launching, SessionState.failed}),
    SessionState.launching: frozenset({SessionState.running, SessionState.failed}),
    SessionState.running: frozenset({SessionState.ending, SessionState.failed}),
    SessionState.ending: frozenset({SessionState.ended, SessionState.failed, SessionState.reaped}),
    SessionState.ended: frozenset(),
    SessionState.failed: frozenset(),
    SessionState.reaped: frozenset(),
}


class InvalidTransitionError(ValueError):
    """Raised when a caller attempts a disallowed state transition."""


@dataclass
class ReapPolicy:
    """How aggressively the reaper terminates idle sessions."""

    idle_timeout_s: int = 3600
    hard_ttl_s: int = 86_400


class SessionManager:
    """In-memory store of sessions plus state-machine enforcement."""

    def __init__(self, reap_policy: Optional[ReapPolicy] = None) -> None:
        self._sessions: Dict[str, Session] = {}
        self._reap_policy = reap_policy or ReapPolicy()

    # --- CRUD -----------------------------------------------------------------

    def create(self, user_email: str, spec: LaunchSpec) -> Session:
        session = Session(user_email=user_email, spec=spec)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"session {session_id!r} not found") from exc

    def list_for_user(self, user_email: str) -> List[Session]:
        return [s for s in self._sessions.values() if s.user_email == user_email]

    def list_active(self) -> List[Session]:
        return [s for s in self._sessions.values() if s.state not in TERMINAL_STATES]

    # --- State machine --------------------------------------------------------

    def transition(
        self,
        session_id: str,
        new_state: SessionState,
        *,
        reason: Optional[str] = None,
    ) -> Session:
        session = self.get(session_id)
        allowed = _ALLOWED_TRANSITIONS.get(session.state, frozenset())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"cannot move {session.state.value} -> {new_state.value}"
            )
        session.state = new_state
        if reason is not None:
            session.termination_reason = reason
        session.touch()
        return session

    def set_adapter(self, session_id: str, adapter_name: str, handle: Dict) -> Session:
        session = self.get(session_id)
        session.adapter_name = adapter_name
        session.adapter_handle = dict(handle)
        session.touch()
        return session

    def set_audit_path(self, session_id: str, audit_path: str) -> Session:
        session = self.get(session_id)
        session.audit_path = audit_path
        session.touch()
        return session

    def mark_activity(self, session_id: str) -> Session:
        session = self.get(session_id)
        session.touch()
        return session

    # --- Reaper helpers -------------------------------------------------------

    def find_reapable(self, now_epoch: Optional[float] = None) -> List[Tuple[Session, str]]:
        """Return `[(session, reason), ...]` for sessions that should be ended."""
        now = now_epoch if now_epoch is not None else time.time()
        candidates: List[Tuple[Session, str]] = []
        for s in self.list_active():
            if s.state is not SessionState.running:
                continue
            age = now - s.created_at.timestamp()
            idle = now - s.last_activity_at.timestamp()
            if age > self._reap_policy.hard_ttl_s:
                candidates.append((s, "hard_ttl"))
            elif idle > self._reap_policy.idle_timeout_s:
                candidates.append((s, "idle_timeout"))
        return candidates

    @property
    def reap_policy(self) -> ReapPolicy:
        return self._reap_policy
