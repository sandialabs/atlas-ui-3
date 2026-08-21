"""Conversation persistence must not depend on what the client is holding.

A WebSocket gets a fresh ``session_id`` on every connection, but the browser's
``activeConversationId`` outlives the socket (it is React state in a provider
that does not remount on reconnect). So after a temporary disconnect the next
turn arrives naming a conversation the server has no history for.

``save_conversation`` replaces the whole message set, so that turn used to write
its two messages over the entire stored conversation. These tests pin the two
defences:

1. The server rehydrates the session from the store before running the turn, so
   the save is complete regardless of what the client did or did not restore.
2. The repository refuses a write that would shrink a conversation, unless the
   caller is a rewind/edit-and-resubmit that legitimately shortens it.
"""

import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.service import ChatService
from atlas.application.chat.utilities.conversation_loader import (
    load_messages_into_history,
)
from atlas.domain.messages.models import ConversationHistory
from atlas.domain.sessions.models import Session
from atlas.modules.chat_history.conversation_repository import ConversationRepository
from atlas.modules.chat_history.database import (
    get_session_factory,
    init_database,
    reset_engine,
)
from atlas.modules.config.config_manager import config_manager

USER = "alice@test.com"


# --- repository: the no-shrink guard -----------------------------------------


@pytest.fixture
def repo(tmp_path):
    """A repository backed by a throwaway DuckDB file.

    The engine is a module-level singleton, so it has to be reset on both
    sides: without the reset *before* init, this fixture silently reuses
    whichever database an earlier test opened.
    """
    reset_engine()
    init_database(f"duckdb:///{tmp_path / 'history.db'}")
    try:
        yield ConversationRepository(get_session_factory())
    finally:
        reset_engine()


def _messages(count, prefix="m"):
    return [
        {
            "id": f"{prefix}{i}",
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i}",
            "message_type": "chat",
        }
        for i in range(count)
    ]


def _save(repo, messages, **kwargs):
    return repo.save_conversation(
        conversation_id="conv-1",
        user_email=USER,
        title=kwargs.pop("title", "A long conversation"),
        model="test-model",
        messages=messages,
        **kwargs,
    )


def test_shorter_write_is_refused_and_leaves_the_conversation_intact(repo):
    """The reconnect case: an empty session must not overwrite 50 messages."""
    _save(repo, _messages(50))

    rejected = _save(repo, _messages(2, prefix="new"), title="what were we doing?")

    assert rejected is None
    stored = repo.get_conversation("conv-1", USER)
    assert len(stored["messages"]) == 50
    assert stored["message_count"] == 50
    # The title is replaced in the same statement as the messages, so a
    # successful truncation would have erased the evidence too.
    assert stored["title"] == "A long conversation"


def test_shorter_write_is_allowed_for_a_rewind(repo):
    """Edit-and-resubmit legitimately drops a prompt and everything after it."""
    _save(repo, _messages(50))

    saved = _save(repo, _messages(10, prefix="rewound"), allow_shrink=True)

    assert saved is not None
    stored = repo.get_conversation("conv-1", USER)
    assert len(stored["messages"]) == 10


def test_same_length_and_growing_writes_are_unaffected(repo):
    """The guard must not interfere with the ordinary append-a-turn save."""
    _save(repo, _messages(10))

    assert _save(repo, _messages(10)) is not None
    assert _save(repo, _messages(12)) is not None

    stored = repo.get_conversation("conv-1", USER)
    assert len(stored["messages"]) == 12


def test_guard_does_not_block_the_first_save(repo):
    """Nothing is stored yet, so there is nothing to shrink."""
    assert _save(repo, _messages(3)) is not None
    assert len(repo.get_conversation("conv-1", USER)["messages"]) == 3


def test_empty_write_cannot_wipe_a_conversation(repo):
    _save(repo, _messages(5))

    assert _save(repo, []) is None
    assert len(repo.get_conversation("conv-1", USER)["messages"]) == 5


# --- loader ------------------------------------------------------------------


def test_loader_preserves_original_timestamps():
    """Rebuilding without the timestamp restamps every message with 'now'.

    Because a later save rewrites the whole row set, that restamped value is
    what gets persisted -- so each load/save cycle destroyed the real times.
    """
    history = ConversationHistory()
    original = datetime(2026, 8, 15, 12, 30, tzinfo=timezone.utc)

    loaded = load_messages_into_history(
        history,
        [{"role": "user", "content": "hi", "timestamp": original.isoformat()}],
        "conv-1",
    )

    assert loaded == 1
    assert history.messages[0].timestamp == original


def test_loader_reads_a_trailing_z_timestamp():
    """Browsers write RFC3339 with a Z: Date.prototype.toISOString().

    Python 3.11 taught ``datetime.fromisoformat`` to read it (the project
    requires >= 3.11), and a locally autosaved conversation restored into a
    session arrives in exactly that shape -- silently restamping those to
    "now" would be the timestamp bug this loader exists to avoid.
    """
    history = ConversationHistory()

    load_messages_into_history(
        history, [{"role": "user", "content": "hi", "timestamp": "2026-08-20T12:00:00Z"}], "c"
    )

    assert history.messages[0].timestamp == datetime(
        2026, 8, 20, 12, 0, tzinfo=timezone.utc
    )


def test_loader_falls_back_to_now_for_an_unusable_timestamp():
    history = ConversationHistory()

    load_messages_into_history(
        history, [{"role": "user", "content": "hi", "timestamp": "not-a-date"}], "c"
    )

    assert history.messages[0].timestamp is not None


def test_loader_folds_a_top_level_message_type_into_metadata():
    """Display-only rows must stay excluded from the LLM view after a load."""
    history = ConversationHistory()

    load_messages_into_history(
        history,
        [
            {"role": "user", "content": "hi", "message_type": "chat"},
            {"role": "tool", "content": "{}", "message_type": "tool_call"},
        ],
        "conv-1",
    )

    assert history.messages[1].metadata["message_type"] == "tool_call"
    assert [m["content"] for m in history.get_messages_for_llm()] == ["hi"]


def test_loader_skips_an_unreadable_role_without_dropping_the_rest():
    history = ConversationHistory()

    loaded = load_messages_into_history(
        history,
        [
            {"role": "user", "content": "first"},
            {"role": "wizard", "content": "bogus"},
            {"role": "assistant", "content": "last"},
        ],
        "conv-1",
    )

    assert loaded == 2
    assert [m.content for m in history.messages] == ["first", "last"]


# --- service: rehydrate on a turn the client did not restore -----------------


class _FakeRepository:
    """Just enough repository for the hydration path."""

    def __init__(self, conversations=None):
        self.conversations = conversations or {}
        self.get_conversation_calls = []

    def get_conversation_owner(self, conversation_id):
        conv = self.conversations.get(conversation_id)
        return conv["user_email"] if conv else None

    def get_conversation(self, conversation_id, user_email):
        self.get_conversation_calls.append(conversation_id)
        conv = self.conversations.get(conversation_id)
        if not conv or conv["user_email"] != user_email:
            return None
        return conv


def _make_service(repository=None):
    sessions = {}

    async def _get(session_id):
        return sessions.get(session_id)

    async def _create(session):
        sessions[session.id] = session

    async def _update(session):
        sessions[session.id] = session

    # A bare MagicMock config manager answers every attribute with a truthy
    # mock, so CaptureService builds its store under a directory literally
    # named "MagicMock/mock.app_settings.runtime_capture_dir" in the repo root.
    # Point it at scratch space and turn the feature off.
    config_manager = MagicMock()
    config_manager.app_settings.runtime_capture_dir = tempfile.mkdtemp()
    config_manager.app_settings.feature_finetune_capture_enabled = False
    config_manager.app_settings.capture_user_salt = "test-salt"

    session_repo = MagicMock()
    session_repo.get = AsyncMock(side_effect=_get)
    session_repo.create = AsyncMock(side_effect=_create)
    session_repo.update = AsyncMock(side_effect=_update)

    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=config_manager,
        session_repository=session_repo,
        conversation_repository=repository,
    )
    return service, sessions


def _stored_conversation(message_count, user_email=USER):
    return {
        "id": "conv-1",
        "user_email": user_email,
        "title": "The original question",
        "messages": [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"message {i}",
                "message_type": "chat",
            }
            for i in range(message_count)
        ],
    }


async def _run_turn(service, session_id, user_email=USER, on_execute=None, **kwargs):
    """Dispatch a turn with the orchestrator stubbed out.

    ``on_execute`` runs in place of the orchestrator's body, which is where a
    real rewind records that it removed messages.
    """
    orchestrator = MagicMock()

    async def _execute(**_ignored):
        if on_execute is not None:
            on_execute()
        return {"type": "done"}

    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="what were we doing?",
            model="test-model",
            user_email=user_email,
            **kwargs,
        )
    return orchestrator


@pytest.mark.asyncio
async def test_turn_on_a_fresh_session_rehydrates_the_conversation():
    """The reconnect case: a new session_id, the old conversation_id."""
    repository = _FakeRepository({"conv-1": _stored_conversation(50)})
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")

    history = sessions[session_id].history
    assert len(history.messages) == 50, (
        "the turn must run against the stored conversation, not an empty "
        "history -- otherwise the save that follows writes over the record"
    )
    assert history.messages[0].content == "message 0"


@pytest.mark.asyncio
async def test_rehydration_marks_the_session_restored_so_the_title_survives():
    """_save_conversation regenerates the title unless the session is restored."""
    repository = _FakeRepository({"conv-1": _stored_conversation(6)})
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")

    assert sessions[session_id].context.get("_restored") is True


@pytest.mark.asyncio
async def test_a_second_turn_does_not_reload_the_history():
    """The live session is authoritative once it carries the conversation."""
    repository = _FakeRepository({"conv-1": _stored_conversation(4)})
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")
    await _run_turn(service, session_id, conversation_id="conv-1")

    assert len(sessions[session_id].history.messages) == 4, (
        "a second load would duplicate every message"
    )
    assert repository.get_conversation_calls == ["conv-1"]


@pytest.mark.asyncio
async def test_switching_conversations_loads_the_new_one():
    """A session that changes conversation must not carry the old thread over."""
    repository = _FakeRepository(
        {
            "conv-1": _stored_conversation(4),
            "conv-2": {
                "id": "conv-2",
                "user_email": USER,
                "title": "Another",
                "messages": [{"role": "user", "content": "different thread"}],
            },
        }
    )
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")
    await _run_turn(service, session_id, conversation_id="conv-2")

    assert repository.get_conversation_calls == ["conv-1", "conv-2"]
    # Replaced, not appended: splicing conv-1's messages onto conv-2 would then
    # save the combined thread under conv-2's id.
    assert [m.content for m in sessions[session_id].history.messages] == [
        "different thread"
    ]


@pytest.mark.asyncio
async def test_switching_to_an_unknown_conversation_leaves_the_history_alone():
    """A client-side id the store has never seen must not clear the session.

    ``local``/``none`` save modes mint their own ``local_*`` id part-way
    through a conversation. Those turns are incognito and never reach the
    loader, but the store lookup is what actually decides -- so an id the store
    does not have leaves the live thread intact either way.
    """
    repository = _FakeRepository({"conv-1": _stored_conversation(4)})
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")
    await _run_turn(service, session_id, conversation_id="local_1234_abcd")

    assert len(sessions[session_id].history.messages) == 4
    assert sessions[session_id].context["conversation_id"] == "local_1234_abcd"


@pytest.mark.asyncio
async def test_unknown_conversation_id_is_not_treated_as_restored():
    """A brand-new conversation still needs its title generated from turn one."""
    repository = _FakeRepository()
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="brand-new")

    assert sessions[session_id].history.messages == []
    assert "_restored" not in sessions[session_id].context


@pytest.mark.asyncio
async def test_incognito_turns_are_not_rehydrated():
    """Incognito is never persisted, so there is no record it is continuing."""
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(
        service, session_id, conversation_id="conv-1", incognito=True
    )

    assert sessions[session_id].history.messages == []
    assert repository.get_conversation_calls == []


@pytest.mark.asyncio
async def test_a_failing_store_does_not_fail_the_turn():
    """An unreadable store degrades to no context, not to an outage.

    The repository's no-shrink guard is what stops the un-hydrated turn from
    overwriting the stored conversation.
    """
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    repository.get_conversation = MagicMock(side_effect=RuntimeError("db is down"))
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, conversation_id="conv-1")

    assert sessions[session_id].history.messages == []


def _rewind_removed(sessions, session_id):
    """What ChatOrchestrator records once truncate_at_user_index removed rows."""
    return lambda: sessions[session_id].context.__setitem__("rewind_removed", True)


@pytest.mark.asyncio
async def test_rewind_that_removed_messages_allows_the_shorter_save():
    """The one turn that may legitimately shorten the stored conversation."""
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    service, sessions = _make_service(repository)
    session_id = uuid4()
    captured = {}

    with patch.object(
        service, "_save_conversation", side_effect=lambda *a, **kw: captured.update(kw) or True
    ):
        await _run_turn(
            service,
            session_id,
            conversation_id="conv-1",
            rewind_to_user_index=2,
            on_execute=_rewind_removed(sessions, session_id),
        )

    assert captured["allow_shrink"] is True


@pytest.mark.asyncio
async def test_a_rewind_that_removed_nothing_does_not_allow_a_shorter_save():
    """The field is a request, not a permit.

    An out-of-range or malformed index truncates nothing, so the turn is an
    ordinary one and must not carry an exemption from the no-shrink guard.
    """
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    service, sessions = _make_service(repository)
    session_id = uuid4()
    captured = {}

    with patch.object(
        service, "_save_conversation", side_effect=lambda *a, **kw: captured.update(kw) or True
    ):
        await _run_turn(
            service,
            session_id,
            conversation_id="conv-1",
            rewind_to_user_index=999,
        )

    assert captured["allow_shrink"] is False


@pytest.mark.asyncio
async def test_a_failed_hydration_revokes_the_shrink_permission():
    """A truncation measured against a partial session proves nothing.

    If the store could not be read, the session's history is not the stored
    conversation, so even a real rewind must not be allowed to replace it with
    something shorter.
    """
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    repository.get_conversation = MagicMock(side_effect=RuntimeError("db is down"))
    service, sessions = _make_service(repository)
    session_id = uuid4()
    captured = {}

    with patch.object(
        service, "_save_conversation", side_effect=lambda *a, **kw: captured.update(kw) or True
    ):
        await _run_turn(
            service,
            session_id,
            conversation_id="conv-1",
            rewind_to_user_index=2,
            on_execute=_rewind_removed(sessions, session_id),
        )

    assert captured["allow_shrink"] is False


@pytest.mark.asyncio
async def test_the_shrink_permission_does_not_leak_into_the_next_turn():
    """It is earned per turn; the session outlives the turn that earned it."""
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    service, sessions = _make_service(repository)
    session_id = uuid4()
    captured = {}

    with patch.object(
        service, "_save_conversation", side_effect=lambda *a, **kw: captured.update(kw) or True
    ):
        await _run_turn(
            service,
            session_id,
            conversation_id="conv-1",
            rewind_to_user_index=2,
            on_execute=_rewind_removed(sessions, session_id),
        )
        assert captured["allow_shrink"] is True
        await _run_turn(service, session_id, conversation_id="conv-1")

    assert captured["allow_shrink"] is False


@pytest.mark.asyncio
async def test_ordinary_turn_does_not_allow_a_shorter_save():
    repository = _FakeRepository({"conv-1": _stored_conversation(10)})
    service, sessions = _make_service(repository)
    session_id = uuid4()
    captured = {}

    with patch.object(
        service, "_save_conversation", side_effect=lambda *a, **kw: captured.update(kw) or True
    ):
        await _run_turn(service, session_id, conversation_id="conv-1")

    assert captured["allow_shrink"] is False


@pytest.mark.asyncio
async def test_another_users_conversation_is_never_hydrated():
    """Ownership is enforced before any history is loaded."""
    from atlas.domain.errors import AuthorizationError

    repository = _FakeRepository(
        {"conv-1": _stored_conversation(10, user_email="victim@test.com")}
    )
    service, sessions = _make_service(repository)
    session_id = uuid4()

    with pytest.raises(AuthorizationError):
        await _run_turn(
            service, session_id, user_email="attacker@test.com", conversation_id="conv-1"
        )

    assert repository.get_conversation_calls == []


@pytest.mark.asyncio
async def test_default_conversation_id_path_does_not_hit_the_store():
    """No client-supplied id: nothing to rehydrate, and the default still applies."""
    repository = _FakeRepository()
    service, sessions = _make_service(repository)
    session_id = uuid4()

    await _run_turn(service, session_id, user_email=config_manager.app_settings.test_user)

    assert sessions[session_id].context["conversation_id"] == str(session_id)
    assert repository.get_conversation_calls == []


# --- orchestrator: the marker the shrink permission is derived from ----------


async def _run_rewind(rewind_to_user_index):
    """Run a real rewind through the real orchestrator; return the session."""
    from atlas.application.chat.orchestrator import ChatOrchestrator
    from atlas.domain.messages.models import Message, MessageRole
    from atlas.domain.sessions.models import Session
    from atlas.infrastructure.sessions.in_memory_repository import (
        InMemorySessionRepository,
    )

    repo = InMemorySessionRepository()
    session_id = uuid4()
    session = Session(id=session_id, user_email=USER)
    for i in range(3):
        session.history.add_message(Message(role=MessageRole.USER, content=f"q{i}"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content=f"a{i}"))
    await repo.create(session)

    mode = MagicMock()
    mode.run_streaming = AsyncMock(return_value={})
    agent = MagicMock()
    agent.run = AsyncMock(return_value={})
    orchestrator = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=MagicMock(),
        session_repository=repo,
        plain_mode=mode,
        rag_mode=mode,
        tools_mode=mode,
        agent_mode=agent,
    )

    await orchestrator.execute(
        session_id=session_id,
        content="edited",
        model="test-model",
        rewind_to_user_index=rewind_to_user_index,
    )
    return session


@pytest.mark.asyncio
async def test_orchestrator_records_a_rewind_that_removed_messages():
    """This marker is the whole basis of the shrink permission."""
    session = await _run_rewind(1)

    assert session.context.get("rewind_removed") is True


@pytest.mark.asyncio
async def test_orchestrator_records_nothing_for_an_out_of_range_rewind():
    session = await _run_rewind(99)

    assert "rewind_removed" not in session.context


@pytest.mark.asyncio
async def test_orchestrator_records_nothing_for_a_malformed_rewind_index():
    session = await _run_rewind("not-an-index")

    assert "rewind_removed" not in session.context


# --- end to end: the real repository behind the real service -----------------


async def _turn_with_reply(service, session_id, content, **kwargs):
    """A turn whose 'LLM' appends a user/assistant pair, as a real one does."""
    from atlas.domain.messages.models import Message, MessageRole

    orchestrator = MagicMock()

    async def _execute(**_ignored):
        session = await service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content=content))
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content=f"reply to {content}")
        )
        return {"type": "done"}

    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content=content,
            model="test-model",
            user_email=USER,
            conversation_id="conv-e2e",
            **kwargs,
        )


@pytest.mark.asyncio
async def test_end_to_end_reconnect_keeps_the_whole_conversation(repo):
    """The seam this PR exists to close, with nothing faked between the two ends.

    A long conversation is built over one session and persisted through the
    real repository. The socket then drops: the next turn arrives on a brand
    new session_id still carrying the conversation_id the browser held. Before
    this change that turn saved its two messages over all fifty.
    """
    service, sessions = _make_service(repo)

    first_session = uuid4()
    sessions[first_session] = Session(id=first_session, user_email=USER)
    for i in range(25):
        await _turn_with_reply(service, first_session, f"question {i}")

    stored = repo.get_conversation("conv-e2e", USER)
    assert len(stored["messages"]) == 50
    original_title = stored["title"]
    original_first_timestamp = stored["messages"][0]["timestamp"]

    # --- the socket drops; the browser keeps its conversation_id ---
    second_session = uuid4()
    sessions[second_session] = Session(id=second_session, user_email=USER)
    await _turn_with_reply(service, second_session, "after the reconnect")

    assert len(sessions[second_session].history.messages) == 52, (
        "the reconnecting session must run against the stored conversation"
    )
    stored = repo.get_conversation("conv-e2e", USER)
    assert len(stored["messages"]) == 52
    assert stored["messages"][0]["content"] == "question 0"
    assert stored["title"] == original_title, "a reconnect must not rename it"
    assert stored["messages"][0]["timestamp"] == original_first_timestamp, (
        "restoring must not restamp messages that were already stored"
    )


@pytest.mark.asyncio
async def test_hydration_is_retried_after_a_transient_store_failure(repo):
    """A failed read must not lock the session out of hydration for good.

    Otherwise the session's history grows back turn by turn while the
    no-shrink guard refuses every save, until the count catches up and the
    partial thread replaces the real one.
    """
    service, sessions = _make_service(repo)

    seed = uuid4()
    sessions[seed] = Session(id=seed, user_email=USER)
    for i in range(5):
        await _turn_with_reply(service, seed, f"question {i}")
    assert len(repo.get_conversation("conv-e2e", USER)["messages"]) == 10

    reconnected = uuid4()
    sessions[reconnected] = Session(id=reconnected, user_email=USER)

    real_get = repo.get_conversation
    with patch.object(
        repo, "get_conversation", side_effect=RuntimeError("db is down")
    ):
        await _turn_with_reply(service, reconnected, "during the outage")
    assert len(sessions[reconnected].history.messages) == 2
    assert len(real_get("conv-e2e", USER)["messages"]) == 10, (
        "the guard held the line while the store was unreadable"
    )

    # The store comes back. The session is still bound to the conversation but
    # holds a partial history, so the next turn must try again.
    sessions[reconnected].history.messages.clear()
    await _turn_with_reply(service, reconnected, "after the outage")

    assert len(sessions[reconnected].history.messages) == 12
    assert len(repo.get_conversation("conv-e2e", USER)["messages"]) == 12


@pytest.mark.asyncio
async def test_resuming_after_an_incognito_interlude_branches_the_conversation(repo):
    """The savable segment is a new conversation, not a replacement.

    The messages taken while incognito can never be persisted, so the segment
    after it is not a continuation of the stored conversation -- writing it
    back would destroy everything before the incognito turn, and refusing it
    would wedge every remaining turn of the session.
    """
    service, sessions = _make_service(repo)

    seed = uuid4()
    sessions[seed] = Session(id=seed, user_email=USER)
    for i in range(5):
        await _turn_with_reply(service, seed, f"question {i}")
    assert len(repo.get_conversation("conv-e2e", USER)["messages"]) == 10

    # A new session opens that conversation from the sidebar while incognito,
    # takes a turn off the record, then switches saving back on. The save floor
    # is the count at that point, so every later save is a slice.
    resumed = uuid4()
    sessions[resumed] = Session(id=resumed, user_email=USER)
    await service.handle_restore_conversation(
        session_id=resumed,
        conversation_id="conv-e2e",
        messages=[],
        user_email=USER,
    )
    await _turn_with_reply(service, resumed, "off the record", incognito=True)

    saved_frames = []
    await _turn_with_reply(
        service, resumed, "on the record again", incognito=False,
        update_callback=lambda frame: saved_frames.append(frame) or _noop(),
    )

    original = repo.get_conversation("conv-e2e", USER)
    assert len(original["messages"]) == 10, (
        "the conversation the session branched from is untouched"
    )

    branched_id = sessions[resumed].context["conversation_id"]
    assert branched_id != "conv-e2e"
    branched = repo.get_conversation(branched_id, USER)
    assert branched is not None, "the resumed segment was persisted somewhere"
    assert [m["content"] for m in branched["messages"]] == [
        "on the record again",
        "reply to on the record again",
    ], "only the post-incognito segment, and none of the off-the-record turn"
    assert branched["title"] == "on the record again"

    # The client is told the new id, so its next turn names the branch.
    assert any(
        f.get("type") == "conversation_saved" and f.get("conversation_id") == branched_id
        for f in saved_frames
    )


async def _noop():
    return None
