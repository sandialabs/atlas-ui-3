"""Replaying a tracked run's open token segment on reopen (issue #957).

A client that leaves a streaming conversation drops every frame the run
emits while it is elsewhere; the run's session receives a segment only when
the segment closes. What a reopen can show for the answer's in-flight window
comes from the replay buffer these tests pin:

* the buffer tracks exactly the open segment (cleared on close, never a
  blend of two concurrent runs'),
* it is fed by the notifier -- the one chokepoint every token frame reaches
  exactly once -- keyed by the ambient run,
* the in-flight conversation record exposes it as ``streaming_text``, and
* the restore path turns it into the tagged ``token_stream`` replay frame.
"""

import pytest
from main import _stream_replay_frame

from atlas.application.chat.runs import RunRegistry, RunStatus, reset_run_registry
from atlas.application.chat.runs.in_flight import in_flight_conversation
from atlas.application.chat.runs.stream_replay import StreamReplay
from atlas.application.chat.utilities import event_notifier
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

OWNER = "owner@example.com"


# ---------------------------------------------------------------------------
# Buffer semantics
# ---------------------------------------------------------------------------

def test_buffer_accumulates_the_open_segment():
    buffer = StreamReplay()
    buffer.observe("Hello", is_first=True, is_last=False)
    buffer.observe(" world", is_first=False, is_last=False)
    assert buffer.text() == "Hello world"
    assert buffer.truncated is False


def test_buffer_resets_when_a_new_segment_opens():
    buffer = StreamReplay()
    buffer.observe("first segment", is_first=True, is_last=False)
    buffer.observe("second", is_first=True, is_last=False)
    # The closed segment is the run's history's problem now (the narration row
    # is written the moment a step ends); only the open one replays.
    assert buffer.text() == "second"


def test_buffer_clears_when_the_segment_closes():
    buffer = StreamReplay()
    buffer.observe("done text", is_first=True, is_last=False)
    buffer.observe("", is_first=False, is_last=True)
    assert buffer.text() == ""


def test_buffer_ignores_frames_after_truncating():
    buffer = StreamReplay()
    buffer.observe("x" * StreamReplay.MAX_CHARS, is_first=True, is_last=False)
    assert len(buffer.text()) == StreamReplay.MAX_CHARS
    buffer.observe("y", is_first=False, is_last=False)
    assert buffer.truncated is True
    assert len(buffer.text()) == StreamReplay.MAX_CHARS
    buffer.observe("more", is_first=False, is_last=False)
    assert len(buffer.text()) == StreamReplay.MAX_CHARS


def test_buffer_clear_is_reusable():
    buffer = StreamReplay()
    buffer.observe("x" * 10, is_first=True, is_last=False)
    buffer.clear()
    assert buffer.text() == ""
    buffer.observe("next", is_first=True, is_last=False)
    assert buffer.text() == "next"


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    reset_run_registry()
    yield RunRegistry(max_concurrent_runs_per_user=5)
    reset_run_registry()


def test_note_stream_token_feeds_the_run_record(registry):
    record = registry.start(conversation_id="conv-1", user_email="a@example.com")
    registry.note_stream_token(record.run_id, "Hel", True, False)
    registry.note_stream_token(record.run_id, "lo", False, False)
    assert record.stream.text() == "Hello"


def test_terminal_runs_stop_recording_and_lose_the_buffer():
    registry = RunRegistry()
    record = registry.start(conversation_id="conv-1", user_email="a@example.com")
    registry.note_stream_token(record.run_id, "mid-answer", True, False)
    registry.set_status(record.run_id, RunStatus.COMPLETED)

    assert record.stream.text() == ""
    registry.note_stream_token(record.run_id, "late", True, False)
    assert record.stream.text() == ""


def test_unknown_run_ids_are_ignored():
    registry = RunRegistry()
    registry.note_stream_token("no-such-run", "x", True, False)


# ---------------------------------------------------------------------------
# Notifier observation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_token_stream_records_the_ambient_run():
    # The notifier reaches the run through the process-wide registry, so this
    # test installs its run there rather than in a private instance.
    reset_run_registry()
    try:
        from atlas.application.chat.runs import get_run_registry
        from atlas.application.chat.runs.context import clear_current_run, set_current_run

        registry = get_run_registry()
        record = registry.start(conversation_id="conv-1", user_email="a@example.com")
        set_current_run(record.run_id, record.conversation_id)
        try:
            await event_notifier.notify_token_stream(
                token="Hello", is_first=True, is_last=False,
                update_callback=lambda frame: None,
            )
            await event_notifier.notify_token_stream(
                token=" there", is_first=False, is_last=False,
                update_callback=lambda frame: None,
            )
            assert record.stream.text() == "Hello there"
        finally:
            clear_current_run()
    finally:
        reset_run_registry()


@pytest.mark.asyncio
async def test_notify_token_stream_without_a_run_records_nothing(registry):
    await event_notifier.notify_token_stream(
        token="orphan", is_first=True, is_last=False,
        update_callback=lambda frame: None,
    )


@pytest.mark.asyncio
async def test_notify_token_stream_with_an_unregistered_run_still_sends():
    """A frame whose ambient run the registry has never seen still flows."""
    reset_run_registry()
    sent = []

    from atlas.application.chat.runs.context import clear_current_run, set_current_run

    set_current_run("run-unknown", "conv-x")
    try:
        await event_notifier.notify_token_stream(
            token="hi", is_first=True, is_last=False,
            update_callback=sent.append,
        )
    finally:
        clear_current_run()
    assert sent and sent[0]["type"] == "token_stream" and sent[0]["token"] == "hi"


# ---------------------------------------------------------------------------
# In-flight record
# ---------------------------------------------------------------------------

async def _running_conversation(registry, repo, *, tokens=""):
    record = registry.start(conversation_id="conv-1", user_email=OWNER, title="What is 2+2?")
    session = Session(id=record.session_id, user_email=OWNER)
    session.context["agent_mode"] = True
    session.history.messages.append(Message(role=MessageRole.USER, content="What is 2+2?"))
    await repo.create(session)
    registry.note_stream_token(record.run_id, tokens, True, False)
    return record


OWNER = "owner@example.com"
OTHER = "other@example.com"


@pytest.mark.asyncio
async def test_in_flight_record_carries_the_open_segment():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    await _running_conversation(registry, repo, tokens="Four, because")

    conv = await in_flight_conversation(repo, registry, "conv-1", OWNER)

    assert conv["streaming_text"] == "Four, because"
    assert conv["streaming_truncated"] is False


@pytest.mark.asyncio
async def test_in_flight_record_omits_streaming_when_nothing_is_open():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    await _running_conversation(registry, repo, tokens="")

    conv = await in_flight_conversation(repo, registry, "conv-1", OWNER)

    assert conv["streaming_text"] is None
    assert conv["streaming_truncated"] is False


@pytest.mark.asyncio
async def test_in_flight_record_reports_truncation():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    record = await _running_conversation(
        registry, repo, tokens="x" * StreamReplay.MAX_CHARS
    )
    record.stream.observe("y", False, False)

    conv = await in_flight_conversation(repo, registry, "conv-1", OWNER)

    assert conv["streaming_truncated"] is True
    assert len(conv["streaming_text"]) == StreamReplay.MAX_CHARS


# ---------------------------------------------------------------------------
# Restore replay frame
# ---------------------------------------------------------------------------

def test_replay_frame_carries_the_segment_and_run_identity():
    registry = RunRegistry()
    record = registry.start(conversation_id="conv-1", user_email=OWNER)
    registry.note_stream_token(record.run_id, "Hello", True, False)
    registry.note_stream_token(record.run_id, " world", False, False)

    frame = _stream_replay_frame(record)

    assert frame is not None
    assert frame["type"] == "token_stream"
    assert frame["token"] == "Hello world"
    assert frame["replay"] is True
    assert frame["is_first"] is True
    assert frame["is_last"] is False
    assert frame["run_id"] == record.run_id
    assert frame["conversation_id"] == "conv-1"


def test_replay_frame_is_none_without_a_run_or_an_open_segment():
    registry = RunRegistry()
    assert _stream_replay_frame(None) is None
    record = registry.start(conversation_id="conv-1", user_email=OWNER)
    assert _stream_replay_frame(record) is None
    registry.note_stream_token(record.run_id, "text", True, False)
    assert _stream_replay_frame(record) is not None


def test_replay_frame_is_none_once_the_run_ends():
    registry = RunRegistry()
    record = registry.start(conversation_id="conv-1", user_email=OWNER)
    registry.note_stream_token(record.run_id, "closed", True, False)
    registry.set_status(record.run_id, RunStatus.COMPLETED)

    assert _stream_replay_frame(record) is None
