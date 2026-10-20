"""Resuming a session on another replica (issue #1091).

Each "replica" is a ChatService over its own RedisSessionRepository; the
repositories share one fake Redis server, as API replicas share a Redis
deployment. A client's socket drops on replica A and reconnects to replica B,
which resumes the session and keeps going; A's late teardown must not clobber
what B wrote.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import fakeredis
import pytest

import atlas.main as main
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.infrastructure.sessions.redis_repository import RedisSessionRepository

USER = "alice@example.com"


def _redis_repo(server):
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0"), client=fakeredis.FakeAsyncRedis(server=server)
    )
    return RedisSessionRepository(manager, ttl_seconds=3600)


def _service(repo, connection_id):
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )
    service.connection_id = connection_id
    return service


def _orchestrator(seen):
    """Fake orchestrator: records what history the turn saw, then answers."""

    async def execute(**kwargs):
        session = kwargs["session"]
        seen.append([m.content for m in session.history.messages])
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content=f"re: {kwargs['content']}")
        )
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=execute)
    return orchestrator


async def _turn(service, session_id, content, seen):
    with patch.object(service, "_get_orchestrator", return_value=_orchestrator(seen)):
        await service.handle_chat_message(
            session_id=session_id,
            content=content,
            model="test-model",
            user_email=USER,
            conversation_id="conv-42",
        )


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.mark.asyncio
async def test_reconnect_to_another_replica_continues_the_session(server):
    replica_a = _service(_redis_repo(server), "conn-a")
    replica_b = _service(_redis_repo(server), "conn-b")
    session_id = uuid4()
    seen = []

    await _turn(replica_a, session_id, "first question", seen)
    live_a = await replica_a.session_repository.get(session_id)
    live_a.context.setdefault("files", {})["notes.txt"] = {"key": "u/notes.txt"}
    await replica_a.persist_session(live_a)

    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert resumed is not None
    assert resumed.connection_owner == "conn-b"
    assert resumed.context["conversation_id"] == "conv-42"
    assert resumed.context["files"]["notes.txt"]["key"] == "u/notes.txt"

    await _turn(replica_b, session_id, "follow-up", seen)
    # The follow-up on B ran with the history A built.
    assert seen[1] == ["first question", "re: first question"]

    # A notices its socket closed only now. Its copy is stale: the teardown
    # must not overwrite B's newer session.
    with patch("atlas.modules.mcp_tools.mcp_tool_manager.release_sessions", new=AsyncMock()):
        await main.cleanup_disconnected_session(replica_a, session_id, USER, {"task": None})

    stored = await _redis_repo(server).get(session_id)
    assert stored.active is True
    assert stored.connection_owner == "conn-b"
    assert [m.content for m in stored.history.messages] == [
        "first question", "re: first question", "follow-up", "re: follow-up",
    ]


@pytest.mark.asyncio
async def test_resume_refuses_another_users_session(server):
    replica_a = _service(_redis_repo(server), "conn-a")
    replica_b = _service(_redis_repo(server), "conn-b")
    session_id = uuid4()
    await _turn(replica_a, session_id, "private", [])

    assert await main._resume_connection_session(replica_b, str(session_id), "mallory@example.com") is None
    stored = await _redis_repo(server).get(session_id)
    assert stored.connection_owner == "conn-a"


@pytest.mark.asyncio
async def test_resume_refuses_a_tracked_run_session(server):
    repo = _redis_repo(server)
    run_session = Session(id=uuid4(), user_email=USER)
    run_session.context[main._RUN_SESSION_MARKER] = True
    await repo.create(run_session)

    replica_b = _service(_redis_repo(server), "conn-b")
    assert await main._resume_connection_session(replica_b, str(run_session.id), USER) is None


@pytest.mark.parametrize("requested", [None, "", "not-a-uuid", str(uuid4())])
@pytest.mark.asyncio
async def test_unknown_or_malformed_ids_start_fresh(server, requested):
    replica = _service(_redis_repo(server), "conn-b")
    assert await main._resume_connection_session(replica, requested, USER) is None


@pytest.mark.asyncio
async def test_store_outage_starts_a_fresh_session(server):
    replica_a = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    await _turn(replica_a, session_id, "hello", [])
    replica_b = _service(_redis_repo(server), "conn-b")

    server.connected = False
    assert await main._resume_connection_session(replica_b, str(session_id), USER) is None


@pytest.mark.asyncio
async def test_resume_restores_incognito_and_save_floor(server):
    replica_a = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    with patch.object(replica_a, "_get_orchestrator", return_value=_orchestrator([])):
        await replica_a.handle_chat_message(
            session_id=session_id, content="secret", model="m", user_email=USER, incognito=True,
        )

    replica_b = _service(_redis_repo(server), "conn-b")
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)

    assert session_id in replica_b._incognito_sessions
    # Incognito messages never reached the shared store, so the resumed
    # history is empty and no floor is left to skip past.
    assert resumed.history.messages == []
    assert session_id not in replica_b._incognito_save_floor


@pytest.mark.asyncio
async def test_resumed_incognito_session_saves_later_turns_after_opting_in(server):
    """An emptied history must not leave a stale floor that hides new turns."""
    replica_a = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    with patch.object(replica_a, "_get_orchestrator", return_value=_orchestrator([])):
        await replica_a.handle_chat_message(
            session_id=session_id, content="secret", model="m", user_email=USER, incognito=True,
        )

    replica_b = _service(_redis_repo(server), "conn-b")
    replica_b.conversation_repository = MagicMock()
    await main._resume_connection_session(replica_b, str(session_id), USER)
    saved = []
    with patch.object(replica_b, "_get_orchestrator", return_value=_orchestrator([])), \
            patch.object(replica_b, "_save_conversation", side_effect=lambda *a, **k: saved.append(k) or True):
        await replica_b.handle_chat_message(
            session_id=session_id, content="now saved", model="m", user_email=USER, incognito=False,
        )
    assert saved and saved[0]["start_index"] == 0


@pytest.mark.asyncio
async def test_save_floor_survives_when_the_history_is_stored(server):
    """Incognito prefix then opt-in: the stored history keeps the prefix, so the floor stays."""
    replica_a = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    with patch.object(replica_a, "_get_orchestrator", return_value=_orchestrator([])):
        await replica_a.handle_chat_message(
            session_id=session_id, content="secret", model="m", user_email=USER, incognito=True,
        )
        await replica_a.handle_chat_message(
            session_id=session_id, content="public", model="m", user_email=USER, incognito=False,
        )

    replica_b = _service(_redis_repo(server), "conn-b")
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert len(resumed.history.messages) == 4
    assert replica_b._incognito_save_floor[session_id] == 2
    assert session_id in replica_b._save_floor_locked


@pytest.mark.asyncio
async def test_write_locks_do_not_outlive_their_writes(server):
    repo = _redis_repo(server)
    replica = _service(repo, "conn-a")
    for _ in range(5):
        session_id = uuid4()
        await replica.create_session(session_id, USER)
        await replica.end_session(session_id)
    assert repo._locks == {}
    assert repo.stats()["live_sessions"] == 0


@pytest.mark.asyncio
async def test_same_process_takeover_leaves_the_session_to_the_new_connection():
    """Process-local store: the old socket's teardown skips a resumed session."""
    repo = InMemorySessionRepository()
    old = _service(repo, "conn-old")
    new = _service(repo, "conn-new")
    session_id = uuid4()
    await _turn(old, session_id, "hello", [])

    resumed = await main._resume_connection_session(new, str(session_id), USER)
    assert resumed is not None

    release = AsyncMock()
    with patch("atlas.modules.mcp_tools.mcp_tool_manager.release_sessions", new=release):
        await main.cleanup_disconnected_session(old, session_id, USER, {"task": None})

    release.assert_not_called()
    session = await repo.get(session_id)
    assert session.active is True and session.connection_owner == "conn-new"


@pytest.mark.asyncio
async def test_persist_session_never_raises_on_store_failures(server):
    replica = _service(_redis_repo(server), "conn-a")
    session = await replica.create_session(uuid4(), USER)
    server.connected = False
    assert await replica.persist_session(session) is False


@pytest.mark.asyncio
async def test_end_session_survives_an_outage(server):
    replica = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    await replica.create_session(session_id, USER)
    server.connected = False
    await replica.end_session(session_id)  # logs, does not raise
