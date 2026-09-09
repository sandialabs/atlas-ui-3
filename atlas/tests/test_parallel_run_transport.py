"""Transport-level routing for parallel conversation runs (issue #884).

These cover the part of the feature that lives in the WebSocket endpoint: how
an event is stamped with the run that produced it, and how a stop / approval
frame is resolved to exactly one run.
"""

import pytest

from main import _cancel_addressed_run, _resume_waiting_run, tag_run_event

from atlas.application.chat.runs import RunRegistry, RunStatus, reset_run_registry

USER = "a@example.com"
OTHER = "b@example.com"


@pytest.fixture
def registry():
    reset_run_registry()
    yield RunRegistry(max_concurrent_runs_per_user=5)
    reset_run_registry()


# ---------------------------------------------------------------------------
# Event tagging
# ---------------------------------------------------------------------------

def test_events_carry_run_and_conversation_ids():
    tagged = tag_run_event({"type": "token_stream", "token": "hi"}, "run-1", "conv-1")
    assert tagged["run_id"] == "run-1"
    assert tagged["conversation_id"] == "conv-1"
    assert tagged["token"] == "hi"


def test_tagging_does_not_mutate_the_original_event():
    original = {"type": "response_complete"}
    tag_run_event(original, "run-1", "conv-1")
    assert original == {"type": "response_complete"}


def test_producer_supplied_conversation_id_wins():
    """A producer that knows its own conversation is more authoritative."""
    tagged = tag_run_event(
        {"type": "canvas_content", "conversation_id": "conv-real"}, "run-1", "conv-envelope"
    )
    assert tagged["conversation_id"] == "conv-real"


def test_non_dict_event_passes_through():
    assert tag_run_event("not-a-dict", "run-1", "conv-1") == "not-a-dict"


# ---------------------------------------------------------------------------
# Stop routing
# ---------------------------------------------------------------------------

def test_stop_by_run_id_cancels_only_that_run(registry):
    run_a = registry.start(conversation_id="conv-a", user_email=USER)
    run_b = registry.start(conversation_id="conv-b", user_email=USER)

    assert _cancel_addressed_run(registry, USER, {"run_id": run_a.run_id}) is False
    # No task attached, so nothing was *cancelled*, but the record is terminal
    # and B is untouched -- the isolation criterion.
    assert registry.get(run_a.run_id).status == RunStatus.CANCELLED
    assert registry.get(run_b.run_id).status != RunStatus.CANCELLED


def test_stop_by_conversation_id_resolves_the_run(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    _cancel_addressed_run(registry, USER, {"conversation_id": "conv-a"})
    assert registry.get(run.run_id).status == RunStatus.CANCELLED


def test_stop_frame_without_ids_falls_back_to_the_legacy_slot(registry):
    registry.start(conversation_id="conv-a", user_email=USER)
    assert _cancel_addressed_run(registry, USER, {}) is False


def test_stop_cannot_reach_another_users_run(registry):
    run = registry.start(conversation_id="conv-a", user_email=OTHER)
    _cancel_addressed_run(registry, USER, {"run_id": run.run_id})
    assert registry.get(run.run_id).status != RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# Approval resume routing
# ---------------------------------------------------------------------------

def test_approval_response_resumes_the_named_run(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")

    _resume_waiting_run(registry, USER, {"run_id": run.run_id})

    assert registry.get(run.run_id).status == RunStatus.RUNNING


def test_approval_response_without_ids_resumes_the_single_waiting_run(registry):
    """Older clients and MCP elicitations do not name a run."""
    running = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(running.run_id, RunStatus.RUNNING)
    waiting = registry.start(conversation_id="conv-b", user_email=USER)
    registry.set_status(waiting.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")

    _resume_waiting_run(registry, USER, {})

    assert registry.get(waiting.run_id).status == RunStatus.RUNNING


def test_ambiguous_approval_response_changes_nothing(registry):
    """Two paused runs and no id: guessing would clear the wrong indicator."""
    first = registry.start(conversation_id="conv-a", user_email=USER)
    second = registry.start(conversation_id="conv-b", user_email=USER)
    for record in (first, second):
        registry.set_status(record.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")

    _resume_waiting_run(registry, USER, {})

    assert registry.get(first.run_id).status == RunStatus.WAITING_FOR_INPUT
    assert registry.get(second.run_id).status == RunStatus.WAITING_FOR_INPUT


def test_resume_does_not_disturb_a_run_that_is_not_waiting(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.RUNNING)
    _resume_waiting_run(registry, USER, {"run_id": run.run_id})
    assert registry.get(run.run_id).status == RunStatus.RUNNING


def test_tool_settled_events_are_recognised():
    """A settled tool clears a stale waiting_for_input status.

    Without this the run keeps working after an approval times out but stays
    marked "Needs approval" in the conversation list for the rest of its life.
    """
    from main import _TOOL_SETTLED_EVENTS

    assert {"tool_complete", "tool_error", "tool_interrupted"} <= _TOOL_SETTLED_EVENTS
    assert "tool_approval_request" not in _TOOL_SETTLED_EVENTS


# ---------------------------------------------------------------------------
# Ambient run identity (tagging events the shared publisher emits)
# ---------------------------------------------------------------------------

def test_frames_are_stamped_with_the_running_run():
    """The agent loop publishes through a connection-scoped publisher, so the
    run identity has to travel out of band or its output is indistinguishable
    from another conversation's."""
    from atlas.application.chat.runs.context import (
        clear_current_run,
        set_current_run,
        stamp_with_current_run,
    )

    clear_current_run()
    assert stamp_with_current_run({"type": "token_stream"}) == {"type": "token_stream"}

    set_current_run("run-1", "conv-1")
    try:
        stamped = stamp_with_current_run({"type": "token_stream", "token": "hi"})
        assert stamped["run_id"] == "run-1"
        assert stamped["conversation_id"] == "conv-1"

        # A producer that already knows its conversation keeps it.
        kept = stamp_with_current_run({"type": "canvas_content", "conversation_id": "conv-real"})
        assert kept["conversation_id"] == "conv-real"
    finally:
        clear_current_run()


@pytest.mark.asyncio
async def test_run_identity_does_not_leak_between_tasks():
    """Each run's task gets its own context, which is the whole reason a
    context variable is safe here."""
    import asyncio

    from atlas.application.chat.runs.context import (
        clear_current_run,
        get_current_run,
        set_current_run,
    )

    clear_current_run()
    seen = {}

    async def run_task(run_id, conversation_id):
        set_current_run(run_id, conversation_id)
        await asyncio.sleep(0)
        seen[run_id] = get_current_run()

    await asyncio.gather(run_task("run-a", "conv-a"), run_task("run-b", "conv-b"))

    assert seen["run-a"].conversation_id == "conv-a"
    assert seen["run-b"].conversation_id == "conv-b"
    # The parent context is untouched by either task.
    assert get_current_run() is None
