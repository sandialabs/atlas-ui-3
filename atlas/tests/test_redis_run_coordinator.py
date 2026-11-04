"""Cluster-wide run admission through Redis (issue #1128).

The scripts run for real under fakeredis's Lua engine. Two coordinators with
different replica ids on one fake server stand in for two replicas.
"""

import asyncio

import fakeredis
import pytest

from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError
from atlas.application.chat.runs.registry import ConcurrencyLimitError, ConversationBusyError
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator, key_part

USER = "Alice@Example.com"


def _coordinator(server, replica_id, **kwargs):
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0", key_prefix="t"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    return RedisRunCoordinator(manager, replica_id=replica_id, **kwargs)


@pytest.fixture
def server():
    return fakeredis.FakeServer()


async def _claim(coord, run_id, conversation_id="conv-1", limit=5, user=USER):
    return await coord.claim(
        run_id=run_id, conversation_id=conversation_id, user_email=user, max_runs_per_user=limit
    )


async def _user_slots(coord, user=USER):
    return await coord._redis.client.zcard(coord.keys.user(user))


async def test_claim_writes_the_contract_record(server):
    a = _coordinator(server, "replica-a")
    claim = await _claim(a, "run-1")

    assert claim.owner == "replica-a"
    assert claim.user_email == "alice@example.com"
    assert claim.generation >= 1
    record = await a.read("run-1")
    assert record["run_id"] == "run-1"
    assert record["conversation_id"] == "conv-1"
    assert record["user_email"] == "alice@example.com"
    assert record["owner"] == "replica-a"
    assert int(record["generation"]) == claim.generation
    assert record["status"] == "queued"
    assert int(record["lease_expires_at"]) == int(claim.lease_expires_at * 1000)


async def test_busy_conversation_refused_on_another_replica(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    await _claim(a, "run-1", "conv-1")

    with pytest.raises(ConversationBusyError):
        await _claim(b, "run-2", "conv-1")
    # The refusal took nothing: no record, no slot.
    assert await b.read("run-2") is None
    assert await _user_slots(b) == 1


async def test_user_cap_is_global(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    await _claim(a, "run-1", "conv-1", limit=2)
    await _claim(b, "run-2", "conv-2", limit=2)

    with pytest.raises(ConcurrencyLimitError):
        await _claim(a, "run-3", "conv-3", limit=2)
    assert await a.read("run-3") is None
    assert await _user_slots(a) == 2
    # The refused conversation was not locked either.
    assert await a._redis.client.get(a.keys.conversation(USER, "conv-3")) is None


async def test_concurrent_claims_across_replicas_never_exceed_the_cap(server):
    replicas = [_coordinator(server, name) for name in "abcd"]

    async def attempt(i):
        try:
            return await _claim(replicas[i % 4], f"run-{i}", f"conv-{i}", limit=3)
        except ConcurrencyLimitError:
            return None

    results = await asyncio.gather(*(attempt(i) for i in range(20)))
    assert len([r for r in results if r is not None]) == 3
    assert await _user_slots(replicas[0]) == 3


async def test_concurrent_claims_for_one_conversation_admit_one(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")

    async def attempt(coord, run_id):
        try:
            return await _claim(coord, run_id, "conv-x")
        except ConversationBusyError:
            return None

    results = await asyncio.gather(*(attempt(a if i % 2 else b, f"run-{i}") for i in range(10)))
    assert len([r for r in results if r is not None]) == 1


async def test_release_frees_both_locks_and_keeps_the_outcome(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    claim = await _claim(a, "run-1", "conv-1", limit=1)

    assert await a.release(claim, "completed") is True
    assert await _user_slots(a) == 0
    record = await a.read("run-1")
    assert record["status"] == "completed"
    assert record["lease_expires_at"] == "0"
    # Both the conversation and the slot are free for any replica.
    assert await _claim(b, "run-2", "conv-1", limit=1)


async def test_generations_increase(server):
    a = _coordinator(server, "a")
    first = await _claim(a, "run-1", "conv-1")
    await a.release(first, "completed")
    second = await _claim(a, "run-2", "conv-1")
    assert second.generation > first.generation


async def test_release_by_another_owner_or_generation_is_refused(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    claim = await _claim(a, "run-1", "conv-1")

    # Same run id, wrong owner: B must not free A's slot.
    assert await b.release(claim, "failed") is False
    stale = type(claim)(**{**claim.__dict__, "generation": claim.generation - 1})
    assert await a.release(stale, "failed") is False
    assert await _user_slots(a) == 1
    assert (await a.read("run-1"))["status"] == "queued"


async def test_expired_lease_frees_the_slot(server):
    a = _coordinator(server, "a", lease_seconds=0.2)
    b = _coordinator(server, "b", lease_seconds=0.2)
    await _claim(a, "run-1", "conv-1", limit=1)

    with pytest.raises(ConcurrencyLimitError):
        await _claim(b, "run-2", "conv-2", limit=1)
    await asyncio.sleep(0.35)
    # A died without releasing; its lease ran out, so B gets the slot and the
    # conversation.
    assert await _claim(b, "run-3", "conv-1", limit=1)


async def test_renew_extends_and_reports_held(server):
    a = _coordinator(server, "a", lease_seconds=0.3)
    claim = await _claim(a, "run-1", "conv-1", limit=1)
    for _ in range(3):
        await asyncio.sleep(0.15)
        assert await a.renew([claim], {"run-1": "running"}) == {"run-1": True}
    # Still held after more than one lease thanks to renewal.
    with pytest.raises(ConcurrencyLimitError):
        await _claim(_coordinator(server, "b", lease_seconds=0.3), "run-2", "conv-2", limit=1)
    assert (await a.read("run-1"))["status"] == "running"


async def test_renew_after_release_does_not_resurrect_the_claim(server):
    """A heartbeat that raced the release must not take the slot back."""
    a = _coordinator(server, "a")
    claim = await _claim(a, "run-1", "conv-1", limit=1)
    await a.release(claim, "completed")

    assert await a.renew([claim], {"run-1": "running"}) == {"run-1": False}
    assert await _user_slots(a) == 0
    assert await a._redis.client.get(a.keys.conversation(USER, "conv-1")) is None


async def test_renew_reports_a_lost_lease(server):
    a = _coordinator(server, "a", lease_seconds=0.2)
    b = _coordinator(server, "b", lease_seconds=0.2)
    claim = await _claim(a, "run-1", "conv-1")
    await asyncio.sleep(0.3)
    await _claim(b, "run-2", "conv-1")

    assert await a.renew([claim], {}) == {"run-1": False}


async def test_unreachable_redis_fails_closed():
    class _Down:
        def register_script(self, _source):
            async def _call(**_kwargs):
                raise ConnectionError("down")
            return _call

    manager = RedisClientManager(RedisSettings(url="redis://fake/0"), client=_Down())
    coord = RedisRunCoordinator(manager, replica_id="a")
    with pytest.raises(RunCoordinationUnavailableError):
        await _claim(coord, "run-1")
    assert manager.stats.command_errors == 1


def test_key_parts_cannot_collide():
    assert key_part("a:b@x") != key_part("a")
    assert key_part("a:b@x") + ":" + key_part("c") != key_part("a") + ":" + key_part("b@x:c")
    assert key_part("100%") == "100%25"


async def test_renew_after_a_lapse_does_not_retake_the_slot(server):
    """A lease that lapsed is lost even if nothing took the conversation: its slot
    may already count for another run, so re-taking it could exceed the cap."""
    a = _coordinator(server, "a", lease_seconds=0.2)
    lapsed = await _claim(a, "run-1", "conv-1", limit=1)
    await asyncio.sleep(0.3)
    fresh = await _claim(a, "run-2", "conv-2", limit=1)

    assert await a.renew([lapsed], {}) == {"run-1": False}
    assert await _user_slots(a) == 1
    assert await a.renew([fresh], {}) == {"run-2": True}
