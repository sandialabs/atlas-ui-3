"""RedisSessionRepository: storage, versioning, identity and outages (issue #1091).

Two repositories over one fake Redis server stand in for two API replicas
sharing a Redis deployment.
"""

from uuid import uuid4

import fakeredis
import pytest

from atlas.domain.errors import (
    SessionConflictError,
    SessionNotFoundError,
    SessionStoreUnavailableError,
)
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.sessions.factory import create_session_repository
from atlas.infrastructure.sessions.redis_repository import RedisSessionRepository


def _replica(server, prefix="atlas", ttl=3600):
    client = fakeredis.FakeAsyncRedis(server=server)
    manager = RedisClientManager(
        RedisSettings(url="redis://fake:6379/0", key_prefix=prefix), client=client
    )
    return manager, RedisSessionRepository(manager, ttl_seconds=ttl)


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def _session(user="alice@example.com"):
    s = Session(id=uuid4(), user_email=user)
    s.history.add_message(Message(role=MessageRole.USER, content="hello"))
    s.history.add_message(Message(role=MessageRole.ASSISTANT, content="hi there"))
    s.context.update({
        "conversation_id": "conv-1",
        "files": {"report.pdf": {"key": "users/alice/report.pdf", "size": 12}},
        "workspace_id": "ws-1",
    })
    return s


@pytest.mark.asyncio
async def test_create_stores_namespaced_record_with_ttl(server):
    manager, repo = _replica(server, prefix="atlas-test")
    session = await repo.create(_session())

    key = f"atlas-test:session:{session.id}"
    assert await manager.client.exists(key)
    ttl = await manager.client.ttl(key)
    assert 0 < ttl <= 3600
    assert session.store_version == 1


@pytest.mark.asyncio
async def test_live_session_keeps_identity_on_this_replica(server):
    _, repo = _replica(server)
    session = await repo.create(_session())

    assert await repo.get(session.id) is session
    session.context["mutated"] = True
    assert (await repo.get(session.id)).context["mutated"] is True


@pytest.mark.asyncio
async def test_other_replica_reads_a_full_copy(server):
    _, repo_a = _replica(server)
    _, repo_b = _replica(server)
    original = await repo_a.create(_session())

    copy = await repo_b.get(original.id)

    assert copy is not original
    assert copy.user_email == "alice@example.com"
    assert [m.content for m in copy.history.messages] == ["hello", "hi there"]
    assert copy.context["files"]["report.pdf"]["key"] == "users/alice/report.pdf"
    assert copy.context["conversation_id"] == "conv-1"
    assert copy.store_version == original.store_version


@pytest.mark.asyncio
async def test_update_bumps_version_and_is_visible_elsewhere(server):
    _, repo_a = _replica(server)
    _, repo_b = _replica(server)
    session = await repo_a.create(_session())

    session.context["workspace_id"] = "ws-2"
    await repo_a.update(session)

    assert session.store_version == 2
    assert (await repo_b.get(session.id)).context["workspace_id"] == "ws-2"


@pytest.mark.asyncio
async def test_stale_copy_cannot_overwrite_newer_state(server):
    """The concurrent-update guard: replica A's stale copy loses to B's write."""
    _, repo_a = _replica(server)
    _, repo_b = _replica(server)
    session_a = await repo_a.create(_session())

    session_b = await repo_b.get(session_a.id)
    session_b.context["workspace_id"] = "written-by-b"
    await repo_b.update(session_b)

    session_a.context["workspace_id"] = "stale-write-from-a"
    with pytest.raises(SessionConflictError):
        await repo_a.update(session_a)

    stored = await _replica(server)[1].get(session_a.id)
    assert stored.context["workspace_id"] == "written-by-b"
    # The losing replica dropped its stale working copy and now reads the
    # stored one.
    assert (await repo_a.get(session_a.id)).context["workspace_id"] == "written-by-b"


@pytest.mark.asyncio
async def test_concurrent_writes_from_one_replica_do_not_conflict(server):
    import asyncio

    _, repo = _replica(server)
    session = await repo.create(_session())

    async def write(i):
        session.context[f"k{i}"] = i
        await repo.update(session)

    await asyncio.gather(*(write(i) for i in range(10)))
    assert session.store_version == 11
    stored = await _replica(server)[1].get(session.id)
    assert all(stored.context[f"k{i}"] == i for i in range(10))


@pytest.mark.asyncio
async def test_inactive_session_leaves_the_local_map(server):
    _, repo = _replica(server)
    session = await repo.create(_session())
    session.active = False
    await repo.update(session)

    assert not repo.is_live_here(session.id)
    fresh = await repo.get(session.id)
    assert fresh is not session and fresh.active is False


@pytest.mark.asyncio
async def test_update_of_unknown_session_raises_not_found(server):
    _, repo = _replica(server)
    with pytest.raises(SessionNotFoundError):
        await repo.update(Session(id=uuid4(), user_email="a@example.com"))


@pytest.mark.asyncio
async def test_live_session_that_expired_is_stored_again(server):
    manager, repo = _replica(server)
    session = await repo.create(_session())
    await manager.client.delete(manager.key("session", str(session.id)))

    await repo.update(session)

    assert await manager.client.exists(manager.key("session", str(session.id)))
    assert manager.stats.extra["session_expired_while_live"] == 1


@pytest.mark.asyncio
async def test_delete_and_exists(server):
    _, repo_a = _replica(server)
    _, repo_b = _replica(server)
    session = await repo_a.create(_session())

    assert await repo_b.exists(session.id)
    assert await repo_a.delete(session.id) is True
    assert not await repo_b.exists(session.id)
    assert await repo_a.get(session.id) is None
    assert await repo_a.delete(session.id) is False


@pytest.mark.asyncio
async def test_process_local_values_are_not_stored(server):
    _, repo_a = _replica(server)
    _, repo_b = _replica(server)
    session = _session()
    session.context["citation_register"] = object()
    session.context["some_handle"] = object()
    await repo_a.create(session)

    copy = await repo_b.get(session.id)
    assert "citation_register" not in copy.context
    assert "some_handle" not in copy.context
    assert copy.context["conversation_id"] == "conv-1"
    # The live copy on the writing replica keeps its runtime handles.
    assert "some_handle" in (await repo_a.get(session.id)).context


@pytest.mark.asyncio
async def test_incognito_messages_are_never_written(server):
    manager, repo = _replica(server)
    session = _session()
    session.context["incognito"] = True
    await repo.create(session)

    raw = await manager.client.hget(manager.key("session", str(session.id)), "data")
    assert b"hello" not in raw and b"hi there" not in raw
    copy = await _replica(server)[1].get(session.id)
    assert copy.history.messages == []
    assert copy.context["incognito"] is True


@pytest.mark.asyncio
async def test_prefixes_isolate_deployments(server):
    _, repo_one = _replica(server, prefix="deploy-one")
    _, repo_two = _replica(server, prefix="deploy-two")
    session = await repo_one.create(_session())
    assert await repo_two.get(session.id) is None


@pytest.mark.asyncio
async def test_corrupt_record_reads_as_missing(server):
    manager, repo = _replica(server)
    sid = uuid4()
    await manager.client.hset(
        manager.key("session", str(sid)), mapping={"data": b"{not json", "version": 1}
    )
    assert await repo.get(sid) is None
    assert manager.stats.extra["session_decode_errors"] == 1


@pytest.mark.asyncio
async def test_outage_fails_loudly_without_fallback(server):
    manager, repo = _replica(server)
    live = await repo.create(_session())
    server.connected = False

    with pytest.raises(SessionStoreUnavailableError):
        await repo.get(uuid4())
    with pytest.raises(SessionStoreUnavailableError):
        await repo.create(_session())
    with pytest.raises(SessionStoreUnavailableError):
        await repo.update(live)
    assert manager.stats.command_errors >= 3

    # The working copy of a session already live here keeps answering.
    assert await repo.get(live.id) is live
    health = await manager.health()
    assert health["status"] == "unavailable"

    server.connected = True
    await repo.update(live)
    assert (await manager.health())["status"] == "ok"


def test_factory_builds_redis_repository(server):
    manager, _ = _replica(server)
    repo = create_session_repository("redis", redis=manager, session_ttl_seconds=120)
    assert isinstance(repo, RedisSessionRepository)
    assert repo.stats()["ttl_seconds"] == 120


def test_factory_requires_redis_for_redis_type():
    with pytest.raises(ValueError, match="REDIS_URL"):
        create_session_repository("redis")


def test_factory_still_rejects_unknown_types():
    with pytest.raises(ValueError, match="Unknown SESSION_REPOSITORY_TYPE"):
        create_session_repository("dynamo")
