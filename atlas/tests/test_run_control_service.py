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
from atlas.application.chat.runs.control_bus import (
    OWNER_UNAVAILABLE,
    RedisRunControlBus,
    reply,
    set_run_control_bus,
)
from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    approval_storage_key,
    set_execution_store,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus
from atlas.application.chat.runs.resolve import Address, Remote, Unknown, resolve
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

    async def claim(self, run_id, conversation_id, user_email=OWNER, parent_run_id=None):
        return await self.coordinator.claim(run_id=run_id, conversation_id=conversation_id,
                                            user_email=user_email, max_runs_per_user=50,
                                            parent_run_id=parent_run_id)


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


# The sender half takes the entry ``resolve`` found. These adapters locate it
# the way the handlers do (nothing in this replica's registry), so the cases
# below keep exercising lookup and send together.
_NO_LOCAL_RUNS = RunRegistry()


async def _located(actor, run_id, conversation_id, include_terminal=False):
    resolution = await resolve(_NO_LOCAL_RUNS, actor, Address(run_id, conversation_id),
                               include_terminal=include_terminal)
    if isinstance(resolution, Unknown):
        raise run_control.RunDirectoryUnavailable(resolution.reason)
    return resolution.entry if isinstance(resolution, Remote) else None


async def _remote_run_for_conversation(conversation_id):
    return await _located(user_actor(OWNER), None, conversation_id)


def _unavailable():
    return reply(False, OWNER_UNAVAILABLE, "The shared run directory could not be read.")


async def _route_stop(user_email, run_id, conversation_id):
    try:
        entry = await _located(user_actor(user_email), run_id, conversation_id, include_terminal=True)
    except run_control.RunDirectoryUnavailable:
        return _unavailable()
    if entry is None or entry.get("user_email") != user_email:
        return None
    return await run_control.route_stop(entry, user_email)


async def _route_api_cancel(run_id):
    try:
        entry = await _located(run_control.SERVICE_ACTOR, run_id, None, include_terminal=True)
    except run_control.RunDirectoryUnavailable:
        return _unavailable()
    return None if entry is None else await run_control.route_api_cancel(entry)


async def _route_approval(user_email, run_id, invocation_id, tool_call_id):
    try:
        entry = await _located(user_actor(user_email), run_id, None)
    except run_control.RunDirectoryUnavailable:
        return None
    if entry is None:
        return None
    return await run_control.route_approval(entry, user_email, invocation_id, tool_call_id)


async def test_route_stop_cancels_the_run_on_its_owner(cluster):
    record = await cluster.run()
    answer = await _route_stop(OWNER, record.run_id, None)
    assert answer["ok"] is True and answer["payload"]["cancelled"] is True
    assert answer["payload"]["run"]["status"] == "cancelled"
    assert cluster.registry.get(record.run_id).status is RunStatus.CANCELLED
    # A repeat is harmless: the run is already terminal.
    again = await _route_stop(OWNER, record.run_id, None)
    assert again["ok"] is True and again["payload"]["cancelled"] is False


async def test_route_stop_by_conversation(cluster):
    record = await cluster.run()
    answer = await _route_stop(OWNER, None, record.conversation_id)
    assert answer["ok"] is True
    assert answer["run_id"] == record.run_id
    assert cluster.registry.get(record.run_id).is_terminal


async def test_another_user_cannot_stop_the_run(cluster):
    record = await cluster.run()
    assert await _route_stop(OTHER, record.run_id, None) is None
    assert await _route_stop(OTHER, None, record.conversation_id) is None
    # Even a command that reaches the owner with a forged directory is checked
    # against the owner's own registry.
    answer = await cluster.service.handle({"command": "stop", "run_id": record.run_id,
                                           "actor": user_actor(OTHER), "args": {}})
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not cluster.registry.get(record.run_id).is_terminal


async def test_service_actor_may_only_stop_api_runs(cluster):
    record = await cluster.run()
    answer = await _route_api_cancel(record.run_id)
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not cluster.registry.get(record.run_id).is_terminal


async def test_routes_ignore_runs_this_replica_owns_or_nobody_owns(cluster):
    assert await _route_stop(OWNER, "nope", None) is None
    await cluster.b.claim("b-run", "conv-b")  # owned by this replica (B)
    assert await _route_stop(OWNER, "b-run", None) is None
    assert await _route_stop(OWNER, None, "conv-b") is None


async def test_steer_is_queued_once_per_request_id(cluster):
    channel = SteeringChannel()
    channel.activate()
    record = await cluster.run(steering=channel)
    entry = await _remote_run_for_conversation(record.conversation_id)
    first = await run_control.route_steer(entry, OWNER, "go left", None, request_id="s1")
    second = await run_control.route_steer(entry, OWNER, "go left", None, request_id="s1")
    assert first["ok"] and second["ok"] and second.get("duplicate") is True
    assert channel.queue.qsize() == 1 and channel.queue.get_nowait() == "go left"


async def test_steer_refusals(cluster):
    idle = await cluster.run(steering=SteeringChannel())  # loop not draining yet
    entry = await _remote_run_for_conversation(idle.conversation_id)
    assert (await run_control.route_steer(entry, OWNER, "x", None))["code"] == "conversation_busy"

    channel = SteeringChannel()
    channel.activate()
    active = await cluster.run(steering=channel)
    entry = await _remote_run_for_conversation(active.conversation_id)
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

    answer = await _route_approval(OWNER, record.run_id, invocation, "call_0")
    assert answer["ok"] is False and answer["code"] == "no_outcome"
    assert not request.future.done()

    key = approval_storage_key(invocation, "call_0", OWNER)
    await cluster.store.put_resolution(key, {"approved": True, "arguments": None, "reason": None})
    # Same derived request id as the refused attempt: a refusal is not
    # remembered, so this one runs.
    applied = await _route_approval(OWNER, record.run_id, invocation, "call_0")
    assert applied["ok"] is True and applied["payload"]["applied"] is True
    assert not applied.get("duplicate")
    assert (await request.future)["approved"] is True
    assert cluster.registry.get(record.run_id).status is RunStatus.RUNNING

    # A repeated nudge gets the first reply back and wakes nothing.
    repeat = await _route_approval(OWNER, record.run_id, invocation, "call_0")
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
    assert await _route_approval(OTHER, record.run_id, request.invocation_id, "call_0") is None
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
    assert await _remote_run_for_conversation(record.conversation_id) is not None
    cluster.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await _release(cluster, record)
    # Nothing is steered into, or blocks, a conversation whose run finished.
    assert await _remote_run_for_conversation(record.conversation_id) is None
    # A stop naming it still reaches its last owner, and changes nothing there.
    answer = await _route_stop(OWNER, record.run_id, None)
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
    answer = await _route_stop(OWNER, parent.run_id, parent.conversation_id)
    assert answer["ok"] is True
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


async def test_finished_run_whose_last_owner_is_gone_reports_a_timeout(cluster):
    record = await cluster.run()
    cluster.registry.set_status(record.run_id, RunStatus.COMPLETED)
    await _release(cluster, record)
    await cluster.a.bus.stop()  # A no longer answers
    bus = run_control.get_run_control_bus()
    original = bus.send

    async def quick(*args, **kwargs):
        kwargs["timeout"] = 0.3
        return await original(*args, **kwargs)

    bus.send = quick
    try:
        # Its children may still be running there: retryable, not "no run".
        answer = await _route_stop(OWNER, record.run_id, None)
    finally:
        bus.send = original
    assert answer["ok"] is False and answer["code"] == "command_timeout"


class _ApiReplicas:
    """API runs on both replicas: A's records in ``cluster.registry``, B's in
    ``registry_b``. ``cancel_api_run`` reads whichever replica is acting."""

    def __init__(self, cluster, monkeypatch):
        from atlas.application.chat.runs import external_runs

        self.cluster = cluster
        self.api_source = external_runs.API_SOURCE
        self.registry_b = RunRegistry()
        self.acting = {"registry": self.registry_b}
        monkeypatch.setattr(external_runs, "get_run_registry", self._acting_registry)
        cluster.a.bus._handler = self._handle_on_a

    def _acting_registry(self):
        return self.acting["registry"]

    def _registry_b(self):
        return self.registry_b

    async def _handle_on_a(self, envelope):
        self.acting["registry"] = self.cluster.registry
        try:
            answer = await self.cluster.service.handle(envelope)
        finally:
            self.acting["registry"] = self.registry_b
        return answer

    async def finished_on_a(self):
        record = await self.cluster.run()
        record.launch_spec = {"source": self.api_source}
        self.cluster.registry.set_status(record.run_id, RunStatus.COMPLETED)
        await self._mark_api(record)
        await _release(self.cluster, record)
        return record

    async def live_on_a(self, conversation_id):
        record = self.cluster.registry.start(conversation_id=conversation_id, user_email=OWNER)
        record.launch_spec = {"source": self.api_source}
        self.cluster.registry.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
        await self.cluster.a.claim(record.run_id, conversation_id)
        await self._mark_api(record)
        return record

    async def live_on_b(self, conversation_id):
        record = self.registry_b.start(conversation_id=conversation_id, user_email=OWNER)
        record.launch_spec = {"source": self.api_source}
        self.registry_b.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
        await self.cluster.b.claim(record.run_id, conversation_id)
        await self._mark_api(record)
        return record

    async def _mark_api(self, record):
        # What the owner's lease mirror writes with the run's snapshot.
        await self.cluster.b.bus._client().hset(
            self.cluster.b.coordinator.keys.run(record.run_id), "source", self.api_source)


async def test_api_cancel_of_a_finished_run_stops_its_follow_up_elsewhere(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    original = await api.finished_on_a()
    follow_up = await api.live_on_b(original.conversation_id)
    answer = await _route_api_cancel(original.run_id)
    assert answer["ok"] is True, answer
    status = answer["payload"]["status"]
    assert status["run_id"] == follow_up.run_id and status["cancelled"] is True
    assert api.registry_b.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_api_cancel_of_a_finished_run_held_here_stops_its_follow_up_elsewhere(cluster, monkeypatch):
    from atlas.routes.agent_run_routes import cancel_run

    api = _ApiReplicas(cluster, monkeypatch)
    # The original ran here (B) and finished; the follow-up runs on A.
    original = api.registry_b.start(conversation_id="conv-api", user_email=OWNER)
    original.launch_spec = {"source": api.api_source}
    api.registry_b.set_status(original.run_id, RunStatus.COMPLETED)
    follow_up = await api.live_on_a("conv-api")
    status = await cancel_run(original.run_id)
    assert status["run_id"] == follow_up.run_id and status["cancelled"] is True
    assert cluster.registry.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_api_cancel_reports_a_follow_up_it_could_not_stop(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    original = await api.finished_on_a()
    await api.live_on_a(original.conversation_id)
    real_send = run_control._send

    async def follow_up_unreachable(entry, command, actor, args=None, request_id=None):
        if entry["run_id"] == original.run_id:
            return await real_send(entry, command, actor, args, request_id)
        return run_control.reply(False, "command_timeout", "no answer")

    monkeypatch.setattr(run_control, "_send", follow_up_unreachable)
    answer = await _route_api_cancel(original.run_id)
    assert answer["ok"] is False and answer["code"] == "command_timeout"


async def test_api_cancel_reports_a_named_run_it_could_not_stop(cluster, monkeypatch):
    # The follow-up is stopped, but the named run's owner never answered: its
    # stop is what cascades to the sub-conversations it launched, so the
    # cancel must stay a retryable failure rather than a 200.
    api = _ApiReplicas(cluster, monkeypatch)
    original = await api.finished_on_a()
    follow_up = await api.live_on_a(original.conversation_id)
    real_send = run_control._send

    async def named_run_unreachable(entry, command, actor, args=None, request_id=None):
        if entry["run_id"] == original.run_id:
            return run_control.reply(False, "command_timeout", "no answer")
        return await real_send(entry, command, actor, args, request_id)

    monkeypatch.setattr(run_control, "_send", named_run_unreachable)
    answer = await _route_api_cancel(original.run_id)
    assert answer["ok"] is False and answer["code"] == "command_timeout"
    assert cluster.registry.get(follow_up.run_id).is_terminal


async def test_api_cancel_never_cascades_from_a_run_the_api_did_not_start(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    browser_run = await cluster.run()
    cluster.registry.set_status(browser_run.run_id, RunStatus.COMPLETED)
    await _release(cluster, browser_run)
    follow_up = await api.live_on_b(browser_run.conversation_id)
    answer = await _route_api_cancel(browser_run.run_id)
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not api.registry_b.get(follow_up.run_id).is_terminal


async def test_unreadable_directory_is_not_taken_for_no_owner(cluster):
    record = await cluster.run()

    async def fail(*_args, **_kwargs):
        raise TimeoutError("redis timed out")

    cluster.b.bus.owner_of = fail
    cluster.b.bus.run_for_conversation = fail
    with pytest.raises(run_control.RunDirectoryUnavailable):
        await _remote_run_for_conversation(record.conversation_id)
    for answer in (
        await _route_stop(OWNER, record.run_id, None),
        await _route_stop(OWNER, None, record.conversation_id),
        await _route_api_cancel(record.run_id),
    ):
        assert answer["ok"] is False and answer["code"] == "owner_unavailable", answer
    # The approval nudge is best effort: the executor polls the outcome anyway.
    assert await _route_approval(OWNER, record.run_id, "inv", "call") is None
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


def test_stop_failure_by_conversation_names_the_resolved_run():
    # A stop that named only the conversation still reports the run it
    # resolved to, so the client keeps the running state and its Stop control.
    from atlas.application.chat.runs.control_bus import COMMAND_TIMEOUT, reply
    from atlas.application.chat.runs.remote_run_frames import stop_frame

    answer = {**reply(False, COMMAND_TIMEOUT, "late"), "run_id": "run-9"}
    frame = stop_frame(answer, {"conversation_id": "conv-9"})
    assert frame["run_id"] == "run-9" and frame["conversation_id"] == "conv-9"
    # The run id the client addressed wins.
    assert stop_frame(answer, {"run_id": "run-1"})["run_id"] == "run-1"


async def test_api_cancel_of_a_run_that_finishes_in_flight_stops_its_new_follow_up(cluster, monkeypatch):
    # Live when routed, finished while the stop was in flight, and a follow-up
    # already started on B: the follow-up lookup runs after the owner answers.
    api = _ApiReplicas(cluster, monkeypatch)
    original = await api.live_on_a("conv-race")
    real_send = run_control._send
    started = {}

    async def finish_then_send(entry, command, actor, args=None, request_id=None, off_record=False):
        if entry["run_id"] == original.run_id and "follow_up" not in started:
            cluster.registry.set_status(original.run_id, RunStatus.COMPLETED)
            await _release(cluster, original)
            started["follow_up"] = await api.live_on_b("conv-race")
        return await real_send(entry, command, actor, args, request_id=request_id, off_record=off_record)

    monkeypatch.setattr(run_control, "_send", finish_then_send)
    await _route_api_cancel(original.run_id)
    assert api.registry_b.get(started["follow_up"].run_id).status is RunStatus.CANCELLED


async def test_steer_is_refused_when_the_loop_stops_draining_during_the_check(cluster):
    channel = SteeringChannel()
    channel.activate()
    record = await cluster.run(steering=channel)
    entry = await _remote_run_for_conversation(record.conversation_id)

    async def deactivating_check(_session_id, _level):
        channel.active = False
        return None

    cluster.service._refusal_check = deactivating_check
    answer = await run_control.route_steer(entry, OWNER, "late", None)
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert channel.queue.qsize() == 0


async def test_api_cancel_of_a_live_run_held_here_needs_no_directory(cluster, monkeypatch):
    from atlas.application.chat.runs.commands import cancel_api_run_addressed

    api = _ApiReplicas(cluster, monkeypatch)
    record = api.registry_b.start(conversation_id="conv-here", user_email=OWNER)
    record.launch_spec = {"source": api.api_source}
    api.registry_b.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
    await asyncio.sleep(0.05)

    async def fail(*_a, **_k):
        raise TimeoutError("redis timed out")

    cluster.b.bus.owner_of = fail
    cluster.b.bus.run_for_conversation = fail
    # The run was live when cancelled: no follow-up is looked up, so the
    # unreadable directory cannot turn a cancel that took effect into a 503.
    answer = await cancel_api_run_addressed(record.run_id)
    assert answer["ok"] is True and answer["payload"]["status"]["cancelled"] is True


async def test_auto_approve_toggle_on_another_replica_reaches_the_owned_run(cluster):
    from atlas.application.chat.runs.commands import set_auto_approve

    record = await cluster.run()
    other = await cluster.run()
    assert record.auto_approve_user_tools is False
    # Replica B received the toggle; its own registry holds none of the runs.
    await set_auto_approve(RunRegistry(), OWNER, True)
    assert cluster.registry.get(record.run_id).auto_approve_user_tools is True
    assert cluster.registry.get(other.run_id).auto_approve_user_tools is True
    # Idempotent, and switching off follows the same path.
    await set_auto_approve(RunRegistry(), OWNER, True)
    await set_auto_approve(RunRegistry(), OWNER, False)
    assert cluster.registry.get(record.run_id).auto_approve_user_tools is False


async def test_auto_approve_toggle_never_reaches_another_users_run(cluster):
    from atlas.application.chat.runs.commands import set_auto_approve

    mine = await cluster.run()
    await set_auto_approve(RunRegistry(), OTHER, True)
    assert cluster.registry.get(mine.run_id).auto_approve_user_tools is False
