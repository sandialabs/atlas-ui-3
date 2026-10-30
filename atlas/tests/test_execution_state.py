"""Shared execution projection (issue #1122)."""

import asyncio

import fakeredis
import pytest

from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    RedisExecutionStore,
    apply_event_to_document,
    invocation_id,
    merge_execution_into_messages,
    merge_run_snapshots,
    public_projection,
    reset_execution_store,
)
from atlas.infrastructure.redis import RedisClientManager, RedisSettings

EMAIL = "owner@example.com"
OTHER = "other@example.com"
CONV = "conv-1122"


def _event(index, **overrides):
    event = {
        "type": "invocation",
        "status": "calling",
        "run_id": "run-1",
        "conversation_id": CONV,
        "user_email": EMAIL,
        "step": 0,
        "tool_call_id": f"call_{index}",
        "tool_name": f"tool_{index}",
        "invocation_id": invocation_id("run-1", 0, f"call_{index}"),
    }
    event.update(overrides)
    return event


def _redis_store(server):
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    return RedisExecutionStore(manager)


@pytest.fixture(autouse=True)
def _isolated_store():
    reset_execution_store()
    yield
    reset_execution_store()


@pytest.mark.asyncio
async def test_parallel_step_is_visible_before_any_tool_finishes():
    store = MemoryExecutionStore()
    pending = _event(3, type="tool_approval_request", arguments={"q": "x"})
    events = [_event(i, type="tool_start") for i in range(10)]
    events[1]["type"] = "tool_complete"
    events[1]["success"] = True
    events[1]["result"] = "done"
    events[3] = pending
    await asyncio.gather(*(store.apply_event(event) for event in events))
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["revision"] == 10
    assert len(projection["invocations"]) == 10
    by_id = {row["tool_call_id"]: row["status"] for row in projection["invocations"]}
    assert by_id["call_1"] == "completed"
    assert by_id["call_0"] == "in_progress"
    assert by_id["call_3"] == "calling"
    assert len(projection["pending_requests"]) == 1
    assert projection["activity"] == "waiting_for_approval"
    assert projection["status"] == "waiting_for_input"


@pytest.mark.asyncio
async def test_two_replicas_share_one_projection():
    server = fakeredis.FakeServer()
    first = _redis_store(server)
    second = _redis_store(server)
    await asyncio.gather(*(first.apply_event(_event(i, type="tool_start")) for i in range(5)))
    await asyncio.gather(*(second.apply_event(_event(i, type="tool_complete", success=True, result="ok")) for i in range(5, 10)))
    left = public_projection(await first.get(CONV, EMAIL))
    right = public_projection(await second.get(CONV, EMAIL))
    assert left["revision"] == right["revision"] == 10
    assert [row["tool_call_id"] for row in left["invocations"]] == [f"call_{i}" for i in range(10)]
    assert right["invocations"][0]["status"] == "in_progress"
    assert right["invocations"][7]["status"] == "completed"
    assert await first.get(CONV, OTHER) is None


@pytest.mark.asyncio
async def test_merge_is_idempotent_and_a_new_run_replaces_the_document():
    store = MemoryExecutionStore()
    await store.apply_event(_event(0, type="tool_complete", success=True, result="one"))
    messages = [{
        "role": "tool",
        "message_type": "tool_call",
        "metadata": {"message_type": "tool_call", "tool_call_id": "call_0", "status": "calling"},
    }]
    projection = public_projection(await store.get(CONV, EMAIL))
    once = merge_execution_into_messages(messages, projection)
    twice = merge_execution_into_messages(once, projection)
    assert len(once) == len(twice) == 1
    assert twice[0]["metadata"]["status"] == "completed"
    assert twice[0]["metadata"]["invocation_id"] == invocation_id("run-1", 0, "call_0")

    await store.apply_event(_event(0, run_id="run-2", invocation_id=invocation_id("run-2", 0, "call_0")))
    replaced = public_projection(await store.get(CONV, EMAIL))
    assert replaced["run_id"] == "run-2"
    assert len(replaced["invocations"]) == 1


@pytest.mark.asyncio
async def test_reused_call_id_updates_only_its_step():
    store = MemoryExecutionStore()
    first = invocation_id("run-1", 0, "call_0")
    second = invocation_id("run-1", 1, "call_0")
    await store.apply_event(_event(0, type="tool_complete", success=True, result="step-0", invocation_id=first))
    await store.apply_event(_event(
        0, type="tool_start", step=1, invocation_id=second, tool_name="atlas_sleep",
    ))
    rows = {row["invocation_id"]: row for row in public_projection(await store.get(CONV, EMAIL))["invocations"]}
    assert rows[first]["status"] == "completed"
    assert rows[first]["result"] == "step-0"
    assert rows[second]["status"] == "sleeping"


@pytest.mark.asyncio
async def test_sleep_keeps_earlier_tools_and_is_not_an_approval():
    store = MemoryExecutionStore()
    await store.apply_event(_event(0, type="tool_complete", success=True, result="listed"))
    await store.apply_event(_event(1, type="tool_start", tool_name="atlas_sleep"))
    projection = public_projection(await store.get(CONV, EMAIL))
    assert [row["status"] for row in projection["invocations"]] == ["completed", "sleeping"]
    assert projection["activity"] == "sleeping"
    assert projection["status"] == "running"
    assert projection["pending_requests"] == []
    assert projection["waiting_on"] is None


@pytest.mark.asyncio
async def test_resolution_is_first_write_wins():
    store = MemoryExecutionStore()
    first = await store.put_resolution("run-1:0:call_0", {"approved": True, "status": "approved"})
    second = await store.put_resolution("run-1:0:call_0", {"approved": False, "status": "rejected"})
    assert first["already_resolved"] is False
    assert second["already_resolved"] is True
    assert second["approved"] is True


def test_reducer_does_not_clear_a_different_invocations_card():
    doc = {
        "run_id": "run-1",
        "conversation_id": CONV,
        "user_email": EMAIL,
        "revision": 0,
        "status": "running",
        "activity": "executing",
        "invocations": {},
        "pending_requests": {},
        "seq": 0,
        "updated_at": 0,
    }
    apply_event_to_document(doc, _event(0, type="tool_approval_request"))
    apply_event_to_document(doc, _event(
        0, type="tool_error", step=1, status="expired", error="Approval timeout",
        invocation_id=invocation_id("run-1", 1, "call_0"),
    ))
    assert invocation_id("run-1", 0, "call_0") in doc["pending_requests"]


def test_merge_run_snapshots_surfaces_a_detached_run():
    local = [{"run_id": "run-1", "conversation_id": CONV, "status": "running"}]
    shared = [{
        "run_id": "run-9",
        "conversation_id": "conv-other",
        "status": "waiting_for_input",
        "activity": "waiting_for_approval",
        "waiting_on": "tool_approval_request",
        "revision": 4,
    }]
    merged = {row["conversation_id"]: row for row in merge_run_snapshots(local, shared)}
    assert merged[CONV]["status"] == "running"
    assert merged["conv-other"]["detached"] is True
    assert merged["conv-other"]["activity"] == "waiting_for_approval"
    assert merged["conv-other"]["execution_revision"] == 4
