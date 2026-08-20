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

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.service import ChatService
from atlas.application.chat.utilities.conversation_loader import (
    load_messages_into_history,
)
from atlas.domain.messages.models import ConversationHistory
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

    session_repo = MagicMock()
    session_repo.get = AsyncMock(side_effect=_get)
    session_repo.create = AsyncMock(side_effect=_create)
    session_repo.update = AsyncMock(side_effect=_update)

    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
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


async def _run_turn(service, session_id, user_email=USER, **kwargs):
    """Dispatch a turn with the orchestrator stubbed out."""
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value={"type": "done"})
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
    # A switch only reaches the loader once the live history is cleared; what
    # matters here is that the server notices the change and asks the store.
    sessions[session_id].history.messages.clear()
    await _run_turn(service, session_id, conversation_id="conv-2")

    assert repository.get_conversation_calls == ["conv-1", "conv-2"]
    assert [m.content for m in sessions[session_id].history.messages] == [
        "different thread"
    ]


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


@pytest.mark.asyncio
async def test_rewind_turn_allows_the_shorter_save():
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
        )

    assert captured["allow_shrink"] is True


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
