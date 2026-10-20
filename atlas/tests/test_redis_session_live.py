"""RedisSessionRepository against a real Redis server (issue #1091).

Skipped unless ``ATLAS_TEST_REDIS_URL`` names a disposable Redis, e.g.::

    podman run -d --rm -p 127.0.0.1:6391:6379 docker.io/library/redis:7-alpine
    ATLAS_TEST_REDIS_URL=redis://127.0.0.1:6391/15 bash test/atlas_tests.sh

Keys go under a random prefix and are deleted afterwards.
"""

import asyncio
import os
from uuid import uuid4

import pytest

from atlas.domain.errors import SessionConflictError
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.sessions.redis_repository import RedisSessionRepository

REDIS_URL = os.environ.get("ATLAS_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(not REDIS_URL, reason="ATLAS_TEST_REDIS_URL not set")


@pytest.fixture
async def replicas():
    prefix = f"atlas-test-{uuid4().hex[:8]}"
    managers = [
        RedisClientManager(RedisSettings(url=REDIS_URL, key_prefix=prefix, max_connections=4))
        for _ in range(2)
    ]
    for manager in managers:
        await manager.start()
    yield [RedisSessionRepository(m, ttl_seconds=60) for m in managers]
    keys = [k async for k in managers[0].client.scan_iter(match=f"{prefix}:*")]
    if keys:
        await managers[0].client.delete(*keys)
    for manager in managers:
        await manager.close()


@pytest.mark.asyncio
async def test_round_trip_ttl_and_conflict(replicas):
    repo_a, repo_b = replicas
    session = Session(id=uuid4(), user_email="alice@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="hello"))
    await repo_a.create(session)

    copy = await repo_b.get(session.id)
    assert copy.history.messages[0].content == "hello"
    ttl = await repo_a._redis.client.ttl(repo_a._key(session.id))
    assert 0 < ttl <= 60

    copy.context["by"] = "b"
    await repo_b.update(copy)
    session.context["by"] = "a"
    with pytest.raises(SessionConflictError):
        await repo_a.update(session)
    assert (await repo_a.get(session.id)).context["by"] == "b"


@pytest.mark.asyncio
async def test_racing_replicas_never_both_win(replicas):
    """Two replicas write the same version at once: exactly one succeeds."""
    repo_a, repo_b = replicas
    session = Session(id=uuid4(), user_email="alice@example.com")
    await repo_a.create(session)

    for _ in range(20):
        copy_a = await repo_b.get(session.id)  # fresh copies, same version
        copy_b = await repo_b.get(session.id)
        repo_a._live.pop(session.id, None)
        results = await asyncio.gather(
            repo_a.update(copy_a), repo_b.update(copy_b), return_exceptions=True
        )
        wins = [r for r in results if not isinstance(r, Exception)]
        losses = [r for r in results if isinstance(r, SessionConflictError)]
        assert len(wins) == 1 and len(losses) == 1, results
        repo_b._live.pop(session.id, None)
