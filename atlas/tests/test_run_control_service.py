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


async def test_released_run_is_no_longer_routed(cluster):
    record = await cluster.run()
    claim = await cluster.a.coordinator.read(record.run_id)
    assert await run_control.remote_run_for_conversation(record.conversation_id) is not None
    from atlas.application.chat.runs.coordination import RunClaim

    await cluster.a.coordinator.release(RunClaim(
        run_id=record.run_id, conversation_id=record.conversation_id, user_email=OWNER,
        owner="replica-a", generation=int(claim["generation"]), lease_expires_at=0,
    ), "completed")
    assert await run_control.remote_run_for_conversation(record.conversation_id) is None
    assert await run_control.route_stop(OWNER, record.run_id, None) is None
