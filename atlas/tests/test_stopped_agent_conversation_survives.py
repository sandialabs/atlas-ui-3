"""A stopped agent run is still there after a browser refresh (issue #842).

Issue #755 made a cancelled turn persist instead of being discarded, and
issue #884 then moved the Stop button onto the run registry, so the cancel now
arrives through ``_cancel_addressed_run`` -> ``RunRegistry.cancel`` rather than
through the connection's single untracked task. These tests pin the whole chain
a user actually exercises -- stop a long agent run, reload the page -- entering
at the transport's own stop resolver and ending at a read-back out of DuckDB,
the store the issue was reported against.

The existing interrupted-turn tests cover the pieces (the mode runners, the
service's cancel handler, the digest round-trip); what they do not cover is a
registry-addressed stop reaching storage, which is the gap these close.

Both branches of the stop resolver are covered: a frame naming a ``run_id``,
and one naming only the ``conversation_id`` -- the latter on a brand-new chat,
whose id the transport mints itself (main.py:1210) because the first agent turn
of a new conversation is the single most common case.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from main import _cancel_addressed_run

from atlas.application.chat.runs import get_run_registry, reset_run_registry
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history import (
    ConversationRepository,
    get_session_factory,
    init_database,
)
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.config.config_manager import config_manager

USER = config_manager.app_settings.test_user

# A stop that fails to propagate must fail the test, not park a coroutine on a
# one-hour sleep and hang CI. Every wait on the turn task is bounded by this.
CANCEL_DEADLINE_SECONDS = 5


@pytest.fixture(autouse=True)
def _clean():
    reset_engine()
    reset_run_registry()
    yield
    reset_engine()
    reset_run_registry()


@pytest.fixture
def repo(tmp_path):
    init_database(f"duckdb:///{tmp_path / 'i842.db'}")
    return ConversationRepository(get_session_factory())


def _make_service(repo):
    sessions = {}

    async def _get(sid):
        return sessions.get(sid)

    async def _put(session):
        sessions[session.id] = session

    session_repo = MagicMock()
    session_repo.get = AsyncMock(side_effect=_get)
    session_repo.create = AsyncMock(side_effect=_put)
    session_repo.update = AsyncMock(side_effect=_put)

    return ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=session_repo,
        conversation_repository=repo,
    ), sessions


async def _stop_and_await(task, stop_frame):
    """Press Stop the way the transport does, then wait -- with a deadline."""
    assert _cancel_addressed_run(get_run_registry(), USER, stop_frame) is True, (
        "the stop frame did not resolve to a tracked run"
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, CANCEL_DEADLINE_SECONDS)


@pytest.mark.asyncio
async def test_stopping_an_agent_run_by_run_id_keeps_every_step(repo):
    """Step 30 of 50, Stop, refresh: the whole transcript is still in DuckDB."""
    service, sessions = _make_service(repo)
    registry = get_run_registry()
    conversation_id = str(uuid4())
    record = registry.start(conversation_id=conversation_id, user_email=USER)
    session_id = record.session_id

    deep_into_the_run = asyncio.Event()

    async def fake_execute(**kwargs):
        session = sessions[session_id]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        for step in range(30):
            session.history.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=f"step {step}",
                metadata={"message_type": "agent_intermediate", "agent_mode": True},
            ))
        deep_into_the_run.set()
        await asyncio.sleep(3600)  # a tool that never returns -> the user hits Stop

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=fake_execute)

    async def turn():
        with patch.object(service, "_get_orchestrator", return_value=orchestrator):
            await service.handle_chat_message(
                session_id=session_id,
                content="do the big thing",
                model="test-model",
                user_email=USER,
                agent_mode=True,
                conversation_id=conversation_id,
            )

    task = asyncio.create_task(turn())
    registry.attach_task(record.run_id, task)
    await deep_into_the_run.wait()

    await _stop_and_await(task, {"run_id": record.run_id})

    # The browser refresh: read the conversation straight back out of DuckDB.
    stored = repo.get_conversation(conversation_id, USER)
    assert stored is not None, "issue #842: the stopped run's conversation was lost"

    rows = [(m["role"], m["content"], m.get("message_type")) for m in stored["messages"]]
    assert rows[0] == ("user", "do the big thing", "chat")
    # Every step, in order -- not merely the last one, which would still pass
    # if the middle of the run had been dropped.
    assert rows[1:31] == [
        ("assistant", f"step {step}", "agent_intermediate") for step in range(30)
    ]
    assert rows[31][0] == "assistant"
    assert "stopped before it finished" in rows[31][1], (
        "the turn must be closed, or the next prompt replays as user -> user"
    )
    assert len(rows) == 32


@pytest.mark.asyncio
async def test_stopping_a_brand_new_conversation_by_conversation_id_saves_it(repo):
    """The first agent turn of a fresh chat, stopped before anything finishes.

    The client sent no conversation id, so the transport minted one
    (main.py:1210) and keyed the run by it; the stop frame names only that id.
    """
    service, sessions = _make_service(repo)
    registry = get_run_registry()
    minted_conversation_id = str(uuid4())  # what main.py mints for a new agent turn
    record = registry.start(conversation_id=minted_conversation_id, user_email=USER)
    session_id = record.session_id

    running = asyncio.Event()
    events = []

    async def update_callback(message):
        events.append(message)

    async def fake_execute(**kwargs):
        session = sessions[session_id]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        session.history.add_message(Message(
            role=MessageRole.ASSISTANT, content="working on it",
            metadata={"message_type": "agent_intermediate", "agent_mode": True},
        ))
        running.set()
        await asyncio.sleep(3600)

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=fake_execute)

    async def turn():
        with patch.object(service, "_get_orchestrator", return_value=orchestrator):
            await service.handle_chat_message(
                session_id=session_id,
                content="start the agent",
                model="test-model",
                user_email=USER,
                agent_mode=True,
                selected_tools=["calc_add"],
                conversation_id=minted_conversation_id,
                update_callback=update_callback,
            )

    task = asyncio.create_task(turn())
    registry.attach_task(record.run_id, task)
    await running.wait()

    # No run_id on the frame: the resolver has to find the run by conversation.
    await _stop_and_await(task, {"conversation_id": minted_conversation_id})

    saved = [e for e in events if e.get("type") == "conversation_saved"]
    assert saved, "the client is never told which conversation to reopen"
    assert saved[-1]["conversation_id"] == minted_conversation_id, (
        "the turn must be saved under the same id the run is keyed by"
    )

    stored = repo.get_conversation(minted_conversation_id, USER)
    assert stored is not None
    assert [(m["role"], m["content"]) for m in stored["messages"]][:2] == [
        ("user", "start the agent"),
        ("assistant", "working on it"),
    ]
    assert "stopped before it finished" in stored["messages"][-1]["content"]
