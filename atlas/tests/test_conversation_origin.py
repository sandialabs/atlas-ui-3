"""Conversation launch origin (issue #1187).

A conversation started through the Agent Runs REST API must stay identifiable
in the user's history after the run registry has forgotten the run. These
tests pin the durable half: the launch path writes the origin into the
conversation's metadata, every history read publishes it, and it is fixed at
creation -- a later save neither drops it nor adds one.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.runs import RunRegistry
from atlas.application.chat.runs.external_runs import API_SOURCE
from atlas.domain.conversation_lineage import PARENT_CONVERSATION_METADATA_KEY
from atlas.domain.conversation_origin import (
    ORIGIN_AGENT_RUNS_API,
    ORIGIN_METADATA_KEY,
    carry_recorded_origin,
    origin_fields,
    origin_for_run_source,
    origin_from_metadata,
)
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history.database import reset_engine

USER = "user@test.com"


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import (
        ConversationRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'origin.db'}")
    return ConversationRepository(get_session_factory())


@pytest.fixture
def chat_service(repo):
    from atlas.application.chat.service import ChatService

    return ChatService(llm=MagicMock(), conversation_repository=repo)


def _messages(count=1):
    messages = []
    for index in range(count):
        messages.append({"role": "user", "content": f"ask {index}", "message_type": "chat"})
        messages.append({"role": "assistant", "content": f"reply {index}", "message_type": "chat"})
    return messages


def _save(repo, conversation_id, metadata=None, count=1):
    return repo.save_conversation(
        conversation_id=conversation_id,
        user_email=USER,
        title=conversation_id,
        model="gpt-4",
        messages=_messages(count),
        metadata=metadata,
    )


async def _run_turn(chat_service, conversation_id, **turn_kwargs):
    session_id = uuid4()

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="do work"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        await chat_service.handle_chat_message(
            session_id=session_id,
            content="do work",
            model="gpt-4",
            user_email=USER,
            conversation_id=conversation_id,
            **turn_kwargs,
        )


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def test_the_api_run_source_is_the_recorded_origin():
    assert API_SOURCE == ORIGIN_AGENT_RUNS_API
    assert origin_for_run_source(API_SOURCE) == ORIGIN_AGENT_RUNS_API


@pytest.mark.parametrize("source", ["", None, "atlas_launch", "browser", 7, ["agent_runs_api"]])
def test_other_run_sources_are_not_an_origin(source):
    assert origin_for_run_source(source) is None


@pytest.mark.parametrize(
    "metadata",
    [None, "not a dict", {}, {ORIGIN_METADATA_KEY: ""}, {ORIGIN_METADATA_KEY: "made_up"},
     {ORIGIN_METADATA_KEY: {"nested": True}}],
)
def test_missing_or_unknown_metadata_reads_as_no_origin(metadata):
    assert origin_from_metadata(metadata) is None
    assert origin_fields(metadata) == {"launch_origin": None}


def test_carry_recorded_origin_lets_the_stored_record_decide():
    stored = {ORIGIN_METADATA_KEY: ORIGIN_AGENT_RUNS_API}
    incoming = {"agent_mode": True}
    assert carry_recorded_origin(stored, incoming)[ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API
    assert ORIGIN_METADATA_KEY not in incoming  # a copy, not a mutation
    claimed = {ORIGIN_METADATA_KEY: ORIGIN_AGENT_RUNS_API}
    assert ORIGIN_METADATA_KEY not in carry_recorded_origin({}, claimed)
    assert ORIGIN_METADATA_KEY not in carry_recorded_origin(None, claimed)


# ---------------------------------------------------------------------------
# Live run record
# ---------------------------------------------------------------------------

def test_the_public_run_dict_carries_the_origin_only_for_api_runs():
    registry = RunRegistry()
    api = registry.start(conversation_id="api-conv", user_email=USER)
    api.source = API_SOURCE
    child = registry.start(
        conversation_id="child-conv", user_email=USER, parent_conversation_id="api-conv"
    )
    child.source = "atlas_launch"
    plain = registry.start(conversation_id="plain-conv", user_email=USER)

    assert api.to_public_dict()["launch_origin"] == ORIGIN_AGENT_RUNS_API
    assert child.to_public_dict()["launch_origin"] is None
    assert child.to_public_dict()["parent_conversation_id"] == "api-conv"
    assert plain.to_public_dict()["launch_origin"] is None


def test_the_first_frame_listeners_see_already_carries_the_origin():
    """``start`` notifies listeners itself, so the source cannot be set after."""
    registry = RunRegistry()
    seen = []
    registry.add_listener(USER, lambda record: seen.append(record.to_public_dict()))
    registry.start(conversation_id="api-conv", user_email=USER, source=API_SOURCE)

    assert seen and seen[0]["launch_origin"] == ORIGIN_AGENT_RUNS_API


# ---------------------------------------------------------------------------
# Durable record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_api_launched_turn_saves_its_origin(chat_service, repo):
    await _run_turn(chat_service, "api-conv", launch_origin=API_SOURCE)

    saved = repo.get_conversation("api-conv", USER)
    assert saved["metadata"][ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API
    assert saved["launch_origin"] == ORIGIN_AGENT_RUNS_API
    assert saved["parent_conversation_id"] is None


@pytest.mark.asyncio
async def test_an_ordinary_turn_saves_no_origin(chat_service, repo):
    await _run_turn(chat_service, "plain-conv")

    saved = repo.get_conversation("plain-conv", USER)
    assert ORIGIN_METADATA_KEY not in saved["metadata"]
    assert saved["launch_origin"] is None


@pytest.mark.asyncio
async def test_an_unknown_origin_argument_is_not_recorded(chat_service, repo):
    await _run_turn(chat_service, "odd-conv", launch_origin="made_up")

    assert repo.get_conversation("odd-conv", USER)["launch_origin"] is None


@pytest.mark.asyncio
async def test_a_browser_turn_on_an_api_conversation_keeps_the_origin(chat_service, repo):
    """The user continues an API-launched conversation from the browser.

    That turn runs in a new session and passes no origin; the save replaces
    the whole metadata blob, so only the repository rule keeps the marker.
    """
    await _run_turn(chat_service, "api-conv", launch_origin=API_SOURCE)
    await _run_turn(chat_service, "api-conv")

    saved = repo.get_conversation("api-conv", USER)
    assert saved["message_count"] == 4
    assert saved["metadata"][ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API
    assert saved["launch_origin"] == ORIGIN_AGENT_RUNS_API


@pytest.mark.asyncio
async def test_a_session_moving_to_another_conversation_does_not_carry_the_origin():
    """The context key is assigned every turn, not only when an origin is passed."""
    from atlas.application.chat.service import ChatService

    store = MagicMock()
    store.get_conversation.return_value = None
    store.get_conversation_owner.return_value = None
    chat_service = ChatService(llm=MagicMock(), conversation_repository=store)
    session_id = uuid4()

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="do work"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        for conversation_id, extra in (
            ("api-conv", {"launch_origin": API_SOURCE}),
            ("next-conv", {}),
        ):
            await chat_service.handle_chat_message(
                session_id=session_id,
                content="do work",
                model="gpt-4",
                user_email=USER,
                conversation_id=conversation_id,
                **extra,
            )

    saves = {
        call.kwargs["conversation_id"]: call.kwargs["metadata"]
        for call in store.save_conversation.call_args_list
    }
    assert saves["api-conv"][ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API
    assert ORIGIN_METADATA_KEY not in saves["next-conv"]


@pytest.mark.asyncio
async def test_a_browser_turn_carries_the_stored_origin_while_it_runs(chat_service, repo):
    """The live view of a running turn must agree with the stored record.

    ``GET /api/conversations/{id}`` prefers the run's live transcript, whose
    metadata is built from the session context, so the context has to read
    the origin back when it loads the conversation.
    """
    from atlas.application.chat.runs.in_flight import _live_metadata

    await _run_turn(chat_service, "api-conv", launch_origin=API_SOURCE)
    seen = {}
    session_id = uuid4()

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        seen.update(_live_metadata(session))
        session.history.add_message(Message(role=MessageRole.USER, content="more"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        await chat_service.handle_chat_message(
            session_id=session_id, content="more", model="gpt-4", user_email=USER,
            conversation_id="api-conv",
        )

    assert seen[ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API
    assert repo.get_conversation("api-conv", USER)["launch_origin"] == ORIGIN_AGENT_RUNS_API


@pytest.mark.asyncio
async def test_an_incognito_interlude_does_not_tag_the_users_new_conversation(chat_service, repo):
    """Saving resumes after incognito as a *new* conversation the user started.

    The session still holds the API conversation it restored, so its origin
    must not be written onto the new record.
    """
    from atlas.domain.conversation_origin import ORIGIN_METADATA_KEY as KEY

    await _run_turn(chat_service, "api-conv", launch_origin=API_SOURCE)
    session_id = uuid4()
    session = await chat_service.create_session(session_id, USER)
    await chat_service._hydrate_session_from_store(
        session, "api-conv", USER, is_incognito=False
    )
    assert session.context[KEY] == ORIGIN_AGENT_RUNS_API
    session.context["conversation_id"] = "api-conv"
    session.context["_restored"] = True
    session.history.add_message(Message(role=MessageRole.USER, content="private"))
    session.history.add_message(Message(role=MessageRole.ASSISTANT, content="ok"))
    floor = len(session.history.messages)
    session.history.add_message(Message(role=MessageRole.USER, content="saved again"))
    session.history.add_message(Message(role=MessageRole.ASSISTANT, content="ok"))

    assert chat_service._save_conversation(session, USER, "gpt-4", start_index=floor)

    new_id = session.context["conversation_id"]
    assert new_id != "api-conv"
    assert repo.get_conversation(new_id, USER)["launch_origin"] is None
    assert repo.get_conversation("api-conv", USER)["launch_origin"] == ORIGIN_AGENT_RUNS_API


@pytest.mark.asyncio
async def test_an_incognito_switch_drops_the_carried_origin(chat_service, repo):
    await _run_turn(chat_service, "api-conv", launch_origin=API_SOURCE)
    session = await chat_service.create_session(uuid4(), USER)
    await chat_service._hydrate_session_from_store(
        session, "api-conv", USER, is_incognito=False
    )
    assert session.context[ORIGIN_METADATA_KEY] == ORIGIN_AGENT_RUNS_API

    await chat_service._hydrate_session_from_store(
        session, "another-conv", USER, is_incognito=True
    )
    assert not session.context.get(ORIGIN_METADATA_KEY)


@pytest.mark.asyncio
async def test_a_launch_argument_cannot_mark_a_stored_conversation(chat_service, repo):
    await _run_turn(chat_service, "plain-conv")
    await _run_turn(chat_service, "plain-conv", launch_origin=API_SOURCE)

    saved = repo.get_conversation("plain-conv", USER)
    assert saved["message_count"] == 4
    assert saved["launch_origin"] is None


def test_a_later_save_cannot_add_an_origin_to_an_existing_conversation(repo):
    _save(repo, "plain-conv", metadata={"agent_mode": False})
    _save(repo, "plain-conv", metadata={ORIGIN_METADATA_KEY: ORIGIN_AGENT_RUNS_API}, count=2)

    saved = repo.get_conversation("plain-conv", USER)
    assert saved["message_count"] == 4
    assert ORIGIN_METADATA_KEY not in saved["metadata"]
    assert saved["launch_origin"] is None


def test_every_history_read_publishes_the_origin(repo):
    _save(repo, "api-conv", metadata={ORIGIN_METADATA_KEY: ORIGIN_AGENT_RUNS_API})
    _save(repo, "plain-conv")
    _save(repo, "parent-conv")
    _save(
        repo,
        "child-conv",
        metadata={PARENT_CONVERSATION_METADATA_KEY: "parent-conv"},
    )

    def by_id(rows):
        return {row["id"]: row for row in rows}

    listed = by_id(repo.list_conversations(USER))
    searched = by_id(repo.search_conversations(USER, "ask"))
    exported = by_id(repo.export_all_conversations(USER))
    for rows in (listed, searched, exported):
        assert rows["api-conv"]["launch_origin"] == ORIGIN_AGENT_RUNS_API
        assert rows["plain-conv"]["launch_origin"] is None
        # A sub-conversation is identified by its parent link, not an origin.
        assert rows["child-conv"]["launch_origin"] is None
        assert rows["child-conv"]["parent_conversation_id"] == "parent-conv"
        assert rows["api-conv"]["parent_conversation_id"] is None
