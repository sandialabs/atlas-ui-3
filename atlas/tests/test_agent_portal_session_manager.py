"""Unit tests for the Agent Portal session-manager state machine."""

from datetime import datetime, timedelta, timezone

import pytest

from atlas.modules.agent_portal.models import (
    LaunchSpec,
    SandboxTier,
    SessionState,
)
from atlas.modules.agent_portal.session_manager import (
    InvalidTransitionError,
    ReapPolicy,
    SessionManager,
)


def _spec() -> LaunchSpec:
    return LaunchSpec(
        scope="test scope",
        agent_command=["/bin/true"],
        sandbox_tier=SandboxTier.restrictive,
    )


def test_create_returns_pending_session():
    sm = SessionManager()
    s = sm.create(user_email="a@b", spec=_spec())
    assert s.state is SessionState.pending
    assert s.id
    assert sm.get(s.id) is s


def test_happy_path_transitions():
    sm = SessionManager()
    s = sm.create("a@b", _spec())
    sm.transition(s.id, SessionState.launching)
    sm.transition(s.id, SessionState.running)
    sm.transition(s.id, SessionState.ending)
    sm.transition(s.id, SessionState.ended, reason="done")
    assert sm.get(s.id).state is SessionState.ended
    assert sm.get(s.id).termination_reason == "done"


def test_skipping_states_rejected():
    sm = SessionManager()
    s = sm.create("a@b", _spec())
    with pytest.raises(InvalidTransitionError):
        sm.transition(s.id, SessionState.running)  # pending -> running not allowed


def test_no_transitions_out_of_terminal():
    sm = SessionManager()
    s = sm.create("a@b", _spec())
    sm.transition(s.id, SessionState.failed)
    with pytest.raises(InvalidTransitionError):
        sm.transition(s.id, SessionState.running)


def test_list_for_user_filters():
    sm = SessionManager()
    a = sm.create("alice@x", _spec())
    b = sm.create("bob@x", _spec())
    assert [s.id for s in sm.list_for_user("alice@x")] == [a.id]
    assert [s.id for s in sm.list_for_user("bob@x")] == [b.id]


def test_find_reapable_by_idle():
    sm = SessionManager(ReapPolicy(idle_timeout_s=60, hard_ttl_s=86_400))
    s = sm.create("a@b", _spec())
    sm.transition(s.id, SessionState.launching)
    sm.transition(s.id, SessionState.running)
    # simulate the session being idle for 2 minutes
    s.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    reapable = sm.find_reapable()
    assert len(reapable) == 1
    assert reapable[0][1] == "idle_timeout"


def test_find_reapable_by_hard_ttl():
    sm = SessionManager(ReapPolicy(idle_timeout_s=1_000_000, hard_ttl_s=60))
    s = sm.create("a@b", _spec())
    sm.transition(s.id, SessionState.launching)
    sm.transition(s.id, SessionState.running)
    s.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    s.last_activity_at = datetime.now(timezone.utc)
    reapable = sm.find_reapable()
    assert len(reapable) == 1
    assert reapable[0][1] == "hard_ttl"


def test_terminal_sessions_are_not_reaped():
    sm = SessionManager(ReapPolicy(idle_timeout_s=1, hard_ttl_s=1))
    s = sm.create("a@b", _spec())
    sm.transition(s.id, SessionState.failed)
    assert sm.find_reapable() == []
