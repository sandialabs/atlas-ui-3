"""Idempotency store: in-process and Redis implementations (#1220)."""

import asyncio

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError
from atlas.application.chat.runs.idempotency import (
    IDEMPOTENCY_PENDING_TTL_SECONDS,
    IDEMPOTENCY_TTL_SECONDS,
    InProcessIdempotencyStore,
)
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.idempotency_store import RedisIdempotencyStore

USER = "Alice@Example.com"
HANDLE = {"run_id": "r1", "conversation_id": "c1", "status": "queued"}


def _manager(server):
    return RedisClientManager(
        RedisSettings(url="redis://fake/0"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )


async def _boom(*args, **kwargs):
    raise RedisConnectionError("down")


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture(params=["in_process", "redis"])
def make_store(request, server):
    """Builds a store; Redis stores built here share one server (two replicas)."""
    shared = InProcessIdempotencyStore()

    def build():
        if request.param == "in_process":
            return shared
        return RedisIdempotencyStore(_manager(server))

    return build


async def test_first_reserve_wins_and_second_sees_pending(make_store):
    a, b = make_store(), make_store()
    slot, existing = await a.reserve(USER, "k1", "fp")
    assert slot is not None and existing is None
    slot2, existing2 = await b.reserve(USER, "k1", "fp")
    assert slot2 is None
    assert existing2.fingerprint == "fp"
    assert existing2.handle is None  # still admitting


async def test_complete_makes_the_handle_visible_to_the_other_replica(make_store):
    a, b = make_store(), make_store()
    slot, _ = await a.reserve(USER, "k2", "fp")
    await a.complete(slot, HANDLE)
    _, existing = await b.reserve(USER.lower(), "k2", "fp")
    assert existing.handle == HANDLE


async def test_concurrent_reservations_have_one_winner(make_store):
    stores = [make_store() for _ in range(8)]
    results = await asyncio.gather(*(s.reserve(USER, "race", "fp") for s in stores))
    assert sum(1 for slot, _ in results if slot is not None) == 1
    assert sum(1 for _, entry in results if entry is not None) == 7


async def test_a_different_body_sees_the_original_fingerprint(make_store):
    a, b = make_store(), make_store()
    await a.reserve(USER, "k6", "fp-one")
    slot, existing = await b.reserve(USER, "k6", "fp-two")
    assert slot is None
    assert existing.fingerprint == "fp-one"


async def test_keys_are_scoped_per_user(make_store):
    store = make_store()
    s1, _ = await store.reserve("a@x.com", "same", "fp")
    s2, _ = await store.reserve("b@x.com", "same", "fp")
    assert s1 is not None and s2 is not None


async def test_a_key_with_colons_does_not_collide(make_store):
    store = make_store()
    s1, _ = await store.reserve("a@x.com", "b:c", "fp")
    s2, _ = await store.reserve("a@x.com", "b", "fp")
    assert s1 is not None and s2 is not None


async def test_release_frees_a_pending_key_for_a_retry(make_store):
    a, b = make_store(), make_store()
    slot, _ = await a.reserve(USER, "k3", "fp")
    await a.release(slot)
    retry, existing = await b.reserve(USER, "k3", "fp")
    assert retry is not None and existing is None


async def test_release_never_drops_a_completed_record(make_store):
    store = make_store()
    slot, _ = await store.reserve(USER, "k4", "fp")
    await store.complete(slot, HANDLE)
    await store.release(slot)
    _, existing = await store.reserve(USER, "k4", "fp")
    assert existing.handle == HANDLE


async def test_a_stale_slot_cannot_release_a_newer_reservation(make_store):
    store = make_store()
    old, _ = await store.reserve(USER, "k5", "fp")
    await store.release(old)
    new, _ = await store.reserve(USER, "k5", "fp")
    await store.release(old)  # the old owner's late cleanup
    again, existing = await store.reserve(USER, "k5", "fp")
    assert new is not None and again is None and existing is not None


async def test_find_follow_up_by_addressed_run(make_store):
    a, b = make_store(), make_store()
    slot, _ = await a.reserve(
        USER, "fu", "fp", addressed_run_id="run-9", conversation_id="conv-9"
    )
    await a.complete(slot, HANDLE)
    owner, entry = await b.find_follow_up("run-9", "fu")
    assert owner == "alice@example.com"
    assert entry.conversation_id == "conv-9" and entry.handle == HANDLE
    assert await b.find_follow_up("run-other", "fu") is None


async def test_released_follow_up_leaves_no_index(make_store):
    store = make_store()
    slot, _ = await store.reserve(USER, "fu2", "fp", addressed_run_id="run-8")
    await store.release(slot)
    assert await store.find_follow_up("run-8", "fu2") is None


async def test_redis_ttls_pending_then_full(server):
    store = RedisIdempotencyStore(_manager(server))
    client = store._redis.client
    slot, _ = await store.reserve(USER, "ttl", "fp")
    record = store._record_key(USER, "ttl")
    pending = await client.pttl(record)
    assert 0 < pending <= IDEMPOTENCY_PENDING_TTL_SECONDS * 1000
    await store.complete(slot, HANDLE)
    full = await client.pttl(record)
    assert full > (IDEMPOTENCY_TTL_SECONDS - 60) * 1000
    assert IDEMPOTENCY_TTL_SECONDS >= 24 * 60 * 60  # the in-memory retention


async def test_redis_pending_reservation_expires_after_a_crash(server):
    """A replica that reserved and died must not block the key forever."""
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(USER, "crash", "fp")
    assert slot is not None
    await store._redis.client.pexpire(store._record_key(USER, "crash"), 1)
    await asyncio.sleep(0.05)
    retry, existing = await store.reserve(USER, "crash", "fp")
    assert retry is not None and existing is None


async def test_redis_late_complete_after_expiry_keeps_the_handle(server):
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(USER, "slow", "fp")
    await store._redis.client.delete(store._record_key(USER, "slow"))
    await store.complete(slot, HANDLE)
    _, existing = await store.reserve(USER, "slow", "fp")
    assert existing.handle == HANDLE and existing.fingerprint == "fp"


async def test_redis_complete_does_not_overwrite_another_owner(server):
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(USER, "own", "fp")
    await store._redis.client.delete(store._record_key(USER, "own"))
    other, _ = await store.reserve(USER, "own", "fp")
    await store.complete(slot, {"run_id": "stale"})
    await store.complete(other, HANDLE)
    _, existing = await store.reserve(USER, "own", "fp")
    assert existing.handle == HANDLE


async def test_redis_down_fails_closed_on_reserve(server):
    store = RedisIdempotencyStore(_manager(server))
    store._reserve = _boom
    with pytest.raises(RunCoordinationUnavailableError):
        await store.reserve(USER, "down", "fp")


async def test_redis_down_fails_closed_on_follow_up_lookup(server):
    store = RedisIdempotencyStore(_manager(server))
    store._redis.client.get = _boom
    with pytest.raises(RunCoordinationUnavailableError):
        await store.find_follow_up("run-1", "k")


async def test_redis_down_on_complete_and_release_is_best_effort(server):
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(USER, "be", "fp")
    store._complete = _boom
    store._release = _boom
    assert await store.complete(slot, HANDLE) is False  # reported, not raised
    await store.release(slot)


async def test_redis_renew_extends_only_our_pending_reservation(server):
    store = RedisIdempotencyStore(_manager(server))
    client = store._redis.client
    record = store._record_key(USER, "renew")
    slot, _ = await store.reserve(USER, "renew", "fp")
    await client.pexpire(record, 5000)
    await store.renew(slot)
    assert await client.pttl(record) > (IDEMPOTENCY_PENDING_TTL_SECONDS - 5) * 1000
    # Not ours any more: left alone.
    await client.delete(record)
    other, _ = await store.reserve(USER, "renew", "fp")
    await client.pexpire(record, 5000)
    await store.renew(slot)
    assert await client.pttl(record) <= 5000
    # Completed: the long TTL is not shortened back to the pending one.
    await store.complete(other, HANDLE)
    await store.renew(other)
    assert await client.pttl(record) > (IDEMPOTENCY_TTL_SECONDS - 60) * 1000


async def test_redis_renew_failure_is_best_effort(server):
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(USER, "renew2", "fp")
    store._renew = _boom
    await store.renew(slot)  # must not raise


async def test_find_follow_up_by_conversation_names_a_sibling_run(make_store):
    a, b = make_store(), make_store()
    slot, _ = await a.reserve(
        USER, "sib", "fp", addressed_run_id="run-1", conversation_id="conv-1"
    )
    await a.complete(slot, HANDLE)
    assert await b.find_follow_up("run-2", "sib") is None
    owner, entry = await b.find_follow_up("run-2", "sib", "conv-1")
    assert owner == "alice@example.com" and entry.handle == HANDLE
    assert await b.find_follow_up("run-2", "sib", "conv-other") is None
    # A launch (addresses no run) is never found as a follow-up.
    launch, _ = await a.reserve(USER, "plain", "fp", conversation_id="conv-1")
    await a.complete(launch, HANDLE)
    assert await b.find_follow_up("run-2", "plain", "conv-1") is None


async def test_renew_reports_extended_lost_and_unreachable(make_store, server):
    store = make_store()
    slot, _ = await store.reserve(USER, "rn", "fp")
    assert await store.renew(slot) is True
    await store.release(slot)
    assert await store.renew(slot) is False
    if isinstance(store, RedisIdempotencyStore):
        store._renew = _boom
        assert await store.renew(slot) is None


async def test_redis_complete_reports_false_when_another_request_holds_the_key(server):
    store = RedisIdempotencyStore(_manager(server))
    stale, _ = await store.reserve(USER, "held", "fp")
    await store._redis.client.delete(store._record_key(USER, "held"))
    other, _ = await store.reserve(USER, "held", "fp")
    assert other is not None
    assert await store.complete(stale, {"run_id": "stale"}) is False
    assert await store.complete(other, HANDLE) is True


async def test_redis_find_follow_up_falls_back_to_the_conversation_index(server):
    store = RedisIdempotencyStore(_manager(server))
    slot, _ = await store.reserve(
        USER, "fb", "fp", addressed_run_id="run-1", conversation_id="conv-1"
    )
    await store.complete(slot, HANDLE)
    # The run index points at a record that is no longer there (another user's
    # key, since expired): the conversation index must still be tried.
    await store._redis.client.set(store._addr_key("run-2", "fb"), "gone@example.com")
    found = await store.find_follow_up("run-2", "fb", "conv-1")
    assert found is not None and found[1].handle == HANDLE


async def test_find_follow_up_is_scoped_to_the_owner_when_given(make_store):
    store = make_store()
    slot, _ = await store.reserve(
        USER, "own", "fp", addressed_run_id="run-1", conversation_id="conv-1"
    )
    await store.complete(slot, HANDLE)
    assert await store.find_follow_up("run-2", "own", "conv-1", "alice@example.com") is not None
    assert await store.find_follow_up("run-2", "own", "conv-1", "mallory@example.com") is None
