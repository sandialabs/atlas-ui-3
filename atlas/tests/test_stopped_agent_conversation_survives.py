"""A stopped agent run is still there after a browser refresh (issue #842).

Issue #755 made a cancelled turn persist instead of being discarded, and
issue #884 then moved the Stop button onto the run registry, so the cancel
now arrives through ``RunRegistry.cancel`` rather than through the
connection's single untracked task. These tests pin the whole chain that a
user actually exercises -- stop a long agent run, reload the page -- from the
real cancel entry point down to a read-back out of DuckDB, which is the store
the issue was reported against.

The existing interrupted-turn tests cover the pieces (the mode runners, the
service's cancel handler, the digest round-trip); what they do not cover is
the registry-driven stop reaching storage, which is the gap these close.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.runs import RunRegistry, reset_run_registry
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


@pytest.mark.asyncio
async def test_stopping_agent_mode_keeps_the_conversation_after_refresh(repo):
    service, sessions = _make_service(repo)
    registry = RunRegistry(max_concurrent_runs_per_user=5)
    conversation_id = str(uuid4())
    record = registry.start(conversation_id=conversation_id, user_email=USER)
    session_id = record.session_id

    step_30 = asyncio.Event()

    async def fake_execute(**kwargs):
        """Agent grinds through some steps, then hangs on step 30's tool."""
        session = sessions[session_id]
        session.history.add_message(Message(
            role=MessageRole.USER, content=kwargs["content"],
        ))
        for step in range(30):
            session.history.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=f"step {step}",
                metadata={"message_type": "agent_intermediate", "agent_mode": True},
            ))
        step_30.set()
        await asyncio.sleep(3600)  # tool that never returns -> user hits Stop

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
    await step_30.wait()

    # The Stop button.
    assert registry.cancel(record.run_id, USER) is True
    with pytest.raises(asyncio.CancelledError):
        await task

    # The browser refresh: read the conversation straight back out of DuckDB.
    stored = repo.get_conversation(conversation_id, USER)
    assert stored is not None, "issue #842: the stopped run's conversation was lost"
    contents = [m["content"] for m in stored["messages"]]
    assert "do the big thing" in contents
    assert any("step 29" == c for c in contents), "agent progress must survive"
    assert any("stopped before it finished" in c for c in contents), \
        "the turn must be closed so the next prompt is not user -> user"


@pytest.mark.asyncio
async def test_stopping_a_brand_new_conversation_still_saves_it(repo):
    """The first turn of a fresh chat, stopped: nothing has a conversation id
    yet, so the save has to mint one and announce it to the client."""
    service, sessions = _make_service(repo)
    session_id = uuid4()
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
                session_id=session_id, content="start the agent",
                model="test-model", user_email=USER, agent_mode=True,
                update_callback=update_callback,
            )

    task = asyncio.create_task(turn())
    await running.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = [e for e in events if e.get("type") == "conversation_saved"]
    assert saved, "the client is never told which conversation to reopen"
    stored = repo.get_conversation(saved[-1]["conversation_id"], USER)
    assert stored is not None
    assert "start the agent" in [m["content"] for m in stored["messages"]]
