"""Transport-level routing for parallel conversation runs (issue #884).

These cover the part of the feature that lives in the WebSocket endpoint: how
an event is stamped with the run that produced it, and how a stop / approval
frame is resolved to exactly one run.
"""

import copy

import pytest
from main import (
    _cancel_addressed_run,
    _DOWNLOAD_ERROR_PRIORITY,
    _download_error_rank,
    _download_session_candidates,
    _MERGED_FROM_RUN,
    _merge_run_session_files,
    _release_finished_run,
    _resolve_download,
    _resume_waiting_run,
    _seed_run_session_files,
    tag_run_event,
)

from atlas.application.chat.service import DownloadError
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
    tagged = tag_run_event(original, "run-1", "conv-1")
    assert tagged is not original
    assert original == {"type": "response_complete"}


def test_producer_supplied_conversation_id_wins(caplog):
    """A producer that knows its own conversation is more authoritative."""
    caplog.set_level("DEBUG")
    tagged = tag_run_event(
        {"type": "canvas_content", "conversation_id": "conv-real"}, "run-1", "conv-envelope"
    )
    assert tagged["conversation_id"] == "conv-real"
    assert "overrides ambient" in caplog.text


def test_producer_supplied_run_id_mismatch_is_logged(caplog):
    caplog.set_level("DEBUG")
    tag_run_event(
        {"type": "token_stream", "run_id": "run-real"}, "run-envelope", "conv-1"
    )
    assert "run_id" in caplog.text


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
    "event,expected",
    [
        # A bare event gets both ids.
        (
            {"type": "token_stream", "token": "hi"},
            {
                "type": "token_stream",
                "token": "hi",
                "run_id": "run-1",
                "conversation_id": "conv-1",
            },
        ),
        # A producer that already named its own conversation keeps it, and
        # still picks up the run id.
        (
            {"type": "canvas_content", "conversation_id": "conv-real"},
            {
                "type": "canvas_content",
                "conversation_id": "conv-real",
                "run_id": "run-1",
            },
        ),
        # Both ids pre-set: nothing is touched.
        (
            {"type": "chat_response", "run_id": "run-real", "conversation_id": "conv-real"},
            {"type": "chat_response", "run_id": "run-real", "conversation_id": "conv-real"},
        ),
        # Not a dict: nowhere to put the ids, so it passes straight through.
        ("not-a-dict", "not-a-dict"),
        (None, None),
    ],
)
def test_both_tagging_entry_points_share_one_rule(event, expected):
    """One function implements the stamping rule; the other delegates.

    The expected values are written out literally rather than derived from
    ``tag_event``: an oracle computed by the very function under test would
    move in lockstep with a regression inside it (``setdefault`` quietly
    becoming assignment, say) and the two entry points would still agree.

    Parametrised across the shapes the rule actually distinguishes, so a
    divergence between the entry points cannot hide in an input a single
    happy-path case never reaches.
    """
    from atlas.application.chat.runs.context import (
        clear_current_run,
        set_current_run,
        stamp_with_current_run,
        tag_event,
    )

    assert tag_event(copy.deepcopy(event), "run-1", "conv-1") == expected
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


# ---------------------------------------------------------------------------
# Files produced by a finished run (issue #953)
# ---------------------------------------------------------------------------


def _without_provenance(files):
    """The file map as it would read without the merge's provenance stamp."""
    return {
        name: {k: v for k, v in meta.items() if k != _MERGED_FROM_RUN}
        if isinstance(meta, dict) else meta
        for name, meta in files.items()
    }


@pytest.mark.asyncio
async def test_run_artifacts_are_merged_back_to_the_connection():
    """A tool artifact must stay downloadable after its run's session goes."""
    produced = {"mcp_image_0.jpeg": {"key": "s3/img"}}
    sessions = {
        "conn": _FakeSession({"files": {}}),
        "run-session": _FakeSession({"files": produced}),
    }
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")

    merged = _without_provenance(sessions["conn"].context["files"])
    assert merged["mcp_image_0.jpeg"] == {"key": "s3/img"}


@pytest.mark.asyncio
async def test_a_name_collision_keeps_both_files():
    """Two different files wearing one label: neither may be dropped.

    The connection's entry is the live one for what the user attached, so it
    keeps the plain name -- but discarding the run's entry would lose its
    storage key, and with it the only way to fetch bytes that do exist.
    """
    sessions = {
        "conn": _FakeSession({"files": {"a.txt": {"key": "s3/conn-a"}}}),
        "run-session": _FakeSession({"files": {"a.txt": {"key": "s3/run-a"}}}),
    }
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")

    merged = _without_provenance(sessions["conn"].context["files"])
    # The suffixed key is the one the artifact ingest path produces, so it
    # survives ``sanitize_filename`` like any other session key.
    assert merged == {
        "a.txt": {"key": "s3/conn-a"},
        "a_1.txt": {"key": "s3/run-a"},
    }


@pytest.mark.asyncio
async def test_seeded_files_do_not_accumulate_across_turns():
    """The seed/merge round trip must be a no-op, turn after turn.

    Every tracked turn copies the connection's files into the run session and
    merges them back. Treating the returning copy as a new file would add one
    phantom entry per file per turn, and then copy those forward too.
    """
    attached = {"report.csv": {"key": "s3/report"}, "notes.txt": {}}
    sessions = {"conn": _FakeSession({"files": dict(attached)})}
    service = _FakeChatService(sessions)

    for turn in range(3):
        run_session_id = f"run-{turn}"
        await _seed_run_session_files(service, "conn", run_session_id, USER)
        await _merge_run_session_files(service, run_session_id, "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == attached


@pytest.mark.asyncio
async def test_a_file_filed_under_a_suffix_is_refreshed_not_recopied():
    """A run re-emitting its own artifact updates the entry it already has."""
    sessions = {
        "conn": _FakeSession({"files": {"a.txt": {"key": "s3/conn-a"}}}),
        "run-session": _FakeSession({"files": {"a.txt": {"key": "s3/run-a"}}}),
    }
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")
    await _merge_run_session_files(service, "run-session", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "a.txt": {"key": "s3/conn-a"},
        "a_1.txt": {"key": "s3/run-a"},
    }


@pytest.mark.asyncio
async def test_merging_when_the_connection_session_is_gone_is_logged(caplog):
    """A detached run outliving its socket must not lose files silently."""
    sessions = {
        "run-session": _FakeSession({"files": {"out.png": {"key": "s3/out"}}}),
    }
    service = _FakeChatService(sessions)

    with caplog.at_level("WARNING"):
        await _merge_run_session_files(service, "run-session", "conn")

    assert "File Library" in caplog.text


@pytest.mark.asyncio
async def test_an_inactive_connection_session_still_receives_the_files():
    """New Chat ends and re-creates the session under the same id.

    A run releasing in that window is on a live connection, so skipping the
    merge would drop artifacts the user can still see on screen. Merging into
    a session nobody reads costs nothing, so merge either way.
    """
    sessions = {
        "conn": _FakeSession({"files": {}}),
        "run-session": _FakeSession({"files": {"out.png": {"key": "s3/out"}}}),
    }
    sessions["conn"].active = False
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "out.png": {"key": "s3/out"}
    }


@pytest.mark.asyncio
async def test_merging_is_a_noop_for_an_untracked_turn():
    sessions = {"conn": _FakeSession({"files": {"a.txt": {"key": "s3/a"}}})}
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "conn", "conn")

    assert sessions["conn"].context["files"] == {"a.txt": {"key": "s3/a"}}


@pytest.mark.asyncio
async def test_merge_failure_does_not_break_release():
    class _Exploding(_FakeSessionRepo):
        async def get(self, session_id):
            raise RuntimeError("repo down")

    service = _FakeChatService({})
    service.session_repository = _Exploding({})

    await _merge_run_session_files(service, "run-session", "conn")


@pytest.mark.asyncio
async def test_release_merges_files_before_deleting_the_run_session(registry):
    """The merge has to happen while the run's session still exists."""
    seen = {}

    class _Repo(_FakeSessionRepo):
        async def delete(self, session_id):
            seen["files_at_delete"] = dict(
                self._sessions["conn"].context.get("files", {})
            )
            self._sessions.pop(session_id, None)

    sessions = {
        "conn": _FakeSession({"files": {}}),
        "run-session": _FakeSession({"files": {"out.png": {"key": "s3/out"}}}),
    }
    service = _FakeChatService(sessions)
    service.session_repository = _Repo(sessions)
    ended = []
    service.end_session = lambda sid: _noop(ended, sid)

    await _release_finished_run(
        service,
        registry,
        "run-1",
        "run-session",
        None,
        USER,
        connection_session_id="conn",
    )

    assert ended == ["run-session"]
    assert "run-session" not in sessions
    assert _without_provenance(seen["files_at_delete"]) == {"out.png": {"key": "s3/out"}}


async def _noop(sink, session_id):
    sink.append(session_id)


# ---------------------------------------------------------------------------
# Picking which candidate session's answer the user sees (issue #953)
# ---------------------------------------------------------------------------

class _FakeDownloads:
    """Replays a scripted reply per candidate session, in order."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def handle_download_file(self, session_id, filename, user_email, s3_key=None):
        self.calls.append(session_id)
        return self._replies.pop(0)


def _fail(code, message):
    return {"error": message, "error_code": code}


@pytest.mark.asyncio
async def test_a_dead_run_session_does_not_mask_file_not_found():
    """The exact reversal reported in #953: the useful error must win."""
    service = _FakeDownloads([
        _fail(DownloadError.NOT_FOUND.value, "File not found in session"),
        _fail(DownloadError.NO_SESSION.value, "Session or file manager not available"),
    ])

    response = await _resolve_download(service, ["conn", "run"], "img.jpeg", USER, None)

    assert response["error"] == "File not found in session"


@pytest.mark.asyncio
async def test_a_later_success_wins_and_stops_the_search():
    service = _FakeDownloads([
        _fail(DownloadError.NOT_FOUND.value, "File not found in session"),
        {"content_base64": "aGk="},
        _fail(DownloadError.NO_SESSION.value, "never reached"),
    ])

    response = await _resolve_download(
        service, ["conn", "run", "other"], "img.jpeg", USER, None
    )

    assert response == {"content_base64": "aGk="}
    assert service.calls == ["conn", "run"]


@pytest.mark.asyncio
async def test_an_unmapped_error_does_not_outrank_file_not_found():
    """An unclassified failure elsewhere says less than an accurate answer."""
    service = _FakeDownloads([
        _fail(DownloadError.NOT_FOUND.value, "File not found in session"),
        {"error": "something odd"},
    ])

    response = await _resolve_download(service, ["conn", "run"], "img.jpeg", USER, None)

    assert response["error"] == "File not found in session"


def test_error_ranking_prefers_the_most_informative_failure():
    ranks = [
        _download_error_rank(_fail(code, "x"))
        for code in (
            DownloadError.NOT_FOUND.value,
            DownloadError.STORAGE.value,
            DownloadError.NO_SESSION.value,
        )
    ]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 3


@pytest.mark.asyncio
async def test_an_unmapped_error_loses_to_file_not_found_in_either_order():
    """Which reply wins must not depend on the order candidates are tried."""
    for replies in (
        [_fail(DownloadError.NOT_FOUND.value, "File not found in session"),
         {"error": "something odd"}],
        [{"error": "something odd"},
         _fail(DownloadError.NOT_FOUND.value, "File not found in session")],
    ):
        service = _FakeDownloads(replies)
        response = await _resolve_download(
            service, ["conn", "run"], "img.jpeg", USER, None
        )
        assert response["error"] == "File not found in session"


@pytest.mark.asyncio
async def test_an_unmapped_error_beats_a_missing_session_in_either_order():
    for replies in (
        [{"error": "something odd"},
         _fail(DownloadError.NO_SESSION.value, "Session or file manager not available")],
        [_fail(DownloadError.NO_SESSION.value, "Session or file manager not available"),
         {"error": "something odd"}],
    ):
        service = _FakeDownloads(replies)
        response = await _resolve_download(
            service, ["conn", "run"], "img.jpeg", USER, None
        )
        assert response["error"] == "something odd"


@pytest.mark.asyncio
async def test_a_run_artifact_does_not_displace_an_attachment_of_the_same_name():
    """Matching advertised names alone do not make two files one file.

    The identity rule that lets a re-emitted artifact converge is scoped to
    entries the merge itself wrote. A user's attachment carries no such mark,
    so a run artifact advertising the same name is a *different* file: the
    attachment keeps its name and its storage ref, and the artifact is filed
    beside it.
    """
    attached = {"key": "s3/attached", "original_filename": "a.txt"}
    produced = {"key": "s3/produced", "original_filename": "a.txt"}
    sessions = {
        "conn": _FakeSession({"files": {"a.txt": dict(attached)}}),
        "run-session": _FakeSession({"files": {"a.txt": dict(produced)}}),
    }
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "a.txt": attached,
        "a_1.txt": produced,
    }


@pytest.mark.asyncio
async def test_an_artifact_never_holds_both_a_plain_and_a_suffixed_key():
    """One file under two names makes ``_resolve_session_file`` call it missing.

    A run whose artifact was suffixed on an earlier turn (because an
    attachment held the plain name) must keep refreshing that suffixed entry
    even after the attachment is gone and the plain name is free again.
    """
    attached = {"key": "s3/attached", "original_filename": "a.txt"}
    produced = {"key": "s3/produced", "original_filename": "a.txt"}
    sessions = {
        "conn": _FakeSession({"files": {"a.txt": dict(attached)}}),
        "run-session": _FakeSession({"files": {"a.txt": dict(produced)}}),
    }
    service = _FakeChatService(sessions)

    await _merge_run_session_files(service, "run-session", "conn")
    del sessions["conn"].context["files"]["a.txt"]
    await _merge_run_session_files(service, "run-session", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "a_1.txt": produced
    }


@pytest.mark.asyncio
async def test_a_round_trip_does_not_mark_attachments_as_run_output():
    """The stamp must not spread to the files the connection already owned.

    Every file the connection owns is seeded into each run and merged back. If
    that round trip stamped them, a user's attachment would join advertised-name
    matching and the next same-named artifact would take over its slot.
    """
    attached = {"key": "s3/attached", "original_filename": "a.txt"}
    produced = {"key": "s3/produced", "original_filename": "a.txt"}
    sessions = {"conn": _FakeSession({"files": {"a.txt": dict(attached)}})}
    service = _FakeChatService(sessions)

    # One ordinary turn: seed the attachment into a run, merge it back.
    await _seed_run_session_files(service, "conn", "run-0", USER)
    await _merge_run_session_files(service, "run-0", "conn")

    # Now a run produces a *different* file advertising the same name.
    sessions["run-1"] = _FakeSession({"files": {"a.txt": dict(produced)}})
    await _merge_run_session_files(service, "run-1", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "a.txt": attached,
        "a_1.txt": produced,
    }


@pytest.mark.asyncio
async def test_a_superseded_key_does_not_swallow_a_later_artifact():
    """A replaced entry's old key must leave the index with it.

    A re-emitted artifact is refreshed to a new storage key. If the old key
    stayed indexed against that name, a *different* file later in the same run
    map that happens to carry the old key would look like the name just
    updated, overwrite it, and lose its own name into the bargain.
    """
    sessions = {
        "conn": _FakeSession({"files": {}}),
        "turn-1": _FakeSession({
            "files": {"a.txt": {"key": "s3/old", "original_filename": "a.txt"}}
        }),
    }
    service = _FakeChatService(sessions)
    # Turn one: the run produces a.txt, so the entry carries the merge's stamp.
    await _merge_run_session_files(service, "turn-1", "conn")

    # Turn two re-emits a.txt under a fresh key -- retiring ``s3/old`` -- and
    # produces a second file that reuses it.
    sessions["turn-2"] = _FakeSession({
        "files": {
            "a.txt": {"key": "s3/new", "original_filename": "a.txt"},
            "b.txt": {"key": "s3/old"},
        }
    })

    await _merge_run_session_files(service, "turn-2", "conn")

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "a.txt": {"key": "s3/new", "original_filename": "a.txt"},
        "b.txt": {"key": "s3/old"},
    }


@pytest.mark.asyncio
async def test_files_are_not_merged_into_another_conversation():
    """A reset that completes *before* the merge starts must still be honoured.

    Gating only the replay leaves this window open: artifacts from the run's
    conversation would land in the one now on screen, where the model and its
    tools would see them and the next run would be seeded with them.
    """
    sessions = {
        "conn": _FakeSession({"files": {}}),
        "run-session": _FakeSession({"files": {"out.png": {"key": "s3/out"}}}),
    }
    sessions["conn"].context["conversation_id"] = "conversation-B"
    service = _FakeChatService(sessions)

    await _merge_run_session_files(
        service, "run-session", "conn", "conversation-A"
    )

    assert sessions["conn"].context["files"] == {}


@pytest.mark.asyncio
async def test_files_are_merged_when_the_conversation_still_matches():
    sessions = {
        "conn": _FakeSession({"files": {}}),
        "run-session": _FakeSession({"files": {"out.png": {"key": "s3/out"}}}),
    }
    sessions["conn"].context["conversation_id"] = "conversation-A"
    service = _FakeChatService(sessions)

    await _merge_run_session_files(
        service, "run-session", "conn", "conversation-A"
    )

    assert _without_provenance(sessions["conn"].context["files"]) == {
        "out.png": {"key": "s3/out"}
    }


def test_every_download_error_code_has_an_explicit_rank():
    """A new DownloadError member must not silently fall to unknown rank."""
    ranked = {code for code in _DOWNLOAD_ERROR_PRIORITY if code is not None}
    assert ranked == {member.value for member in DownloadError}
