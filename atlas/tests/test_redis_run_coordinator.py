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
    assert await a._redis.client.get(a.keys.conversation("conv-3")) is None


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


async def test_generations_keep_increasing_across_a_redis_flush(server):
    """A flushed or restarted Redis restarts the counter; the floor at Redis
    time keeps later generations above every earlier one, including a burst
    of claims just before the flush."""
    a = _coordinator(server, "a")
    burst = await asyncio.gather(
        *(_claim(a, f"run-{i}", f"conv-{i}", limit=100) for i in range(50))
    )
    highest = max(c.generation for c in burst)
    assert len({c.generation for c in burst}) == 50
    await a._redis.client.flushall()
    after = await _claim(a, "run-after", "conv-after")
    assert after.generation > highest
    assert (await _claim(a, "run-next", "conv-next")).generation > after.generation


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
    assert await a._redis.client.get(a.keys.conversation("conv-1")) is None


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


async def test_conversation_lock_is_exclusive_across_users(server):
    """Conversation ids are global in storage: another user may not run the same id."""
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    await _claim(a, "run-1", "conv-shared", user="one@example.com")
    with pytest.raises(ConversationBusyError):
        await _claim(b, "run-2", "conv-shared", user="two@example.com")
    assert await _user_slots(b, user="two@example.com") == 0


async def test_lost_reply_after_an_applied_claim_is_cleaned_up(server):
    """The script ran but the reply never arrived: the refusal must hold no slot."""
    a = _coordinator(server, "a")
    real_claim = a._claim

    async def applied_then_lost(**kwargs):
        await real_claim(**kwargs)
        raise TimeoutError("reply lost")

    a._claim = applied_then_lost
    with pytest.raises(RunCoordinationUnavailableError):
        await _claim(a, "run-1", "conv-1", limit=1)
    assert await _user_slots(a) == 0
    assert await a._redis.client.get(a.keys.conversation("conv-1")) is None
    a._claim = real_claim
    assert await _claim(a, "run-2", "conv-1", limit=1)


async def test_renewal_runs_concurrently(server):
    a = _coordinator(server, "a")
    claims = [await _claim(a, f"run-{i}", f"conv-{i}", limit=50) for i in range(40)]
    real_renew, in_flight, peak = a._renew, 0, 0

    async def slow(**kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return await real_renew(**kwargs)

    a._renew = slow
    held = await a.renew(claims, {})
    assert all(held.values()) and len(held) == 40
    assert 1 < peak <= 16


def test_replica_id_is_stable_for_the_process():
    from atlas.application.chat.runs.coordination import default_replica_id

    assert default_replica_id() == default_replica_id()
    m = RedisClientManager(RedisSettings(url="redis://fake/0"), client=fakeredis.FakeAsyncRedis())
    assert RedisRunCoordinator(m).replica_id == RedisRunCoordinator(m).replica_id


async def test_claim_writes_the_routing_fields_from_admission(server):
    a = _coordinator(server, "replica-a")
    parent = await _claim(a, "parent")
    await a.claim(
        run_id="child", conversation_id="conv-2", user_email=USER, max_runs_per_user=5,
        source="agent_api", parent_run_id="parent", follow_up_of="earlier",
    )
    record = await a.read("child")
    assert (record["source"], record["parent_run_id"], record["follow_up_of"]) == (
        "agent_api", "parent", "earlier")
    assert await a.read("parent") is not None and parent.run_id == "parent"
    plain = await a.read("parent")
    assert (plain["source"], plain["parent_run_id"], plain["follow_up_of"]) == ("", "", "")


async def test_parent_record_outlives_its_children(server):
    a = _coordinator(server, "replica-a", lease_seconds=30, retention_seconds=100)
    client = a._redis.client
    parent = await _claim(a, "parent", "conv-p")
    child = await a.claim(run_id="child", conversation_id="conv-c", user_email=USER,
                          max_runs_per_user=5, parent_run_id="parent")
    grandchild = await a.claim(run_id="grand", conversation_id="conv-g", user_email=USER,
                               max_runs_per_user=5, parent_run_id="child")
    pkey, ckey = a.keys.run("parent"), a.keys.run("child")

    # The parent finishes first: with live descendants its record is kept for
    # lease + retention, not retention alone.
    assert await a.release(parent, "completed")
    assert await client.pttl(pkey) > 100_000

    # The child finishing keeps both ancestors while the grandchild is live,
    # and the grandchild's heartbeat refreshes the finished ancestors.
    assert await a.release(child, "completed")
    assert await client.pttl(ckey) > 100_000 and await client.pttl(pkey) > 100_000
    await client.pexpire(pkey, 5_000)
    assert (await a.renew([grandchild], {"grand": "running"}))["grand"] is True
    assert await client.pttl(pkey) > 100_000
    assert (await a.read("parent"))["status"] == "completed"

    # The last descendant ending starts the retention window up the chain.
    assert await a.release(grandchild, "completed")
    for key in (pkey, ckey, a.keys.run("grand")):
        assert 0 < await client.pttl(key) <= 100_000


async def test_parent_record_expires_normally_without_live_children(server):
    a = _coordinator(server, "replica-a", lease_seconds=30, retention_seconds=100)
    parent = await _claim(a, "parent", "conv-p")
    assert await a.release(parent, "completed")
    assert 0 < await a._redis.client.pttl(a.keys.run("parent")) <= 100_000


def _api_spec(tools):
    return {
        "source": "agent_runs_api", "model": "model", "workspace_id": "ws",
        "tools": tools, "data_sources": [], "requested_data_sources": [],
        "compliance_level": None,
    }


async def _api_claim(coord, run_id, previous=None, tools=None, user=USER):
    claim = await coord.claim(
        run_id=run_id, conversation_id="api-conv", user_email=user, max_runs_per_user=5,
        source="agent_runs_api", follow_up_of=previous,
        api_continuation=_api_spec(["read"] if tools is None else tools),
    )
    await coord.publish_api_admission(claim)
    return claim


async def test_api_predecessor_is_checked_atomically_even_after_newer_turn_finishes(server):
    import json

    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    first = await _api_claim(a, "first", tools=["read", "write"])
    await a.release(first, "completed")
    observed = await b.read_api_continuation("api-conv")
    assert observed["run_id"] == "first"
    second = await _api_claim(a, "second", previous="first")
    await a.release(second, "failed")

    # B read first before A's narrower turn. Even with no active holder,
    # the old predecessor cannot authorize a new claim.
    with pytest.raises(ConversationBusyError):
        await _api_claim(b, "stale", previous=observed["run_id"], tools=["read", "write"])
    assert await b.read("stale") is None
    latest = await b.read_api_continuation("api-conv")
    assert latest["run_id"] == "second"
    assert latest["api_completed_before"] == "1"
    assert json.loads(latest["api_spec"])["tools"] == ["read"]
    third = await _api_claim(b, "third", previous="second")
    await b.release(third, "completed")


async def test_api_continuation_fails_closed_when_latest_missing_or_wrong_user(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    first = await _api_claim(a, "first")
    await a.release(first, "completed")
    with pytest.raises(RunCoordinationUnavailableError):
        await _api_claim(b, "wrong-user", previous="first", user="other@example.com")
    await a._redis.client.delete(a.keys.api_latest("api-conv"))
    with pytest.raises(RunCoordinationUnavailableError):
        await _api_claim(b, "missing", previous="first")
    assert await b.read("wrong-user") is None
    assert await b.read("missing") is None


async def test_api_latest_without_a_completed_turn_is_not_continuable(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    first = await _api_claim(a, "first")
    await a.release(first, "failed")
    with pytest.raises(RunCoordinationUnavailableError):
        await _api_claim(b, "next", previous="first")
    assert await b.read("next") is None


async def test_api_index_retains_terminal_runs_but_excludes_browser_runs(server):
    a, b = _coordinator(server, "a"), _coordinator(server, "b")
    first = await _api_claim(a, "first")
    await _claim(b, "browser", conversation_id="browser-conv")
    assert {r["run_id"] for r in await b.read_api_runs()} == {"first"}
    await a.release(first, "completed")
    assert {r["run_id"] for r in await b.read_api_runs()} == {"first"}
    await a._redis.client.delete(a.keys.run("first"))
    assert await b.read_api_runs() == []


async def test_api_latest_and_index_expiry_follow_run_renewal_and_release(server):
    a = _coordinator(server, "a", lease_seconds=5, retention_seconds=10)
    first = await _api_claim(a, "first")
    client = a._redis.client
    latest_key = a.keys.api_latest("api-conv")
    run_key = a.keys.run("first")
    assert 14000 < await client.pttl(latest_key) <= 15000
    assert await a.renew([first], {"first": "running"}) == {"first": True}
    assert abs(await client.pttl(latest_key) - await client.pttl(run_key)) < 100
    await a.release(first, "completed")
    assert 9000 < await client.pttl(latest_key) <= 10000
    assert abs(await client.pttl(latest_key) - await client.pttl(run_key)) < 100
    seconds, micros = await client.time()
    expires = await client.zscore(a.keys.api_index(), "first")
    assert 9000 < expires - (seconds * 1000 + micros // 1000) <= 10000


async def test_api_user_index_is_scoped_normalized_and_expires_with_records(server):
    a = _coordinator(server, "a", lease_seconds=5, retention_seconds=10)
    first = await _api_claim(a, "first")
    second = await a.claim(
        run_id="other", conversation_id="other-conv", user_email="other@example.com",
        max_runs_per_user=5, source="agent_runs_api", api_continuation=_api_spec(["read"]),
    )
    await a.publish_api_admission(second)
    assert {r["run_id"] for r in await a.read_api_runs()} == {"first", "other"}
    assert {r["run_id"] for r in await a.read_api_runs("ALICE@example.com")} == {"first"}
    assert {r["run_id"] for r in await a.read_api_runs("other@example.com")} == {"other"}
    assert await a.read_api_runs("missing@example.com") == []
    await a.release(first, "completed")
    await a.release(second, "completed")
    client = a._redis.client
    for index in (a.keys.api_index(), a.keys.api_index(USER), a.keys.api_index("other@example.com")):
        assert 9000 < await client.pttl(index) <= 10000



async def test_api_latest_retention_covers_older_retained_run(server):
    a = _coordinator(server, "a", lease_seconds=5, retention_seconds=10)
    first = await _api_claim(a, "first")
    await a.release(first, "completed")
    second = await _api_claim(a, "second", previous="first")
    # Simulate a longer-lived descendant of the older run, which refreshes
    # the ancestor's retention independently of the newest API turn.
    child = await a.claim(run_id="child", conversation_id="child-conv", user_email=USER,
                          max_runs_per_user=5, parent_run_id="first")
    await a.renew([child], {"child": "running"})
    await a.release(second, "completed")
    client = a._redis.client
    first_ttl = await client.pttl(a.keys.run("first"))
    assert first_ttl > 10000
    assert abs(await client.pttl(a.keys.api_latest("api-conv")) - first_ttl) < 100
    assert abs(await client.pttl(a.keys.run("second")) - first_ttl) < 100
    assert (await a.read_api_continuation("api-conv"))["run_id"] == "second"
