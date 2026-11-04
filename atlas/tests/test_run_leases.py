"""Run leases, orphan sweep and fencing on top of the shared claims (#1128).

Two ``RedisRunCoordinator``s with different replica ids on one fake Redis
(the Lua scripts run for real under ``fakeredis[lua]``) stand in for two
replicas, each with its own process-local registry and lease guard. Lease
expiry is simulated by moving the record's deadline into the past, and a
frozen owner by advancing its guard's injected clock.
"""

import asyncio
from uuid import uuid4

import fakeredis
import pytest

from atlas.application.chat.runs import leases as leases_module
from atlas.application.chat.runs.coordination import set_run_coordinator
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
    yield
    set_lease_guard(None)
    set_run_coordinator(None)
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
