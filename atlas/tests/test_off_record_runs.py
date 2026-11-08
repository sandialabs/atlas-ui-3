"""An off-record run claims its conversation cluster-wide and shares nothing else (#1128).

Incognito and ``save_mode: local`` turns are admitted as runs (every chat turn
is one), and the run holds the shared claim: one run per conversation must hold
across replicas. What lands in Redis for it is the claim's own fields (owner,
generation, status). No title, prompt, launch spec, error text, tool arguments,
projection row or approval outcome may reach it.

The test dumps every key and value of the fake Redis after a run that carried a
marker string through each channel, and asserts the marker is nowhere. The same
run recorded normally proves the dump can see the marker.
"""

import asyncio
import json
from uuid import uuid4

import fakeredis
import pytest

from atlas.application.chat.runs import execution_state
from atlas.application.chat.runs import leases as leases_module
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.coordination import (
    drain_pending_releases,
    set_release_gate,
    set_run_coordinator,
)
from atlas.application.chat.runs.execution_state import (
    RedisExecutionStore,
    execution_identity,
    publish_execution_event,
    reset_execution_store,
    set_execution_store,
)
from atlas.application.chat.runs.leases import RunLeaseGuard, set_lease_guard
from atlas.application.chat.runs.registry import RunRegistry, RunStatus, get_run_registry, reset_run_registry
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
from atlas.infrastructure.redis.run_leases import RedisRunLeases

EMAIL = "owner@example.com"
SECRET = "SECRET-TURN-CONTENT-7f3a"
# The claim's own fields: the only things an off-record run may leave behind.
CLAIM_FIELDS = {
    "run_id", "conversation_id", "user_email", "owner", "generation", "status",
    "lease_expires_at", "interrupted", "snapshot", "error", "source", "created_at",
    "updated_at", "ended_at", "released_at", "replica", "claimed_at",
}


@pytest.fixture(autouse=True)
def _isolation():
    for reset in (reset_execution_store, reset_run_registry):
        reset()
    set_lease_guard(None)
    set_run_coordinator(None)
    set_release_gate(None)
    clear_current_run()
    yield
    clear_current_run()
    set_lease_guard(None)
    set_run_coordinator(None)
    set_release_gate(None)
    for reset in (reset_execution_store, reset_run_registry):
        reset()


async def _dump(client) -> dict:
    """Every key of the fake Redis with its value, whatever the type."""
    dump = {}
    async for raw in client.scan_iter("*"):
        key = raw.decode() if isinstance(raw, bytes) else raw
        kind = (await client.type(key))
        kind = kind.decode() if isinstance(kind, bytes) else kind
        if kind == "string":
            value = await client.get(key)
        elif kind == "hash":
            value = await client.hgetall(key)
        elif kind == "set":
            value = sorted(await client.smembers(key))
        elif kind == "zset":
            value = await client.zrange(key, 0, -1, withscores=True)
        elif kind == "list":
            value = await client.lrange(key, 0, -1)
        else:
            value = await client.xrange(key)
        dump[key] = value
    return dump


def _text(dump) -> str:
    return repr(dump)


async def _run_a_turn(off_record: bool):
    """One run carrying ``SECRET`` through every channel a run writes to."""
    server = fakeredis.FakeServer()
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0", key_prefix="t"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    coordinator = RedisRunCoordinator(manager, replica_id="A", lease_seconds=6.0)
    registry = get_run_registry()
    guard = RunLeaseGuard(coordinator, RedisRunLeases(coordinator), registry, clock=lambda: 1000.0)
    registry.add_observer(guard.observe)
    set_run_coordinator(coordinator)
    set_lease_guard(guard)
    set_release_gate(guard.gate_release)
    set_execution_store(RedisExecutionStore(manager))

    conversation_id = f"conv-{uuid4()}"
    run_id = str(uuid4())
    claim = await coordinator.claim(
        run_id=run_id, conversation_id=conversation_id, user_email=EMAIL, max_runs_per_user=5
    )
    record = registry.start(
        conversation_id=conversation_id, user_email=EMAIL, run_id=run_id,
        title=SECRET, off_record=off_record,
    )
    record.launch_spec = {"source": "ws", "model": SECRET, "tools": [SECRET]}
    record.claim = claim
    task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    registry.attach_task(run_id, task)

    async def settle():
        for _ in range(20):
            await asyncio.sleep(0)
            if guard._pending:
                await asyncio.gather(*list(guard._pending))

    await settle()

    # A tool call with the content in its arguments, as the tool executor
    # publishes it: identity first, then the frames it observes.
    set_current_run(run_id, conversation_id)
    identity = execution_identity({"user_email": EMAIL}, "call_0", "some_tool")
    event = {**identity, "type": "invocation", "status": "calling", "arguments": {"q": SECRET},
             "user_email": EMAIL, "invocation_id": f"{run_id}:0:call_0"}
    await publish_execution_event(event)
    await publish_execution_event({**event, "type": "tool_complete", "result": SECRET})
    await settle()
    clear_current_run()

    task.cancel()
    registry.set_status(run_id, RunStatus.FAILED, error=SECRET)
    await settle()
    await drain_pending_releases()
    await settle()
    return record, identity, await _dump(manager.client), coordinator


async def test_off_record_run_writes_only_the_claim_fields_to_redis():
    record, identity, dump, coordinator = await _run_a_turn(off_record=True)
    assert SECRET not in _text(dump), "off-record content reached Redis"
    # The invocation id keys approval outcomes in the shared store; without one
    # they stay process-local, as they did before every turn was a run.
    assert not identity.get("invocation_id")
    run_key = coordinator.keys.run(record.run_id)
    fields = {k.decode() if isinstance(k, bytes) else k: v for k, v in dump[run_key].items()}
    assert set(fields) <= CLAIM_FIELDS
    assert fields.get("owner") or fields.get("status")
    assert json.loads(fields.get("snapshot") or b"{}") == {}
    assert not (fields.get("error") or b"")
    assert (fields.get("source") or b"") in (b"", "")
    # No projection document or approval outcome was created for it.
    assert not [k for k in dump if "exec" in k or "proj" in k or "approval" in k]


async def test_the_same_run_recorded_normally_does_write_its_content():
    # Proves the dump above would have found the marker.
    _record, _identity, dump, _coordinator = await _run_a_turn(off_record=False)
    assert SECRET in _text(dump)


async def test_off_record_run_publishes_no_lifecycle_event_to_the_projection(monkeypatch):
    seen = []

    async def spy(event):
        seen.append(event)

    monkeypatch.setattr(execution_state, "publish_execution_event", spy)
    registry = RunRegistry()
    off = registry.start(conversation_id="conv-off", user_email=EMAIL, off_record=True)
    registry.set_status(off.run_id, RunStatus.COMPLETED)
    on = registry.start(conversation_id="conv-on", user_email=EMAIL)
    registry.set_status(on.run_id, RunStatus.COMPLETED)
    for _ in range(5):
        await asyncio.sleep(0)
    assert {e["run_id"] for e in seen} == {on.run_id}
    assert leases_module  # the module under test is imported for the mirror path


# ---------------------------------------------------------------------------
# The WebSocket admits every turn as a run.
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_service():
    from unittest.mock import AsyncMock, MagicMock, patch

    from atlas.application.chat.runs.control_bus import set_run_control_bus

    with patch("main.app_factory") as mock_factory:
        cfg = MagicMock()
        cfg.app_settings.test_user = None
        cfg.app_settings.debug_mode = False
        cfg.app_settings.auth_user_header = "X-User-Email"
        cfg.app_settings.auth_user_header_type = "header"
        cfg.app_settings.auth_aws_expected_alb_arn = ""
        cfg.app_settings.auth_aws_region = "us-east-1"
        cfg.app_settings.feature_proxy_secret_enabled = False
        cfg.app_settings.feature_websocket_origin_check_enabled = False
        cfg.app_settings.feature_chat_history_enabled = True
        cfg.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = cfg
        service = MagicMock()
        service.handle_chat_message = AsyncMock(return_value={})
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        service.steering_classification_refusal = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = service
        set_run_control_bus(None)
        yield service
        reset_run_registry()


def _turn(extra):
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": EMAIL}) as ws:
        ws.send_json({"type": "chat", "content": SECRET, "model": "m", **extra})
        frame = ws.receive_json()
        while frame.get("type") != "run_started":
            frame = ws.receive_json()
        return frame


@pytest.mark.parametrize("extra", [{"incognito": True}, {"save_mode": "local"}])
def test_an_off_record_turn_without_a_conversation_is_admitted_as_a_run(ws_service, extra):
    frame = _turn(extra)
    [record] = get_run_registry().all_records()
    assert record.off_record is True and record.connection_bound is True
    assert frame["run_id"] == record.run_id and frame["connection_bound"] is True
    # The prompt is not the run's name, and the turn runs on the connection's
    # own session under the conversation that session already holds.
    assert frame["title"] is None and record.title is None
    kwargs = ws_service.handle_chat_message.await_args.kwargs
    assert kwargs["session_id"] == record.session_id and kwargs["conversation_id"] is None
    assert frame["conversation_id"] == record.conversation_id == str(record.session_id)


def test_a_recorded_plain_turn_is_a_run_on_the_connection_session(ws_service):
    frame = _turn({})
    [record] = get_run_registry().all_records()
    assert record.off_record is False and record.connection_bound is True
    assert frame["title"] == SECRET
