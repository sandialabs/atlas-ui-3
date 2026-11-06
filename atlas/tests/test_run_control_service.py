"""Owner-side execution and sender-side routing of run control (issue #1128).

Replica A owns the runs (its own registry and approval manager, and its
coordinator claimed them); replica B is the process-wide bus the ``route_*``
helpers use. Both share one fakeredis.
"""

import asyncio

import fakeredis
import pytest

from atlas.application.chat.agent.steering import STEERING_QUEUE_MAXSIZE, SteeringChannel
from atlas.application.chat.approval_manager import ToolApprovalManager
from atlas.application.chat.runs import run_control
from atlas.application.chat.runs.control_bus import RedisRunControlBus, set_run_control_bus
from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    approval_storage_key,
    set_execution_store,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus
from atlas.application.chat.runs.run_control import RunControlService, user_actor
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator

OWNER = "owner@example.com"
OTHER = "other@example.com"


class Replica:
    def __init__(self, server, replica_id):
        manager = RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=server),
        )
        self.coordinator = RedisRunCoordinator(manager, replica_id=replica_id)
        self.bus = RedisRunControlBus(manager, self.coordinator)

    async def claim(self, run_id, conversation_id, user_email=OWNER):
        return await self.coordinator.claim(run_id=run_id, conversation_id=conversation_id,
                                            user_email=user_email, max_runs_per_user=50)


class Cluster:
    def __init__(self):
        server = fakeredis.FakeServer()
        self.a = Replica(server, "replica-a")
        self.b = Replica(server, "replica-b")
        self.registry = RunRegistry()
        self.approvals = ToolApprovalManager()
        self.refusal = None

        async def refusal_check(_session_id, _level):
            return self.refusal

        self.service = RunControlService(lambda: self.registry, lambda: self.approvals, refusal_check)
        self.a.bus.start(self.service.handle)

    async def run(self, *, steering=None, task=True):
        record = self.registry.start(conversation_id=f"conv-{len(self.registry.all_records())}",
                                     user_email=OWNER, steering=steering)
        if task:
            self.registry.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
        # As admission does on A: the cluster-wide claim names A the owner.
        await self.a.claim(record.run_id, record.conversation_id, record.user_email)
        return record


@pytest.fixture
async def cluster():
    store = MemoryExecutionStore()
    set_execution_store(store)
    c = Cluster()
    c.store = store
    set_run_control_bus(c.b.bus)
    yield c
    set_run_control_bus(None)
    set_execution_store(None)
    for record in c.registry.all_records():
        if record.task is not None:
            record.task.cancel()
    await c.a.bus.stop()


async def test_route_stop_cancels_the_run_on_its_owner(cluster):
    record = await cluster.run()
    answer = await run_control.route_stop(OWNER, record.run_id, None)
    assert answer["ok"] is True and answer["payload"]["cancelled"] is True
    assert answer["payload"]["run"]["status"] == "cancelled"
    assert cluster.registry.get(record.run_id).status is RunStatus.CANCELLED
    # A repeat is harmless: the run is already terminal.
    again = await run_control.route_stop(OWNER, record.run_id, None)
    assert again["ok"] is True and again["payload"]["cancelled"] is False


async def test_route_stop_by_conversation(cluster):
    record = await cluster.run()
    answer = await run_control.route_stop(OWNER, None, record.conversation_id)
    assert answer["ok"] is True
    assert cluster.registry.get(record.run_id).is_terminal


async def test_another_user_cannot_stop_the_run(cluster):
    record = await cluster.run()
    assert await run_control.route_stop(OTHER, record.run_id, None) is None
    assert await run_control.route_stop(OTHER, None, record.conversation_id) is None
    # Even a command that reaches the owner with a forged directory is checked
    # against the owner's own registry.
    answer = await cluster.service.handle({"command": "stop", "run_id": record.run_id,
                                           "actor": user_actor(OTHER), "args": {}})
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not cluster.registry.get(record.run_id).is_terminal


async def test_service_actor_may_only_stop_api_runs(cluster):
    record = await cluster.run()
    answer = await run_control.route_api_cancel(record.run_id)
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not cluster.registry.get(record.run_id).is_terminal


async def test_routes_ignore_runs_this_replica_owns_or_nobody_owns(cluster):
    assert await run_control.route_stop(OWNER, "nope", None) is None
    await cluster.b.claim("b-run", "conv-b")  # owned by this replica (B)
    assert await run_control.route_stop(OWNER, "b-run", None) is None
    assert await run_control.route_stop(OWNER, None, "conv-b") is None


async def test_steer_is_queued_once_per_request_id(cluster):
    channel = SteeringChannel()
    channel.activate()
    record = await cluster.run(steering=channel)
    entry = await run_control.remote_run_for_conversation(record.conversation_id)
    first = await run_control.route_steer(entry, OWNER, "go left", None, request_id="s1")
    second = await run_control.route_steer(entry, OWNER, "go left", None, request_id="s1")
    assert first["ok"] and second["ok"] and second.get("duplicate") is True
    assert channel.queue.qsize() == 1 and channel.queue.get_nowait() == "go left"


async def test_steer_refusals(cluster):
    idle = await cluster.run(steering=SteeringChannel())  # loop not draining yet
    entry = await run_control.remote_run_for_conversation(idle.conversation_id)
    assert (await run_control.route_steer(entry, OWNER, "x", None))["code"] == "conversation_busy"

    channel = SteeringChannel()
    channel.activate()
    active = await cluster.run(steering=channel)
    entry = await run_control.remote_run_for_conversation(active.conversation_id)
    cluster.refusal = "level changed"
    refused = await run_control.route_steer(entry, OWNER, "x", "Secret")
    assert refused["code"] == "classification_refused" and refused["message"] == "level changed"
    cluster.refusal = None
    for _ in range(STEERING_QUEUE_MAXSIZE):
        channel.queue.put_nowait("filler")
    assert (await run_control.route_steer(entry, OWNER, "x", None))["code"] == "steering_queue_full"

    cluster.registry.cancel(active.run_id, OWNER)
    answer = await cluster.service.handle({"command": "steer", "run_id": active.run_id,
                                           "actor": user_actor(OWNER), "args": {"content": "x"}})
    assert answer["code"] == "run_not_found"


async def _pending_approval(cluster, record):
    request = cluster.approvals.create_approval_request(
        "call_0", "server_tool", {"a": 1}, user_email=OWNER,
        invocation_id=f"{record.run_id}:0:call_0", run_id=record.run_id,
    )
    cluster.registry.set_status(record.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval_request")
    cluster.registry.set_pending_request(record.run_id, {"type": "tool_approval_request", "tool_call_id": "call_0"})
    return request


async def test_approval_needs_the_recorded_outcome_and_wakes_the_tool_once(cluster):
    record = await cluster.run()
    request = await _pending_approval(cluster, record)
    invocation = request.invocation_id

    answer = await run_control.route_approval(OWNER, record.run_id, invocation, "call_0")
    assert answer["ok"] is False and answer["code"] == "no_outcome"
    assert not request.future.done()

    key = approval_storage_key(invocation, "call_0", OWNER)
    await cluster.store.put_resolution(key, {"approved": True, "arguments": None, "reason": None})
    # Same derived request id as the refused attempt: a refusal is not
    # remembered, so this one runs.
    applied = await run_control.route_approval(OWNER, record.run_id, invocation, "call_0")
    assert applied["ok"] is True and applied["payload"]["applied"] is True
    assert not applied.get("duplicate")
    assert (await request.future)["approved"] is True
    assert cluster.registry.get(record.run_id).status is RunStatus.RUNNING

    # A repeated nudge gets the first reply back and wakes nothing.
    repeat = await run_control.route_approval(OWNER, record.run_id, invocation, "call_0")
    assert repeat["ok"] is True and repeat.get("duplicate") is True
    direct = await cluster.service.handle({
        "command": "approval", "run_id": record.run_id, "actor": user_actor(OWNER),
        "args": {"invocation_id": invocation, "tool_call_id": "call_0"},
    })
    assert direct["ok"] is True and direct["payload"]["applied"] is False


async def test_approval_from_another_user_is_refused(cluster):
    record = await cluster.run()
    request = await _pending_approval(cluster, record)
    key = approval_storage_key(request.invocation_id, "call_0", OWNER)
    await cluster.store.put_resolution(key, {"approved": True})
    assert await run_control.route_approval(OTHER, record.run_id, request.invocation_id, "call_0") is None
    answer = await cluster.service.handle({
        "command": "approval", "run_id": record.run_id, "actor": user_actor(OTHER),
        "args": {"invocation_id": request.invocation_id, "tool_call_id": "call_0"},
    })
    assert answer["ok"] is False
    assert not request.future.done()


async def _release(cluster, record, status="completed"):
    from atlas.application.chat.runs.coordination import RunClaim

    claim = await cluster.a.coordinator.read(record.run_id)
    await cluster.a.coordinator.release(RunClaim(
        run_id=record.run_id, conversation_id=record.conversation_id, user_email=record.user_email,
        owner="replica-a", generation=int(claim["generation"]), lease_expires_at=0,
    ), status)


async def test_released_run_only_routes_a_stop(cluster):
    channel = SteeringChannel()
    channel.activate()
    record = await cluster.run(steering=channel)
    assert await run_control.remote_run_for_conversation(record.conversation_id) is not None
    cluster.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await _release(cluster, record)
    # Nothing is steered into, or blocks, a conversation whose run finished.
    assert await run_control.remote_run_for_conversation(record.conversation_id) is None
    # A stop naming it still reaches its last owner, and changes nothing there.
    answer = await run_control.route_stop(OWNER, record.run_id, None)
    assert answer["ok"] is True and answer["payload"]["cancelled"] is False
    assert cluster.registry.get(record.run_id).status is RunStatus.COMPLETED


async def test_stopping_a_finished_parent_stops_its_live_child_on_the_owner(cluster):
    parent = await cluster.run()
    child = cluster.registry.start(conversation_id="conv-child", user_email=OWNER,
                                   parent_run_id=parent.run_id, depth=1)
    cluster.registry.attach_task(child.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.a.claim(child.run_id, child.conversation_id)
    # atlas_launch returns a handle, so the parent usually finishes first.
    cluster.registry.set_status(parent.run_id, RunStatus.COMPLETED)
    await _release(cluster, parent)
    answer = await run_control.route_stop(OWNER, parent.run_id, parent.conversation_id)
    assert answer["ok"] is True
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


async def test_finished_run_whose_last_owner_is_gone_is_not_routed(cluster):
    record = await cluster.run()
    cluster.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await _release(cluster, record)
    await cluster.a.bus.stop()  # A no longer answers
    started = asyncio.get_running_loop().time()
    run_control_bus = run_control.get_run_control_bus()
    original = run_control_bus.send

    async def quick(*args, **kwargs):
        kwargs["timeout"] = 0.3
        return await original(*args, **kwargs)

    run_control_bus.send = quick
    try:
        assert await run_control.route_stop(OWNER, record.run_id, None) is None
    finally:
        run_control_bus.send = original
    assert asyncio.get_running_loop().time() - started < 3


async def test_api_cancel_of_a_finished_run_stops_its_follow_up_elsewhere(cluster, monkeypatch):
    from atlas.application.chat.runs import external_runs

    # Replica A ran the original API run; replica B (this process) runs the
    # follow-up in the same conversation.
    registry_b = RunRegistry()
    monkeypatch.setattr(external_runs, "get_run_registry", lambda: registry_b)
    original = await cluster.run()
    original.launch_spec = {"source": external_runs.API_SOURCE}
    cluster.registry.set_status(original.run_id, RunStatus.COMPLETED)
    await _release(cluster, original)
    follow_up = registry_b.start(conversation_id=original.conversation_id, user_email=OWNER)
    follow_up.launch_spec = {"source": external_runs.API_SOURCE}
    registry_b.attach_task(follow_up.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.b.claim(follow_up.run_id, follow_up.conversation_id)
    await cluster.b.bus._client().hset(
        cluster.b.coordinator.keys.run(follow_up.run_id), "source", external_runs.API_SOURCE)

    # A's service resolves API runs through the same patched registry; give it
    # A's view for the original id.
    def a_registry():
        return cluster.registry

    async def stop_on_a(envelope):
        monkeypatch.setattr(external_runs, "get_run_registry", a_registry)
        try:
            return await cluster.service.handle(envelope)
        finally:
            monkeypatch.setattr(external_runs, "get_run_registry", lambda: registry_b)

    cluster.a.bus._handler = stop_on_a
    answer = await run_control.route_api_cancel(original.run_id)
    assert answer["ok"] is True, answer
    status = answer["payload"]["status"]
    assert status["run_id"] == follow_up.run_id and status["cancelled"] is True
    assert registry_b.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_unreadable_directory_is_not_taken_for_no_owner(cluster):
    record = await cluster.run()

    async def fail(*_args, **_kwargs):
        raise TimeoutError("redis timed out")

    cluster.b.bus.owner_of = fail
    cluster.b.bus.run_for_conversation = fail
    with pytest.raises(run_control.RunDirectoryUnavailable):
        await run_control.remote_run_for_conversation(record.conversation_id)
    for answer in (
        await run_control.route_stop(OWNER, record.run_id, None),
        await run_control.route_stop(OWNER, None, record.conversation_id),
        await run_control.route_api_cancel(record.run_id),
    ):
        assert answer["ok"] is False and answer["code"] == "owner_unavailable", answer
    # The approval nudge is best effort: the executor polls the outcome anyway.
    assert await run_control.route_approval(OWNER, record.run_id, "inv", "call") is None
    assert not cluster.registry.get(record.run_id).is_terminal


async def test_steer_whose_check_outlasted_the_deadline_is_not_queued(cluster):
    import time as _time

    channel = SteeringChannel()
    channel.activate()
    record = await cluster.run(steering=channel)
    gate = asyncio.Event()

    async def slow_check(_session_id, _level):
        await gate.wait()
        return None

    cluster.service._refusal_check = slow_check
    pending = asyncio.create_task(cluster.service.handle({
        "command": "steer", "run_id": record.run_id, "actor": user_actor(OWNER),
        "args": {"content": "late"}, "deadline": _time.time() + 0.1,
    }))
    await asyncio.sleep(0.2)
    gate.set()
    answer = await pending
    assert answer["code"] == "command_expired"
    assert channel.queue.qsize() == 0
