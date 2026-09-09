"""Tests for parallel conversation runs (issue #884).

Covers the three invariants the feature rests on -- runs are isolated from each
other, a conversation admits only one run at a time, and a user cannot exceed
the configured cap -- plus the transport-level routing helpers that decide
which run a stop/approval frame addresses.
"""

import asyncio
import time

import pytest

from atlas.application.chat.runs import (
    ConcurrencyLimitError,
    ConversationBusyError,
    RunRegistry,
    RunStatus,
    get_run_registry,
    reset_run_registry,
)
from atlas.application.chat.runs.eligibility import (
    save_mode_is_server,
    turn_is_eligible_for_background_run,
)


@pytest.fixture
def registry():
    reset_run_registry()
    yield RunRegistry(max_concurrent_runs_per_user=3)
    reset_run_registry()


USER = "a@example.com"
OTHER = "b@example.com"


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

def test_two_conversations_run_concurrently(registry):
    """The headline acceptance criterion: A keeps running while B starts."""
    run_a = registry.start(conversation_id="conv-a", user_email=USER)
    run_b = registry.start(conversation_id="conv-b", user_email=USER)

    assert run_a.run_id != run_b.run_id
    assert {r.run_id for r in registry.active_for_user(USER)} == {
        run_a.run_id,
        run_b.run_id,
    }


def test_each_run_gets_its_own_session(registry):
    """Two concurrent conversations must not share a Session/history object."""
    run_a = registry.start(conversation_id="conv-a", user_email=USER)
    run_b = registry.start(conversation_id="conv-b", user_email=USER)
    assert run_a.session_id != run_b.session_id


def test_second_run_for_same_conversation_is_refused(registry):
    registry.start(conversation_id="conv-a", user_email=USER)
    with pytest.raises(ConversationBusyError):
        registry.start(conversation_id="conv-a", user_email=USER)


def test_conversation_frees_up_after_its_run_ends(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.COMPLETED)
    # No exception: the completed run no longer holds the conversation lock.
    registry.start(conversation_id="conv-a", user_email=USER)


def test_concurrency_cap_is_enforced_per_user(registry):
    for i in range(3):
        registry.start(conversation_id=f"conv-{i}", user_email=USER)
    with pytest.raises(ConcurrencyLimitError) as exc:
        registry.start(conversation_id="conv-3", user_email=USER)
    assert "3" in str(exc.value)
    # Another user is unaffected by the first user's runs.
    registry.start(conversation_id="conv-x", user_email=OTHER)


def test_approval_paused_run_counts_against_the_cap(registry):
    """Explicit product decision on #884: waiting_for_input is not terminal."""
    for i in range(3):
        run = registry.start(conversation_id=f"conv-{i}", user_email=USER)
        registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")
    with pytest.raises(ConcurrencyLimitError):
        registry.start(conversation_id="conv-3", user_email=USER)


def test_terminal_runs_do_not_consume_capacity(registry):
    for i in range(3):
        run = registry.start(conversation_id=f"conv-{i}", user_email=USER)
        registry.set_status(run.run_id, RunStatus.COMPLETED)
    # Retained for status reporting, but they must not block new work.
    registry.start(conversation_id="conv-3", user_email=USER)


def test_cap_change_takes_effect_without_restart(registry):
    registry.set_max_concurrent_runs_per_user(1)
    registry.start(conversation_id="conv-a", user_email=USER)
    with pytest.raises(ConcurrencyLimitError):
        registry.start(conversation_id="conv-b", user_email=USER)


# ---------------------------------------------------------------------------
# Cancellation isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stopping_one_run_does_not_touch_the_other(registry):
    async def forever():
        await asyncio.sleep(60)

    run_a = registry.start(conversation_id="conv-a", user_email=USER)
    run_b = registry.start(conversation_id="conv-b", user_email=USER)
    task_a = asyncio.create_task(forever())
    task_b = asyncio.create_task(forever())
    registry.attach_task(run_a.run_id, task_a)
    registry.attach_task(run_b.run_id, task_b)

    assert registry.cancel(run_a.run_id, USER) is True
    await asyncio.sleep(0)

    assert task_a.cancelled() or task_a.cancelling()
    assert not task_b.done()
    assert registry.get(run_a.run_id).status == RunStatus.CANCELLED
    assert registry.get(run_b.run_id).status == RunStatus.RUNNING

    task_b.cancel()
    await asyncio.gather(task_a, task_b, return_exceptions=True)


def test_a_user_cannot_address_another_users_run(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    assert registry.get_for_user(run.run_id, OTHER) is None
    assert registry.cancel(run.run_id, OTHER) is False
    assert registry.get(run.run_id).status != RunStatus.CANCELLED


def test_terminal_status_is_sticky(registry):
    """A cancelled run's own unwinding must not report it as completed."""
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.CANCELLED)
    registry.set_status(run.run_id, RunStatus.COMPLETED)
    assert registry.get(run.run_id).status == RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# Status reporting / indicators
# ---------------------------------------------------------------------------

def test_listener_receives_status_transitions(registry):
    seen = []
    unsubscribe = registry.add_listener(USER, lambda r: seen.append((r.run_id, r.status)))

    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.COMPLETED)
    unsubscribe()
    registry.start(conversation_id="conv-b", user_email=USER)

    statuses = [status for _, status in seen]
    assert statuses == [RunStatus.QUEUED, RunStatus.COMPLETED]


def test_listener_only_sees_its_own_users_runs(registry):
    seen = []
    registry.add_listener(USER, seen.append)
    registry.start(conversation_id="conv-a", user_email=OTHER)
    assert seen == []


def test_snapshot_includes_recently_finished_runs(registry):
    """A browser reopened after a run finished should still see the outcome."""
    done = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(done.run_id, RunStatus.FAILED, error="boom")
    live = registry.start(conversation_id="conv-b", user_email=USER)

    snapshot = registry.snapshot_for_user(USER)
    by_id = {entry["run_id"]: entry for entry in snapshot}
    assert by_id[done.run_id]["status"] == "failed"
    assert by_id[done.run_id]["error"] == "boom"
    assert by_id[live.run_id]["status"] == "queued"
    assert by_id[live.run_id]["conversation_id"] == "conv-b"


def test_snapshot_excludes_other_users(registry):
    registry.start(conversation_id="conv-a", user_email=OTHER)
    assert registry.snapshot_for_user(USER) == []


def test_detached_run_keeps_running(registry):
    """Closing the browser marks a run detached; it does not end it."""
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.mark_detached(run.run_id)
    record = registry.get(run.run_id)
    assert record.detached is True
    assert not record.is_terminal


# ---------------------------------------------------------------------------
# Reaping and limits
# ---------------------------------------------------------------------------

def test_reap_drops_old_terminal_runs_only(registry):
    old = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(old.run_id, RunStatus.COMPLETED)
    live = registry.start(conversation_id="conv-b", user_email=USER)
    # Age the finished run past the retention window. Done after the second
    # start, because start() reaps first.
    registry.get(old.run_id).ended_at = time.time() - 10_000

    assert registry.reap_terminal() == 1
    assert registry.get(old.run_id) is None
    assert registry.get(live.run_id) is not None


@pytest.mark.asyncio
async def test_wall_clock_limit_cancels_an_overrunning_run(registry):
    async def forever():
        await asyncio.sleep(60)

    run = registry.start(conversation_id="conv-a", user_email=USER)
    task = asyncio.create_task(forever())
    registry.attach_task(run.run_id, task)
    registry.get(run.run_id).created_at = time.time() - 500

    expired = registry.enforce_wall_clock(max_seconds=100)

    assert expired == [run.run_id]
    assert registry.get(run.run_id).status == RunStatus.FAILED
    await asyncio.gather(task, return_exceptions=True)


def test_wall_clock_limit_of_zero_disables_the_check(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.get(run.run_id).created_at = time.time() - 10_000
    assert registry.enforce_wall_clock(max_seconds=0) == []
    assert registry.get(run.run_id).status == RunStatus.QUEUED


def test_registry_singleton_is_shared(registry):
    assert get_run_registry() is get_run_registry()


# ---------------------------------------------------------------------------
# Eligibility gate
# ---------------------------------------------------------------------------

BASE = dict(
    chat_history_enabled=True,
    save_mode="server",
    agent_mode=True,
    selected_tools=["tool_a"],
    conversation_id="conv-a",
)


def test_eligible_turn():
    assert turn_is_eligible_for_background_run(**BASE) is True


@pytest.mark.parametrize(
    "override",
    [
        {"chat_history_enabled": False},
        {"save_mode": "local"},
        {"save_mode": "none"},
        {"incognito": True},
        {"agent_mode": False},
        {"selected_tools": []},
        {"selected_tools": None},
        {"conversation_id": None},
        {"conversation_id": ""},
    ],
    ids=[
        "history-off",
        "local-save",
        "incognito-save",
        "incognito-flag",
        "not-agent-mode",
        "no-tools",
        "tools-none",
        "no-conversation",
        "empty-conversation",
    ],
)
def test_ineligible_turns_keep_legacy_behaviour(override):
    assert turn_is_eligible_for_background_run(**{**BASE, **override}) is False


def test_save_mode_helper_matches_transport_derivation():
    assert save_mode_is_server("server") is True
    assert save_mode_is_server("server", incognito=True) is False
    assert save_mode_is_server("local") is False
    assert save_mode_is_server(None) is True


# ---------------------------------------------------------------------------
# Pending input requests (replay after the user was elsewhere)
# ---------------------------------------------------------------------------

def test_pending_request_is_kept_for_replay(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    frame = {"type": "tool_approval_request", "tool_call_id": "t1", "arguments": {"q": "x"}}
    registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")
    registry.set_pending_request(run.run_id, frame)

    replayed = registry.pending_requests_for_conversation("conv-a", USER)

    assert replayed == [frame]


def test_pending_request_is_copied_not_aliased(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    frame = {"type": "tool_approval_request", "tool_call_id": "t1"}
    registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT)
    registry.set_pending_request(run.run_id, frame)
    frame["tool_call_id"] = "mutated"

    assert registry.pending_requests_for_conversation("conv-a", USER)[0]["tool_call_id"] == "t1"


def test_pending_request_clears_when_the_run_resumes(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT)
    registry.set_pending_request(run.run_id, {"type": "tool_approval_request"})

    registry.set_status(run.run_id, RunStatus.RUNNING)

    assert registry.pending_requests_for_conversation("conv-a", USER) == []


def test_pending_request_is_not_readable_by_another_user(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT)
    registry.set_pending_request(run.run_id, {"type": "tool_approval_request"})

    assert registry.pending_requests_for_conversation("conv-a", OTHER) == []


def test_no_pending_request_for_a_conversation_without_a_run(registry):
    assert registry.pending_requests_for_conversation("conv-nope", USER) == []
