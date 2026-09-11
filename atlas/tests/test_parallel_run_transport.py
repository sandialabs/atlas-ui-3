"""Transport-level routing for parallel conversation runs (issue #884).

These cover the part of the feature that lives in the WebSocket endpoint: how
an event is stamped with the run that produced it, and how a stop / approval
frame is resolved to exactly one run.
"""

import copy

import pytest
from main import (
    _cancel_addressed_run,
    _download_session_candidates,
    _resume_waiting_run,
    _seed_run_session_files,
    tag_run_event,
)

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

    # The frame addressed a run, so the transport must NOT fall back to the
    # connection's untracked task -- even though no task was attached here and
    # so nothing was actually cancelled.
    assert _cancel_addressed_run(registry, USER, {"run_id": run_a.run_id}) is True
    assert registry.get(run_a.run_id).status == RunStatus.CANCELLED
    assert registry.get(run_b.run_id).status != RunStatus.CANCELLED


def test_stale_stop_frame_does_not_fall_back_to_the_legacy_slot(registry):
    """A stop frame naming an already-terminal run must not cancel something else.

    `cancel()` reports False for a run that has already finished, which is
    indistinguishable from "no run named" if the return value is taken to mean
    "a cancel happened". Taking it to mean "the frame addressed a run" keeps the
    untracked fallback reserved for clients that name nothing, so a stale stop
    cannot reach an unrelated turn in flight on this socket.
    """
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.COMPLETED)

    assert _cancel_addressed_run(registry, USER, {"run_id": run.run_id}) is True


def test_stop_frame_naming_an_unknown_run_does_not_fall_back(registry):
    """Reaped or foreign run ids are addressed, not absent."""
    assert _cancel_addressed_run(registry, USER, {"run_id": "no-such-run"}) is True


def test_stop_frame_with_only_an_unknown_conversation_falls_back(registry):
    """Nothing resolvable and no run id named: the legacy slot is the target."""
    assert (
        _cancel_addressed_run(registry, USER, {"conversation_id": "conv-unknown"})
        is False
    )


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


def test_publisher_hot_path_stamps_in_place_without_copying():
    """The two tagging call sites were collapsed into one rule (issue #915),
    but the publisher path must keep stamping in place: it builds each frame
    fresh per send, and copying every token event would be pure waste."""
    from atlas.application.chat.runs.context import (
        clear_current_run,
        set_current_run,
        stamp_with_current_run,
    )

    set_current_run("run-1", "conv-1")
    try:
        frame = {"type": "token_stream", "token": "hi"}
        stamped = stamp_with_current_run(frame)
        assert stamped is frame
        assert frame["run_id"] == "run-1"
        assert frame["conversation_id"] == "conv-1"
    finally:
        clear_current_run()


@pytest.mark.parametrize(
    "event",
    [
        {"type": "token_stream", "token": "hi"},
        # A producer that already named its own conversation, and one that
        # named both ids: existing values must survive either entry point.
        {"type": "canvas_content", "conversation_id": "conv-real"},
        {"type": "chat_response", "run_id": "run-real", "conversation_id": "conv-real"},
        # Not a dict: nowhere to put the ids, so it passes straight through.
        "not-a-dict",
        None,
    ],
)
def test_both_tagging_entry_points_share_one_rule(event):
    """One function implements the stamping rule; the other delegates.

    Parametrised across the shapes the rule actually distinguishes, so a
    divergence between the two entry points cannot hide in an input the single
    happy-path case never reaches.
    """
    from atlas.application.chat.runs.context import (
        clear_current_run,
        set_current_run,
        stamp_with_current_run,
        tag_event,
    )

    expected = tag_event(copy.deepcopy(event), "run-1", "conv-1")

    assert tag_run_event(copy.deepcopy(event), "run-1", "conv-1") == expected

    set_current_run("run-1", "conv-1")
    try:
        assert stamp_with_current_run(copy.deepcopy(event)) == expected
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


# ---------------------------------------------------------------------------
# Download routing
# ---------------------------------------------------------------------------

def test_download_searches_the_connection_session_first(registry):
    """The common case must stay the first (and usually only) lookup."""
    candidates = _download_session_candidates(registry, "conn-session", USER, {})
    assert candidates[0] == "conn-session"


def test_download_reaches_the_named_runs_session(registry):
    """A file a background run produced lives on that run's own session.

    Searching only the connection session is what made every download of a
    background run's output fail with "File not found in session".
    """
    run = registry.start(conversation_id="conv-a", user_email=USER)

    candidates = _download_session_candidates(
        registry, "conn-session", USER, {"run_id": run.run_id}
    )

    assert run.session_id in candidates


def test_download_reaches_a_finished_runs_session(registry):
    """A file is usually fetched just after the run that produced it ended."""
    run = registry.start(conversation_id="conv-a", user_email=USER)
    registry.set_status(run.run_id, RunStatus.COMPLETED)

    candidates = _download_session_candidates(registry, "conn-session", USER, {})

    assert run.session_id in candidates


def test_download_never_reaches_another_users_run(registry):
    """Widening the search must not widen access."""
    foreign = registry.start(conversation_id="conv-x", user_email=OTHER)

    candidates = _download_session_candidates(
        registry, "conn-session", USER, {"run_id": foreign.run_id}
    )

    assert foreign.session_id not in candidates


def test_download_candidates_are_deduplicated(registry):
    run = registry.start(conversation_id="conv-a", user_email=USER)

    candidates = _download_session_candidates(
        registry,
        "conn-session",
        USER,
        {"run_id": run.run_id, "conversation_id": "conv-a"},
    )

    assert len(candidates) == len(set(candidates))


# ---------------------------------------------------------------------------
# Attached files reaching a tracked run
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, context=None):
        self.context = context if context is not None else {}


class _FakeSessionRepo:
    """Mirrors the real repository's by-reference storage."""

    def __init__(self, sessions):
        self._sessions = sessions

    async def get(self, session_id):
        return self._sessions.get(session_id)


class _FakeChatService:
    def __init__(self, sessions):
        self.session_repository = _FakeSessionRepo(sessions)
        self.created = []

    async def create_session(self, session_id, user_email=None):
        session = _FakeSession()
        self.session_repository._sessions[session_id] = session
        self.created.append(session_id)
        return session


@pytest.mark.asyncio
async def test_attached_files_are_copied_onto_the_run_session():
    """A file attached just before the turn must be visible to the run.

    `attach_file` writes to the connection session; the run executes against
    its own, which would otherwise start empty.
    """
    attached = {"report.csv": {"key": "s3/report.csv"}}
    sessions = {"conn": _FakeSession({"files": attached})}
    service = _FakeChatService(sessions)

    await _seed_run_session_files(service, "conn", "run-session", USER)

    assert sessions["run-session"].context["files"] == attached


@pytest.mark.asyncio
async def test_seeding_copies_rather_than_shares_the_file_map():
    """The run and the connection must not mutate each other's state."""
    sessions = {"conn": _FakeSession({"files": {"a.txt": {"key": "s3/a"}}})}
    service = _FakeChatService(sessions)

    await _seed_run_session_files(service, "conn", "run-session", USER)
    sessions["run-session"].context["files"]["b.txt"] = {"key": "s3/b"}

    assert "b.txt" not in sessions["conn"].context["files"]


@pytest.mark.asyncio
async def test_seeding_is_a_noop_for_an_untracked_turn():
    """An untracked turn already runs on the connection session."""
    sessions = {"conn": _FakeSession({"files": {"a.txt": {"key": "s3/a"}}})}
    service = _FakeChatService(sessions)

    await _seed_run_session_files(service, "conn", "conn", USER)

    assert service.created == []


@pytest.mark.asyncio
async def test_seeding_failure_does_not_stop_the_run():
    """Cleanup-style helper: a missing file map must not abort admission."""

    class _Exploding(_FakeChatService):
        async def create_session(self, session_id, user_email=None):
            raise RuntimeError("hook denied")

    service = _Exploding({"conn": _FakeSession({"files": {"a.txt": {}}})})

    await _seed_run_session_files(service, "conn", "run-session", USER)
