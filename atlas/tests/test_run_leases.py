"""Run leases, orphan sweep and fencing on top of the shared claims (#1128).

Two ``RedisRunCoordinator``s with different replica ids on one fake Redis
(the Lua scripts run for real under ``fakeredis[lua]``) stand in for two
replicas, each with its own process-local registry and lease guard. Lease
expiry is simulated by moving the record's deadline into the past, and a
frozen owner by advancing its guard's injected clock.
"""

import asyncio
import contextlib
from uuid import uuid4

import fakeredis
import pytest

from atlas.application.chat.runs import leases as leases_module
from atlas.application.chat.runs.coordination import (
    drain_pending_releases,
    set_release_gate,
    set_run_coordinator,
)
from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    invocation_id,
    public_projection,
    publish_execution_event,
    reset_execution_store,
    set_execution_store,
)
from atlas.application.chat.runs.external_runs import API_SOURCE, get_api_run_anywhere
from atlas.application.chat.runs.launcher import LaunchRefused
from atlas.application.chat.runs.leases import (
    INTERRUPTED_ERROR,
    RunFencedError,
    RunLeaseGuard,
    ensure_current_run_may_act,
    set_lease_guard,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus, reset_run_registry
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
from atlas.infrastructure.redis.run_leases import RedisRunLeases

EMAIL = "owner@example.com"
LEASE = 6.0


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Replica:
    def __init__(self, server, name, clock=None):
        manager = RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=server),
        )
        self.coordinator = RedisRunCoordinator(manager, replica_id=name, lease_seconds=LEASE)
        self.registry = RunRegistry()
        self.clock = clock or Clock()
        self.scripts = RedisRunLeases(self.coordinator)
        self.guard = RunLeaseGuard(self.coordinator, self.scripts, self.registry, clock=self.clock)
        self.registry.add_observer(self.guard.observe)
        self.client = manager.client
        self.keys = self.coordinator.keys

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)
            if self.guard._pending:
                await asyncio.gather(*list(self.guard._pending))

    async def start(self, source=API_SOURCE, conversation="conv-1"):
        run_id = str(uuid4())
        claim = await self.coordinator.claim(
            run_id=run_id, conversation_id=conversation, user_email=EMAIL, max_runs_per_user=5
        )
        record = self.registry.start(
            conversation_id=conversation, user_email=EMAIL, title="task", run_id=run_id
        )
        record.launch_spec = {"source": source, "model": "m", "tools": ["t"]}
        record.claim = claim  # as admit_run does; attach_task's change starts tracking
        task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        self.registry.attach_task(record.run_id, task)
        await self.settle()
        return record, task

    async def record(self, run_id):
        return await self.coordinator.read(run_id)

    async def expire(self, record):
        """What happens in Redis when the owner stops renewing."""
        await self.client.hset(self.keys.run(record.run_id), "lease_expires_at", 1)
        await self.client.delete(self.keys.conversation(record.conversation_id))
        await self.client.zadd(self.keys.user(EMAIL), {record.run_id: 1})
        await self.client.zadd(self.keys.live(), {record.run_id: 1})


@pytest.fixture(autouse=True)
def _isolation():
    reset_execution_store()
    reset_run_registry()
    set_lease_guard(None)
    set_run_coordinator(None)
    set_release_gate(None)
    yield
    set_lease_guard(None)
    set_run_coordinator(None)
    set_release_gate(None)
    reset_execution_store()
    reset_run_registry()


@pytest.fixture
def server():
    return fakeredis.FakeServer()


async def test_owner_mirrors_its_snapshot_and_the_release_ends_the_lease(server):
    a = Replica(server, "A")
    set_run_coordinator(a.coordinator)  # the registry's release goes here
    record, task = await a.start()

    doc = await a.record(record.run_id)
    assert doc["owner"] == "A"
    assert doc["status"] == "running"
    assert API_SOURCE in doc["snapshot"]
    assert await a.client.zscore(a.keys.live(), record.run_id) is not None

    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await a.settle()
    from atlas.application.chat.runs.coordination import drain_pending_releases

    await drain_pending_releases()
    view = await a.guard.read_record(record.run_id)
    assert view["status"] == "completed"
    assert view["result"] == "the answer"
    assert view["interrupted"] is False
    assert await a.client.zscore(a.keys.live(), record.run_id) is None
    assert not a.guard.is_fenced(record.run_id)
    task.cancel()


async def test_renewal_rescores_the_live_index(server):
    a = Replica(server, "A")
    record, task = await a.start()
    before = await a.client.zscore(a.keys.live(), record.run_id)
    await asyncio.sleep(0.01)
    held = await a.coordinator.renew([record.claim], {record.run_id: "running"})
    assert held == {record.run_id: True}
    assert await a.client.zscore(a.keys.live(), record.run_id) > before
    task.cancel()


async def test_sweep_leaves_a_run_whose_lease_is_alive(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    await a.client.zadd(a.keys.live(), {record.run_id: 1})  # stale score only

    assert await b.guard.sweep_once() == []
    assert (await a.record(record.run_id))["status"] == "running"
    task.cancel()


async def test_orphan_is_interrupted_under_a_new_generation_and_completed_rows_kept(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    first_generation = record.claim.generation

    def tool(call, **extra):
        return {
            "run_id": record.run_id, "conversation_id": "conv-1", "user_email": EMAIL,
            "step": 0, "tool_call_id": call, "tool_name": "side_effect",
            "invocation_id": invocation_id(record.run_id, 0, call), **extra,
        }

    await store.apply_event(tool("call_0", type="tool_complete", success=True, result="done"))
    await store.apply_event(tool("call_1", type="tool_start"))

    await a.expire(record)
    assert await b.guard.sweep_once() == [record.run_id]

    doc = await b.record(record.run_id)
    assert doc["status"] == "failed"
    assert doc["interrupted"] == "1"
    assert doc["error"] == INTERRUPTED_ERROR
    assert doc["owner"] == ""
    assert doc["interrupted_by"] == "B"
    generation = int(doc["generation"])
    assert generation > first_generation
    assert generation == int(await b.client.get(b.keys.fence()))
    assert await b.client.zscore(b.keys.live(), record.run_id) is None
    assert await b.client.zscore(b.keys.user(EMAIL), record.run_id) is None

    rows = {
        row["tool_call_id"]: row
        for row in public_projection(await store.get("conv-1", EMAIL))["invocations"]
    }
    assert rows["call_0"]["status"] == "completed"
    assert rows["call_0"]["result"] == "done"
    assert rows["call_1"]["status"] == "interrupted"

    assert await b.guard.sweep_once() == []  # the takeover happens once
    task.cancel()


async def test_stale_owner_is_refused_everywhere_and_stops_its_run(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    claim = record.claim
    await a.expire(record)
    await b.guard.sweep_once()
    before = await b.record(record.run_id)

    # The frozen owner wakes up and reports progress, then completion.
    a.registry.mark_detached(record.run_id)
    await a.settle()
    assert await a.coordinator.renew([claim], {record.run_id: "running"}) == {record.run_id: False}
    assert await a.coordinator.release(claim, "completed") is False

    assert await b.record(record.run_id) == before
    local = a.registry.get(record.run_id)
    assert local.fenced and local.status is RunStatus.FAILED
    assert local.error == INTERRUPTED_ERROR
    assert local.claim is None
    assert await a.guard.may_publish(record.run_id) is False
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_refused_heartbeat_fences_and_records_the_takeover(server):
    from atlas.application.chat.runs import admission
    from atlas.application.chat.runs import registry as registry_module

    a = Replica(server, "A")
    record, task = await a.start()
    set_run_coordinator(a.coordinator)
    set_lease_guard(a.guard)
    registry_module._registry = a.registry  # renew_live_claims reads the process registry
    a.guard.start()  # subscribes to admission's claim-lost listener
    await a.expire(record)

    await admission.renew_live_claims()
    await a.settle()
    await a.guard.stop()
    assert a.guard.is_fenced(record.run_id)
    doc = await a.record(record.run_id)
    assert doc["interrupted"] == "1" and int(doc["generation"]) > 0
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_frozen_owner_rechecks_before_acting_and_is_fenced(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()

    await a.guard.ensure_may_act(record.run_id)  # first check renews
    calls = []
    original = a.coordinator.renew

    async def counting(claims, statuses):
        calls.append(1)
        return await original(claims, statuses)

    a.coordinator.renew = counting
    await a.guard.ensure_may_act(record.run_id)  # inside the window: no round trip
    assert calls == []

    # SIGSTOP for longer than the lease: the clock moved on, B took over.
    a.clock.now += LEASE * 3
    await a.expire(record)
    await b.guard.sweep_once()
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)
    assert a.registry.get(record.run_id).fenced
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_frozen_owner_nobody_swept_yet_takes_its_own_run_over(server):
    a = Replica(server, "A")
    record, task = await a.start()
    a.clock.now += LEASE * 3
    await a.expire(record)

    assert await a.guard.may_publish(record.run_id) is False
    assert (await a.record(record.run_id))["interrupted"] == "1"
    task.cancel()


async def test_lapsed_but_unexpired_lease_is_renewed_not_fenced(server):
    a = Replica(server, "A")
    record, task = await a.start()
    a.clock.now += LEASE  # a slow heartbeat, but Redis still holds the claim
    await a.guard.ensure_may_act(record.run_id)
    assert not a.guard.is_fenced(record.run_id)
    assert a.guard._leases[record.run_id].verified_at == a.clock.now
    task.cancel()


async def test_unreachable_redis_after_the_lease_lapsed_fails_closed(server):
    a = Replica(server, "A")
    record, task = await a.start()

    await a.guard.ensure_may_act(record.run_id)  # first check: proves the claim

    async def unreachable(claims, statuses):
        return {}  # what renew() returns for a claim it could not reach

    a.coordinator.renew = unreachable
    await a.guard.ensure_may_act(record.run_id)  # still inside the window
    a.clock.now += LEASE
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)
    task.cancel()


async def test_fenced_owner_writes_nothing_to_the_projection(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a = Replica(server, "A")
    set_lease_guard(a.guard)
    record, task = await a.start()
    event = {
        "type": "tool_start", "run_id": record.run_id, "conversation_id": "conv-1",
        "user_email": EMAIL, "step": 0, "tool_call_id": "call_0", "tool_name": "x",
        "invocation_id": invocation_id(record.run_id, 0, "call_0"),
    }
    assert await publish_execution_event(event) is not None

    a.guard._fence_local(a.guard._leases[record.run_id], record.run_id, "test")
    late = dict(event, type="tool_complete", success=True, result="late")
    assert await publish_execution_event(late) is None
    rows = public_projection(await store.get("conv-1", EMAIL))["invocations"]
    assert rows[0]["status"] == "in_progress"
    task.cancel()


async def test_tool_guard_cancels_a_fenced_run(server):
    from atlas.application.chat.runs.context import set_current_run

    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(a.guard)
    record, task = await a.start()

    async def body():
        set_current_run(record.run_id, record.conversation_id)
        await ensure_current_run_may_act()
        a.clock.now += LEASE * 3
        await a.expire(record)
        await b.guard.sweep_once()
        await ensure_current_run_may_act()
        return "side effect ran"

    with pytest.raises(asyncio.CancelledError):
        await asyncio.get_running_loop().create_task(body())
    task.cancel()


async def test_no_guard_means_no_checks():
    from atlas.application.chat.runs.context import clear_current_run, set_current_run

    set_current_run("run-x", "conv-x")
    try:
        await ensure_current_run_may_act()
        assert await leases_module.may_publish_for_run("run-x") is True
    finally:
        clear_current_run()


async def test_non_owner_reads_the_shared_record_api_runs_only(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    browser_record, browser_task = await a.start(source=None, conversation="conv-2")
    set_lease_guard(b.guard)

    status = await get_api_run_anywhere(record.run_id)
    assert status["status"] == "running"
    assert status["user"] == EMAIL
    assert status["interrupted"] is False
    assert status["result_status"] == "pending"
    with pytest.raises(LaunchRefused):
        await get_api_run_anywhere(browser_record.run_id)

    await a.expire(record)
    await b.guard.sweep_once()
    status = await get_api_run_anywhere(record.run_id)
    assert status["status"] == "failed"
    assert status["interrupted"] is True
    assert status["error"] == INTERRUPTED_ERROR
    task.cancel()
    browser_task.cancel()


async def test_unreadable_shared_record_is_unavailable_not_missing(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    set_lease_guard(b.guard)

    async def blip(run_id):
        raise ConnectionError("redis went away")

    b.coordinator.read = blip
    with pytest.raises(LaunchRefused) as refused:
        await get_api_run_anywhere(record.run_id)
    assert refused.value.code == "unavailable"  # 503, never a 404 that invites a relaunch

    del b.coordinator.read
    await a.client.hset(a.keys.run(record.run_id), "snapshot", "{not json")
    with pytest.raises(LaunchRefused) as refused:
        await get_api_run_anywhere(record.run_id)
    assert refused.value.code == "unavailable"
    task.cancel()


async def test_launch_source_is_in_the_run_hash_before_the_launch_returns(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(b.guard)
    run_id = str(uuid4())
    claim = await a.coordinator.claim(
        run_id=run_id, conversation_id="conv-9", user_email=EMAIL, max_runs_per_user=5
    )
    record = a.registry.start(
        conversation_id="conv-9", user_email=EMAIL, title="task", run_id=run_id
    )
    record.claim = claim
    record.launch_spec = {"source": API_SOURCE, "model": "m"}

    # Claimed, but its owner has written nothing else yet: not a 404.
    with pytest.raises(LaunchRefused) as refused:
        await get_api_run_anywhere(run_id)
    assert refused.value.code == "unavailable"

    await a.guard.publish_now(run_id)  # what launch_api_run awaits before the 202
    assert (await a.record(run_id))["source"] == API_SOURCE
    assert (await get_api_run_anywhere(run_id))["status"] == "queued"

    browser, browser_task = await a.start(source=None, conversation="conv-8")
    assert (await a.record(browser.run_id))["source"] == ""
    with pytest.raises(LaunchRefused) as refused:
        await get_api_run_anywhere(browser.run_id)
    assert refused.value.code != "unavailable"  # a known non-API run stays not found
    browser_task.cancel()


async def test_heartbeat_renewals_count_as_proof_for_the_fence_checks(server):
    a = Replica(server, "A")
    record, task = await a.start()
    sent = a.clock.now + LEASE * 0.9  # the guard clock when the beat was sent
    a.clock.now = sent
    a.guard._heartbeat_renewed([record.run_id], sent)
    calls = []

    async def counting(claims, statuses):
        calls.append(1)
        return {}

    a.coordinator.renew = counting
    await a.guard.ensure_may_act(record.run_id)
    assert calls == []  # the heartbeat just proved it: no round trip


async def test_unknown_renewal_inside_the_lease_does_not_fence(server):
    a = Replica(server, "A")
    record, task = await a.start()
    await a.guard.ensure_may_act(record.run_id)  # proved at clock 1000

    async def timeout(claims, statuses):
        raise TimeoutError("socket timeout")

    a.coordinator.renew = timeout
    a.clock.now += LEASE * 0.9  # past the trust window, inside the lease
    await a.guard.ensure_may_act(record.run_id)
    assert not a.guard.is_fenced(record.run_id)
    assert not task.cancelled()

    a.clock.now += LEASE * 0.2  # a whole lease without proof
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)
    task.cancel()


async def test_refusal_for_a_run_that_just_ended_is_not_a_fence(server):
    a = Replica(server, "A")
    record, task = await a.start()
    a.clock.now += LEASE

    async def ended_meanwhile(claims, statuses):
        a.registry.set_status(record.run_id, RunStatus.COMPLETED)
        return {record.run_id: False}  # released, so renewal refuses

    a.coordinator.renew = ended_meanwhile
    assert await a.guard.may_publish(record.run_id) is True
    assert not a.guard.is_fenced(record.run_id)
    assert a.registry.get(record.run_id).status is RunStatus.COMPLETED
    task.cancel()


async def test_self_fenced_run_still_commits_its_stopped_turn(server):
    from atlas.application.chat.runs.context import set_current_run

    a = Replica(server, "A")
    set_lease_guard(a.guard)
    record, sleeper = await a.start()
    sleeper.cancel()
    committed = []

    async def run():
        set_current_run(record.run_id, record.conversation_id)
        await ensure_current_run_may_act()
        a.clock.now += LEASE * 3
        await a.expire(record)
        try:
            await ensure_current_run_may_act()  # fences this very task
        except asyncio.CancelledError:
            # The stopped-turn commit (service._commit_turn) awaits storage.
            for _ in range(3):
                await asyncio.sleep(0)
            committed.append("partial transcript")
            raise

    task = asyncio.get_running_loop().create_task(run())
    a.registry.attach_task(record.run_id, task)
    (outcome,) = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(outcome, asyncio.CancelledError)
    assert committed == ["partial transcript"]
    assert a.registry.get(record.run_id).fenced


async def test_expired_owner_cannot_mirror_or_release_before_the_sweep(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    claim = record.claim
    await a.expire(record)  # lapsed; nobody has swept yet

    assert await a.scripts.mirror(claim, "completed", "{}", "", API_SOURCE) is False
    assert await a.coordinator.release(claim, "completed") is False
    assert (await a.record(record.run_id))["status"] == "running"

    assert await b.guard.sweep_once() == [record.run_id]  # the takeover still wins
    assert (await b.record(record.run_id))["interrupted"] == "1"
    task.cancel()


async def test_sweep_retries_a_failed_terminal_mirror_and_prunes(server):
    a = Replica(server, "A")
    record, task = await a.start()
    original = a.scripts.mirror
    fail = [True]

    async def flaky(*args):
        if fail[0]:
            raise ConnectionError("redis blip")
        return await original(*args)

    a.scripts.mirror = flaky
    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await a.settle()
    assert a.guard._leases[record.run_id].dirty

    fail[0] = False
    await a.guard.sweep_once()
    await a.settle()
    view = await a.guard.read_record(record.run_id)
    assert view["status"] == "completed" and view["result"] == "the answer"

    a.registry.remove(record.run_id)
    await a.guard.sweep_once()
    assert record.run_id not in a.guard._leases
    task.cancel()


async def test_one_failing_takeover_does_not_stop_the_sweep(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    first, t1 = await a.start(conversation="conv-1")
    second, t2 = await a.start(conversation="conv-2")
    await a.expire(first)
    await a.expire(second)
    original = b.guard._take_over

    async def flaky(run_id):
        if run_id == first.run_id:
            raise ConnectionError("redis blip")
        return await original(run_id)

    b.guard._take_over = flaky
    assert await b.guard.sweep_once() == [second.run_id]
    t1.cancel()
    t2.cancel()


async def test_guard_restarts_on_a_new_event_loop(server):
    a = Replica(server, "A")
    a.guard.start()
    first = list(a.guard._tasks)
    a.guard.start()  # idempotent on the same loop
    assert a.guard._tasks == first
    first[0].cancel()
    await asyncio.sleep(0)
    a.guard.start()  # a dead task (as after the loop it ran on closed) re-arms
    assert a.guard._tasks != first and not a.guard._tasks[0].done()
    from atlas.application.chat.runs import admission

    assert len([cb for cb in admission._claim_lost_listeners if cb == a.guard._claim_lost]) == 1
    await a.guard.stop()


def test_atlas_client_installs_the_lease_guard():
    from unittest.mock import AsyncMock, MagicMock

    from atlas.atlas_client import AtlasClient

    client = AtlasClient()
    factory = MagicMock()
    factory.stop_run_lease_guard = AsyncMock()
    factory.get_mcp_manager.return_value.cleanup = AsyncMock()
    client._factory, client._initialized = factory, True
    # The mocked chat service fails later; installation comes first.
    with contextlib.suppress(Exception):
        asyncio.run(client.chat("hi"))
    factory.start_run_lease_guard.assert_called()
    asyncio.run(client.cleanup())
    factory.stop_run_lease_guard.assert_awaited()


async def test_late_heartbeat_reply_is_not_proof(server):
    a = Replica(server, "A")
    record, task = await a.start()
    sent = a.clock.now
    a.clock.now += LEASE * 1.1
    a.guard._heartbeat_renewed([record.run_id], sent)
    assert a.guard._leases[record.run_id].verified_at == float("-inf")
    task.cancel()


async def test_renewal_success_delayed_past_the_lease_is_not_proof(server):
    a = Replica(server, "A")
    record, task = await a.start()

    async def slow_success(claims, statuses):
        a.clock.now += LEASE * 1.1  # the reply sat in a socket buffer
        return {record.run_id: True}

    a.coordinator.renew = slow_success
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)
    task.cancel()


async def test_tool_is_not_started_when_its_start_notification_finds_the_fence(
    server, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from atlas.application.chat.runs.context import set_current_run
    from atlas.application.chat.utilities import event_notifier
    from atlas.application.chat.utilities.tool_executor import execute_single_tool

    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(a.guard)
    record, sleeper = await a.start()

    async def start_notification(*args, **kwargs):
        # Blocked long enough for another replica to interrupt the run, and
        # its projection write finds the fence (it only drops the event).
        a.clock.now += LEASE * 3
        await a.expire(record)
        await b.guard.sweep_once()
        assert await a.guard.may_publish(record.run_id) is False

    monkeypatch.setattr(event_notifier, "notify_tool_start", start_notification)
    tool_manager = MagicMock()
    tool_manager.get_server_for_tool.return_value = "srv"
    tool_manager.get_tools_schema.return_value = []
    tool_manager.execute_tool = AsyncMock()
    tool_call = SimpleNamespace(id="call-1", function=SimpleNamespace(name="srv_t", arguments="{}"))

    async def step():
        set_current_run(record.run_id, record.conversation_id)
        await execute_single_tool(
            tool_call, {"session_id": "s1", "user_email": EMAIL}, tool_manager,
            update_callback=AsyncMock(), config_manager=None, skip_approval=True,
        )

    (outcome,) = await asyncio.gather(
        asyncio.get_running_loop().create_task(step()), return_exceptions=True
    )
    assert isinstance(outcome, asyncio.CancelledError), outcome
    tool_manager.execute_tool.assert_not_awaited()
    sleeper.cancel()


async def test_failed_projection_close_is_retried_by_a_later_sweep(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    await store.apply_event({
        "type": "tool_start", "run_id": record.run_id, "conversation_id": "conv-1",
        "user_email": EMAIL, "step": 0, "tool_call_id": "call_0", "tool_name": "x",
        "invocation_id": invocation_id(record.run_id, 0, "call_0"),
    })
    original = store.apply_event
    failures = [1]

    async def flaky(event):
        if event.get("type") == "run_status" and failures:
            failures.pop()
            raise ConnectionError("store blip")
        return await original(event)

    store.apply_event = flaky
    await a.expire(record)
    assert await b.guard.sweep_once() == [record.run_id]  # taken over, close failed
    assert await b.client.zscore(b.keys.live(), record.run_id) is not None

    await b.client.zadd(b.keys.live(), {record.run_id: 1})  # its retry is due
    assert await b.guard.sweep_once() == []  # not a second takeover, but closed now
    rows = public_projection(await store.get("conv-1", EMAIL))["invocations"]
    assert rows[0]["status"] == "interrupted"
    assert await b.client.zscore(b.keys.live(), record.run_id) is None

    # Closed and forgotten: the resumed old owner's own takeover attempt
    # writes nothing more to the projection.
    revision = (await store.get("conv-1", EMAIL))["revision"]
    assert not await a.guard._take_over(record.run_id)
    assert (await store.get("conv-1", EMAIL))["revision"] == revision
    task.cancel()


async def test_completion_during_a_blocked_snapshot_write_is_still_mirrored(server):
    a = Replica(server, "A")
    record, task = await a.start()
    gate = asyncio.Event()
    original = a.scripts.mirror

    async def blocked(*args):
        await gate.wait()
        return await original(*args)

    claim = record.claim
    a.scripts.mirror = blocked
    a.registry.mark_detached(record.run_id)  # a non-terminal write, now in flight
    await asyncio.sleep(0)
    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    gate.set()
    await a.settle()
    view = await a.guard.read_record(record.run_id)
    assert view["status"] == "completed" and view["result"] == "the answer"
    assert a.guard._leases[record.run_id].done

    # A late, older snapshot (a delayed publish) cannot replace the final one.
    assert await original(claim, "running", '{"result": null}', "", API_SOURCE) is True
    view = await a.guard.read_record(record.run_id)
    assert view["status"] == "completed" and view["result"] == "the answer"
    task.cancel()


async def test_terminal_projection_write_needs_the_claim(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    a.guard._leases[record.run_id].verified_at = a.clock.now
    # Frozen past its lease, then resumed: it finishes locally before any
    # sweep and before its heartbeat or mirror notice.
    await a.expire(record)
    a.clock.now += LEASE * 3
    record.status = RunStatus.COMPLETED  # set directly: no mirror spawned yet
    assert await a.guard.may_publish(record.run_id) is False
    assert a.registry.get(record.run_id).fenced

    await b.guard.sweep_once()
    assert (await b.record(record.run_id))["interrupted"] == "1"
    task.cancel()


async def test_interrupted_run_without_a_source_is_unavailable(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(b.guard)
    run_id = str(uuid4())
    claim = await a.coordinator.claim(
        run_id=run_id, conversation_id="conv-7", user_email=EMAIL, max_runs_per_user=5
    )
    assert claim is not None  # claimed; its owner died before the first mirror
    await a.client.hset(a.keys.run(run_id), "lease_expires_at", 1)
    await a.client.zadd(a.keys.live(), {run_id: 1})
    assert await b.guard.sweep_once() == [run_id]
    with pytest.raises(LaunchRefused) as refused:
        await get_api_run_anywhere(run_id)
    assert refused.value.code == "unavailable"


async def test_fenced_run_does_not_save_its_stale_turn(server):
    from unittest.mock import MagicMock

    from atlas.application.chat import service as service_module
    from atlas.application.chat.runs import registry as registry_module
    from atlas.application.chat.runs.context import set_current_run

    a = Replica(server, "A")
    record, task = await a.start()
    registry_module._registry = a.registry
    chat = service_module.ChatService(
        llm=MagicMock(), tool_manager=MagicMock(), connection=MagicMock(),
        config_manager=MagicMock(), session_repository=MagicMock(),
        conversation_repository=MagicMock(),
    )
    chat._save_conversation = MagicMock(return_value=True)
    session = MagicMock()
    session.history.messages = []
    session.context = {}

    async def commit():
        set_current_run(record.run_id, record.conversation_id)
        await chat._commit_turn(
            session, uuid4(), EMAIL, "m", None, is_incognito=False, save_floor=0
        )

    await asyncio.get_running_loop().create_task(commit())
    assert chat._save_conversation.call_count == 1  # an owner in good standing saves

    a.guard._fence_local(a.guard._leases[record.run_id], record.run_id, "taken over")
    await asyncio.get_running_loop().create_task(commit())
    assert chat._save_conversation.call_count == 1  # a fenced one does not
    task.cancel()


def test_guard_stops_cleanly_after_its_loop_closed(server):
    a = Replica(server, "A")

    async def first():
        a.guard.start()

    asyncio.run(first())  # chat_sync: this loop is closed afterwards
    asyncio.run(a.guard.stop())  # cleanup() on a new loop must not raise
    assert a.guard._tasks == []


async def test_lease_lapsing_during_authorization_stops_the_dispatch(server):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from atlas.application.chat.runs.context import set_current_run
    from atlas.domain.messages.models import ToolCall
    from atlas.modules.mcp_tools.client import MCPToolManager

    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(a.guard)
    record, sleeper = await a.start()
    await a.guard.ensure_may_act(record.run_id)  # proven before the step

    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = {"srv": {"enabled": True, "groups": []}}
    manager._elicitation_routing = {}
    manager._server_task_support = {}
    manager._tool_task_forbidden = set()
    manager._tool_index = {"srv_t": {"server": "srv", "tool": SimpleNamespace(name="t")}}
    original = manager._authorize_mcp_operation

    async def slow_authorization(*args, **kwargs):
        # The lease lapses and another replica interrupts the run meanwhile.
        a.clock.now += LEASE * 3
        await a.expire(record)
        await b.guard.sweep_once()
        return await original(*args, **kwargs)

    async def step():
        set_current_run(record.run_id, record.conversation_id)
        await manager.execute_tool(
            ToolCall(id="call-1", name="srv_t", arguments={}),
            context={"user_email": EMAIL},
        )

    with patch.object(manager, "_authorize_mcp_operation", slow_authorization), \
            patch.object(manager, "call_tool", new_callable=AsyncMock) as call_tool:
        (outcome,) = await asyncio.gather(
            asyncio.get_running_loop().create_task(step()), return_exceptions=True
        )
    assert isinstance(outcome, asyncio.CancelledError), outcome
    call_tool.assert_not_awaited()
    sleeper.cancel()


async def test_claim_grants_no_grace_before_a_proven_renewal(server):
    a = Replica(server, "A")
    record, task = await a.start()  # tracked only after the claim reply

    async def unreachable(claims, statuses):
        return {}

    a.coordinator.renew = unreachable
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)  # nothing ever proved it
    task.cancel()


async def test_projection_close_counts_only_when_confirmed(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    await store.apply_event({
        "type": "tool_start", "run_id": record.run_id, "conversation_id": "conv-1",
        "user_email": EMAIL, "step": 0, "tool_call_id": "call_0", "tool_name": "x",
        "invocation_id": invocation_id(record.run_id, 0, "call_0"),
    })
    original = store.apply_event
    gave_up = [1]

    async def exhausted(event):
        if event.get("type") == "run_status" and gave_up:
            gave_up.pop()
            return None  # what a store returns after running out of revision retries
        return await original(event)

    store.apply_event = exhausted
    await a.expire(record)
    assert await b.guard.sweep_once() == [record.run_id]
    assert await b.client.zscore(b.keys.live(), record.run_id) is not None  # not forgotten
    await b.client.zadd(b.keys.live(), {record.run_id: 1})  # its retry is due
    await b.guard.sweep_once()
    rows = public_projection(await store.get("conv-1", EMAIL))["invocations"]
    assert rows[0]["status"] == "interrupted"
    assert await b.client.zscore(b.keys.live(), record.run_id) is None
    task.cancel()


async def test_parallel_step_reraises_a_cancelled_tool(monkeypatch):
    from types import SimpleNamespace

    from atlas.application.chat.utilities import tool_executor

    async def fenced(**_kwargs):
        raise asyncio.CancelledError("run lost its claim")

    monkeypatch.setattr(tool_executor, "execute_single_tool", fenced)
    calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name="t", arguments="{}"))
        for i in range(2)
    ]
    with pytest.raises(asyncio.CancelledError):
        await tool_executor.execute_multiple_tools(calls, {}, tool_manager=None)


def test_snapshot_result_cap_keeps_truncation_detectable():
    from atlas.application.chat.runs.launcher import MAX_RESULT_CHARS

    assert leases_module.MAX_SNAPSHOT_RESULT_CHARS == MAX_RESULT_CHARS + 1


async def test_run_that_ended_during_a_failed_renewal_still_needs_its_proof(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    a.clock.now += LEASE

    async def taken_over_meanwhile(claims, statuses):
        # The run ends here while another replica takes it over; the
        # renewal itself gets no answer.
        a.registry.set_status(record.run_id, RunStatus.COMPLETED)
        await a.expire(record)
        await b.guard.sweep_once()
        return {}

    a.coordinator.renew = taken_over_meanwhile
    a.clock.now += LEASE  # and no proof inside the lease either
    assert await a.guard.may_publish(record.run_id) is False
    assert a.guard.is_fenced(record.run_id)
    assert (await b.record(record.run_id))["status"] == "failed"
    task.cancel()


async def test_failing_projection_close_neither_starves_others_nor_expires(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    stuck, t1 = await a.start(conversation="conv-1")
    original = store.apply_event

    async def flaky(event):
        if event.get("type") == "run_status" and event.get("run_id") == stuck.run_id:
            raise ConnectionError("store down for this run")
        return await original(event)

    store.apply_event = flaky
    await a.expire(stuck)
    assert await b.guard.sweep_once() == [stuck.run_id]  # close failed
    # Retried later, not first in line on every sweep.
    assert stuck.run_id not in await b.scripts.lapsed_candidates()

    fresh, t2 = await a.start(conversation="conv-2")
    await a.expire(fresh)
    await b.client.zadd(b.keys.live(), {stuck.run_id: 2})  # its retry is due
    assert await b.scripts.lapsed_candidates(limit=1) == [fresh.run_id]

    # Each retry keeps the record (and so the close obligation) alive.
    await b.client.pexpire(b.keys.run(stuck.run_id), 50)
    await b.guard.sweep_once()
    assert await b.client.pttl(b.keys.run(stuck.run_id)) > 1000
    assert await b.client.zscore(b.keys.live(), stuck.run_id) is not None
    t1.cancel()
    t2.cancel()


async def test_lease_lapsing_while_the_client_connects_stops_the_request(server):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from atlas.application.chat.runs.context import set_current_run
    from atlas.modules.mcp_tools.client import MCPToolManager

    a, b = Replica(server, "A"), Replica(server, "B")
    set_lease_guard(a.guard)
    record, sleeper = await a.start()
    await a.guard.ensure_may_act(record.run_id)  # proven at the dispatch boundary

    manager = MCPToolManager.__new__(MCPToolManager)
    manager._server_task_support = {}
    manager._tool_task_forbidden = set()
    manager._task_timeout = 1
    client = MagicMock()
    client.call_tool = AsyncMock()

    async def slow_connect():
        a.clock.now += LEASE * 3
        await a.expire(record)
        await b.guard.sweep_once()
        return client

    client.__aenter__ = AsyncMock(side_effect=slow_connect)
    client.__aexit__ = AsyncMock(return_value=False)

    async def step():
        set_current_run(record.run_id, record.conversation_id)
        await manager._call_tool_once_impl("srv", "t", {})

    settings = SimpleNamespace(app_settings=SimpleNamespace(mcp_call_timeout=5))
    with patch.object(manager, "_client_for_operation", AsyncMock(return_value=client)), \
            patch("atlas.modules.mcp_tools.client.config_manager", settings):
        (outcome,) = await asyncio.gather(
            asyncio.get_running_loop().create_task(step()), return_exceptions=True
        )
    assert isinstance(outcome, asyncio.CancelledError), outcome
    client.call_tool.assert_not_awaited()
    sleeper.cancel()


async def _owner_with_release_gate(server):
    a, b = Replica(server, "A"), Replica(server, "B")
    set_run_coordinator(a.coordinator)  # the registry's release goes here
    set_release_gate(a.guard.gate_release)  # as the started guard installs it
    record, task = await a.start()
    return a, b, record, task


async def test_release_waits_for_the_terminal_snapshot(server):
    a, b, record, task = await _owner_with_release_gate(server)
    original, gate = a.scripts.mirror, asyncio.Event()

    async def slow_mirror(*args, **kwargs):
        await gate.wait()
        return await original(*args, **kwargs)

    a.scripts.mirror = slow_mirror
    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    for _ in range(10):
        await asyncio.sleep(0)
    # The release has not overtaken the snapshot: nobody sees it ended yet.
    assert (await b.guard.read_record(record.run_id))["status"] == "running"
    assert await a.client.zscore(a.keys.live(), record.run_id) is not None

    gate.set()
    await drain_pending_releases()
    view = await b.guard.read_record(record.run_id)
    assert (view["status"], view["result"]) == ("completed", "the answer")
    assert await a.client.zscore(a.keys.live(), record.run_id) is None
    task.cancel()


async def test_owner_lost_before_its_terminal_snapshot_reads_interrupted(server):
    a, b, record, task = await _owner_with_release_gate(server)

    async def unreachable(*args, **kwargs):
        raise ConnectionError("redis gone")

    a.scripts.mirror = unreachable  # and then the process is gone
    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await drain_pending_releases()
    assert (await b.guard.read_record(record.run_id))["status"] == "running"  # not released

    await a.expire(record)
    assert await b.guard.sweep_once() == [record.run_id]
    view = await b.guard.read_record(record.run_id)
    assert view["status"] == "failed" and view["interrupted"] is True
    task.cancel()


async def test_held_release_lands_after_the_retried_snapshot(server):
    a, b, record, task = await _owner_with_release_gate(server)
    # The observer's write and the gate's own retry both fail.
    original, failures = a.scripts.mirror, [1, 1]

    async def blip(*args, **kwargs):
        if failures:
            failures.pop()
            raise ConnectionError("blip")
        return await original(*args, **kwargs)

    a.scripts.mirror = blip
    record.result = "the answer"
    a.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await drain_pending_releases()
    assert await a.client.zscore(a.keys.live(), record.run_id) is not None  # held

    await a.guard.sweep_once()  # retries the snapshot, then releases
    await a.settle()
    view = await b.guard.read_record(record.run_id)
    assert (view["status"], view["result"]) == ("completed", "the answer")
    assert await a.client.zscore(a.keys.live(), record.run_id) is None
    task.cancel()


async def test_started_guard_gates_releases_until_stopped(server):
    from atlas.application.chat.runs.coordination import get_release_gate

    a = Replica(server, "A")
    a.guard.start()
    assert get_release_gate() == a.guard.gate_release
    await a.guard.stop()
    assert get_release_gate() is None


async def test_orphan_takeover_records_owner_lost_in_the_projection(server):
    store = MemoryExecutionStore()
    set_execution_store(store)
    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    publish = {
        "type": "run_status", "status": "running", "run_id": record.run_id,
        "conversation_id": "conv-1", "user_email": EMAIL,
    }
    await store.apply_event(publish)
    await a.expire(record)
    assert await b.guard.sweep_once() == [record.run_id]
    doc = await store.get("conv-1", EMAIL)
    assert doc["status"] == "failed"
    assert doc["end_reason"] == "owner_lost"
    task.cancel()


async def test_lost_claim_records_fenced_on_the_stopped_run(server):
    from atlas.core.coordination_metrics import outcome_for

    a, b = Replica(server, "A"), Replica(server, "B")
    record, task = await a.start()
    await a.expire(record)
    await b.guard.sweep_once()
    claim = record.claim
    a.registry.mark_detached(record.run_id)
    await a.settle()
    await a.coordinator.renew([claim], {record.run_id: "running"})
    local = a.registry.get(record.run_id)
    assert local.fenced and local.end_reason == "fenced"
    assert outcome_for(local.status.value, local.end_reason) == "interrupted"
    task.cancel()
