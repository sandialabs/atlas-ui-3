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
from atlas.domain.errors import SessionConflictError
from atlas.domain.messages.models import Message, MessageRole
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
    tool_manager = MagicMock()
    tool_manager.release_sessions = AsyncMock()
    service = ChatService(
        llm=MagicMock(),
        tool_manager=tool_manager,
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


async def _disconnect(service, session_id):
    """The old socket closing: its normal teardown ends the session."""
    await main.cleanup_disconnected_session(service, session_id, USER, {"task": None})


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
    await _disconnect(replica_a, session_id)

    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert resumed is not None
    assert resumed.connection_owner == "conn-b"
    assert resumed.context["conversation_id"] == "conv-42"
    assert resumed.context["files"]["notes.txt"]["key"] == "u/notes.txt"

    await _turn(replica_b, session_id, "follow-up", seen)
    # The follow-up on B ran with the history A built.
    assert seen[1] == ["first question", "re: first question"]

    # A late write from A (an interrupted turn still unwinding there) holds
    # A's old copy and must not overwrite B's newer session.
    live_a.history.add_message(Message(role=MessageRole.ASSISTANT, content="stale"))
    assert await replica_a.persist_session(live_a) is False

    stored = await _redis_repo(server).get(session_id)
    assert stored.active is True
    assert stored.connection_owner == "conn-b"
    # A New Chat from the superseded connection cannot take the id back.
    with pytest.raises(SessionConflictError):
        await replica_a.create_session(session_id, USER)
    stored = await _redis_repo(server).get(session_id)
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
    await _disconnect(replica_a, session_id)

    assert await main._resume_connection_session(replica_b, str(session_id), "mallory@example.com") is None
    stored = await _redis_repo(server).get(session_id)
    assert stored.connection_owner == "conn-a"


@pytest.mark.asyncio
async def test_resume_refuses_a_tracked_run_session(server):
    """The run marker is stored as soon as the run session is seeded."""
    repo_a = _redis_repo(server)
    replica_a = _service(repo_a, "conn-a")
    connection_id, run_id = uuid4(), uuid4()
    await main._seed_run_session_files(
        replica_a, connection_id, run_id, USER, conversation_id="conv-run"
    )
    stored = await _redis_repo(server).get(run_id)
    assert stored.context[main._RUN_SESSION_MARKER] is True

    # Even once the run's session is no longer held, it is never resumable.
    run_session = await repo_a.get(run_id)
    run_session.active = False
    await repo_a.update(run_session)
    replica_b = _service(_redis_repo(server), "conn-b")
    assert await main._resume_connection_session(replica_b, str(run_id), USER) is None


# Fixed values: a random id in the parameters would differ between xdist
# workers and fail collection.
@pytest.mark.parametrize(
    "requested", [None, "", "not-a-uuid", "6b1f6a52-0f8e-4d0c-9a51-3c2b8f1d7e40"]
)
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
    await _disconnect(replica_a, session_id)

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
    await _disconnect(replica_a, session_id)

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
async def test_incognito_prefix_never_reaches_the_store_after_opting_in(server):
    """Incognito turn, then a saved one: only the saved turn is written."""
    repo_a = _redis_repo(server)
    replica_a = _service(repo_a, "conn-a")
    session_id = uuid4()
    with patch.object(replica_a, "_get_orchestrator", return_value=_orchestrator([])):
        await replica_a.handle_chat_message(
            session_id=session_id, content="secret", model="m", user_email=USER, incognito=True,
        )
        await replica_a.handle_chat_message(
            session_id=session_id, content="public", model="m", user_email=USER, incognito=False,
        )
    raw = await repo_a._redis.client.hget(repo_a._key(session_id), "data")
    assert b"secret" not in raw
    assert b"public" in raw
    await _disconnect(replica_a, session_id)

    replica_b = _service(_redis_repo(server), "conn-b")
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert [m.content for m in resumed.history.messages] == ["public", "re: public"]
    # The stored history starts after the prefix, so the floor is rebased.
    assert session_id not in replica_b._incognito_save_floor
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
async def test_active_session_is_not_taken_over():
    """The previous connection has not torn down yet: start fresh instead of racing it."""
    repo = InMemorySessionRepository()
    old = _service(repo, "conn-old")
    new = _service(repo, "conn-new")
    session_id = uuid4()
    await _turn(old, session_id, "hello", [])

    assert await main._resume_connection_session(new, str(session_id), USER) is None
    session = await repo.get(session_id)
    assert session.connection_owner == "conn-old"


@pytest.mark.asyncio
async def test_same_process_takeover_detaches_the_old_connection():
    """Process-local store: the resumed session is a new object the old one cannot touch."""
    repo = InMemorySessionRepository()
    old = _service(repo, "conn-old")
    new = _service(repo, "conn-new")
    session_id = uuid4()
    await _turn(old, session_id, "hello", [])
    old_object = await repo.get(session_id)
    await _disconnect(old, session_id)

    resumed = await main._resume_connection_session(new, str(session_id), USER)
    assert resumed is not None and resumed is not old_object
    assert await repo.get(session_id) is resumed

    # The old connection's late work: a write through its stale object, a
    # second teardown, and a New Chat are all kept off the resumed session.
    old_object.history.add_message(Message(role=MessageRole.ASSISTANT, content="stale"))
    assert await old.persist_session(old_object) is False
    # The first disconnect already released the conversation's sessions (issue
    # #1013: that release now targets the service's tool manager, not the
    # package-level singleton). Reset the mock so this assertion is about the
    # second teardown only.
    old.tool_manager.release_sessions.reset_mock()
    await main.cleanup_disconnected_session(old, session_id, USER, {"task": None})
    old.tool_manager.release_sessions.assert_not_called()
    with pytest.raises(SessionConflictError):
        await old.create_session(session_id, USER)

    session = await repo.get(session_id)
    assert session is resumed
    assert session.active is True and session.connection_owner == "conn-new"
    assert [m.content for m in session.history.messages] == ["hello", "re: hello"]


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


@pytest.mark.asyncio
async def test_resume_rehydrates_from_chat_history_instead_of_the_snapshot(server):
    """The snapshot can trail the database (a turn committed during takeover)."""
    replica_a = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    await _turn(replica_a, session_id, "first question", [])
    await _disconnect(replica_a, session_id)

    replica_b = _service(_redis_repo(server), "conn-b")
    replica_b.conversation_repository = MagicMock()
    replica_b.conversation_repository.get_conversation_owner.return_value = USER
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert resumed.history.messages == []
    assert resumed.context["conversation_id"] == "conv-42"

    hydrate = AsyncMock(return_value=0)
    with patch.object(replica_b, "_hydrate_session_from_store", new=hydrate), \
            patch.object(replica_b, "_save_conversation", return_value=True):
        await _turn(replica_b, session_id, "follow-up", [])
    hydrate.assert_awaited_once()
    assert hydrate.await_args.args[1] == "conv-42"


@pytest.mark.asyncio
async def test_disconnect_during_an_outage_still_tears_down(server):
    replica = _service(_redis_repo(server), "conn-a")
    session_id = uuid4()
    replica._incognito_sessions.add(session_id)
    server.connected = False
    await _disconnect(replica, session_id)  # does not raise
    assert session_id not in replica._incognito_sessions


@pytest.mark.asyncio
async def test_teardown_during_an_outage_does_not_pin_the_session(server):
    repo = _redis_repo(server)
    replica = _service(repo, "conn-a")
    session_id = uuid4()
    await replica.create_session(session_id, USER)
    assert repo.is_live_here(session_id)

    server.connected = False
    await replica.end_session(session_id)
    assert not repo.is_live_here(session_id)


@pytest.mark.asyncio
async def test_run_merge_on_another_replica_does_not_bump_the_owner(server):
    """A late run-file merge on the old replica must not make B's next write lose."""
    repo_a = _redis_repo(server)
    replica_a = _service(repo_a, "conn-a")
    session_id, run_id = uuid4(), uuid4()
    await _turn(replica_a, session_id, "start a run", [])
    run_session = await replica_a.create_session(run_id, USER)
    run_session.context["files"] = {"out.csv": {"key": "u/out.csv"}}
    await _disconnect(replica_a, session_id)

    replica_b = _service(_redis_repo(server), "conn-b")
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    version = resumed.store_version

    await main._merge_run_session_files(replica_a, run_id, session_id, "conv-42")
    assert (await _redis_repo(server).get(session_id)).store_version == version
    assert await replica_b.persist_session(resumed) is True


@pytest.mark.asyncio
async def test_run_files_merged_after_disconnect_survive_a_later_resume(server):
    repo_a = _redis_repo(server)
    replica_a = _service(repo_a, "conn-a")
    session_id, run_id = uuid4(), uuid4()
    await _turn(replica_a, session_id, "start a run", [])
    run_session = await replica_a.create_session(run_id, USER)
    run_session.context["files"] = {"out.csv": {"key": "u/out.csv"}}
    await _disconnect(replica_a, session_id)

    await main._merge_run_session_files(replica_a, run_id, session_id, "conv-42")

    replica_b = _service(_redis_repo(server), "conn-b")
    resumed = await main._resume_connection_session(replica_b, str(session_id), USER)
    assert resumed.context["files"]["out.csv"]["key"] == "u/out.csv"


@pytest.mark.asyncio
async def test_commit_after_teardown_keeps_the_incognito_prefix_out(server):
    """A cancelled turn committing after end_session still honours its floor."""
    repo = _redis_repo(server)
    replica = _service(repo, "conn-a")
    session_id = uuid4()
    with patch.object(replica, "_get_orchestrator", return_value=_orchestrator([])):
        await replica.handle_chat_message(
            session_id=session_id, content="secret", model="m", user_email=USER, incognito=True,
        )
    session = await repo.get(session_id)
    session.context["incognito"] = False  # the cancelled turn was a saved one
    session.history.add_message(Message(role=MessageRole.USER, content="public"))
    # The torn-down service state the late commit would otherwise read.
    replica._incognito_save_floor.clear()
    replica._save_floor_locked.clear()
    replica._incognito_sessions.clear()
    await replica._commit_turn(
        session, session_id, USER, "m", None, is_incognito=False, save_floor=2,
    )
    raw = await repo._redis.client.hget(repo._key(session_id), "data")
    assert b"secret" not in raw and b"public" in raw
