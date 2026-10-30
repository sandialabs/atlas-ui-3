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
    set_execution_store,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus
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


def _blank_doc():
    return {
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


def test_pending_approval_arguments_are_not_truncated():
    doc = _blank_doc()
    body = "a" * 9000
    apply_event_to_document(doc, _event(0, type="tool_approval_request", arguments={"body": body}))
    stored = doc["pending_requests"][invocation_id("run-1", 0, "call_0")]
    assert stored["arguments"]["body"] == body


def test_terminal_run_status_clears_pending_and_ignores_a_later_tool_start():
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_approval_request", arguments={"q": "x"}))
    apply_event_to_document(doc, {
        "type": "run_status",
        "status": "completed",
        "run_id": "run-1",
        "error": "done",
    })
    assert doc["status"] == "completed"
    assert doc["pending_requests"] == {}
    assert doc["waiting_on"] is None
    apply_event_to_document(doc, _event(0, type="tool_start"))
    assert doc["status"] == "completed"
    assert doc["activity"] == "completed"
    apply_event_to_document(doc, {"type": "run_status", "status": "failed", "run_id": "run-1"})
    assert doc["status"] == "completed"


def test_merge_run_snapshots_does_not_resurrect_a_finished_run():
    local = [{"run_id": "run-1", "conversation_id": CONV, "status": "completed"}]
    shared = [{
        "run_id": "run-1",
        "conversation_id": CONV,
        "status": "waiting_for_input",
        "activity": "waiting_for_approval",
        "waiting_on": "tool_approval_request",
        "revision": 8,
    }]
    merged = merge_run_snapshots(local, shared)
    assert len(merged) == 1
    assert merged[0]["status"] == "completed"
    assert merged[0]["execution_revision"] == 8
    assert "activity" not in merged[0]


def test_finished_projection_without_a_local_run_is_omitted():
    shared = [{
        "run_id": "run-9",
        "conversation_id": "conv-done",
        "status": "failed",
        "revision": 2,
    }]
    assert merge_run_snapshots([], shared) == []


def test_terminal_projection_finishes_a_local_running_run():
    local = [{"run_id": "run-1", "conversation_id": CONV, "status": "running"}]
    shared = [{
        "run_id": "run-1",
        "conversation_id": CONV,
        "status": "cancelled",
        "activity": "cancelled",
        "revision": 5,
    }]
    merged = merge_run_snapshots(local, shared)[0]
    assert merged["status"] == "cancelled"
    assert merged["activity"] == "cancelled"


@pytest.mark.asyncio
async def test_set_status_publishes_a_terminal_projection():
    store = MemoryExecutionStore()
    set_execution_store(store)
    registry = RunRegistry()
    record = registry.start(conversation_id=CONV, user_email=EMAIL)
    registry.set_status(record.run_id, RunStatus.RUNNING)
    await store.apply_event({
        "type": "tool_approval_request",
        "run_id": record.run_id,
        "conversation_id": CONV,
        "user_email": EMAIL,
        "step": 0,
        "tool_call_id": "call_0",
        "tool_name": "tool_0",
        "invocation_id": invocation_id(record.run_id, 0, "call_0"),
        "arguments": {"q": "x"},
    })
    registry.set_status(record.run_id, RunStatus.COMPLETED, error="done")
    await asyncio.sleep(0)
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["status"] == "completed"
    assert projection["pending_requests"] == []


@pytest.mark.asyncio
async def test_late_terminal_event_does_not_replace_the_newer_run():
    store = MemoryExecutionStore()
    await store.apply_event(_event(0, type="tool_start", run_id="run-2", invocation_id=invocation_id("run-2", 0, "call_0")))
    await store.apply_event({
        "type": "run_status",
        "status": "completed",
        "run_id": "run-1",
        "conversation_id": CONV,
        "user_email": EMAIL,
    })
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["run_id"] == "run-2"
    assert projection["status"] == "running"
    assert projection["invocations"][0]["invocation_id"] == invocation_id("run-2", 0, "call_0")


@pytest.mark.asyncio
async def test_late_opening_event_does_not_revive_a_replaced_run():
    store = MemoryExecutionStore()
    await store.apply_event(_event(0, type="tool_start", run_id="run-1"))
    await store.apply_event(_event(
        0, type="tool_start", run_id="run-2",
        invocation_id=invocation_id("run-2", 0, "call_0"),
    ))
    await store.apply_event(_event(0, type="tool_progress", run_id="run-1"))
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["run_id"] == "run-2"
    assert [item["invocation_id"] for item in projection["invocations"]] == [
        invocation_id("run-2", 0, "call_0"),
    ]


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


def _history_row(**metadata):
    meta = {"message_type": "tool_call", **metadata}
    return {"role": "tool", "message_type": "tool_call", "metadata": meta}


def test_merge_keeps_a_terminal_history_row_intact():
    # History is the durable copy. A projection that lags it (in_progress) or
    # holds a shortened result must not overwrite a settled row.
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_start", arguments={"q": "short"}))
    projection = public_projection(doc)
    full = "r" * 9000
    stored = _history_row(
        tool_call_id="call_0", invocation_id=invocation_id("run-1", 0, "call_0"),
        status="completed", result=full, arguments={"q": "full"},
    )
    merged = merge_execution_into_messages([stored], projection)
    assert len(merged) == 1
    meta = merged[0]["metadata"]
    assert meta["status"] == "completed"
    assert meta["result"] == full
    assert meta["arguments"] == {"q": "full"}


def test_merge_does_not_blank_an_open_rows_result():
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_progress"))
    stored = _history_row(
        tool_call_id="call_0", invocation_id=invocation_id("run-1", 0, "call_0"),
        status="calling", result="partial",
    )
    meta = merge_execution_into_messages([stored], public_projection(doc))[0]["metadata"]
    assert meta["status"] == "in_progress"
    assert meta["result"] == "partial"


def test_merge_ignores_a_finished_run():
    # A finished run's rows are in history. Merging its projection back would
    # resurrect rows a rewind or edit removed.
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_start"))
    apply_event_to_document(doc, {"type": "run_status", "status": "completed", "run_id": "run-1"})
    assert merge_execution_into_messages([], public_projection(doc)) == []


def test_finished_legacy_row_is_not_claimed_by_a_new_invocation():
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_start"))
    earlier = _history_row(tool_call_id="call_0", status="completed", result="old turn")
    merged = merge_execution_into_messages([earlier], public_projection(doc))
    assert len(merged) == 2
    assert merged[0]["metadata"] == earlier["metadata"]
    assert merged[1]["metadata"]["invocation_id"] == invocation_id("run-1", 0, "call_0")


def test_run_end_closes_open_invocations():
    doc = _blank_doc()
    apply_event_to_document(doc, _event(0, type="tool_start", tool_name="atlas_sleep"))
    apply_event_to_document(doc, _event(1, type="tool_start"))
    apply_event_to_document(doc, _event(2, type="tool_complete", success=True, result="ok"))
    apply_event_to_document(doc, {"type": "run_status", "status": "cancelled", "run_id": "run-1"})
    statuses = {row["tool_call_id"]: row["status"] for row in public_projection(doc)["invocations"]}
    assert statuses == {"call_0": "interrupted", "call_1": "interrupted", "call_2": "completed"}


@pytest.mark.asyncio
async def test_memory_store_evicts_past_its_cap(monkeypatch):
    from atlas.application.chat.runs import execution_state

    monkeypatch.setattr(execution_state, "_DOC_CAP", 3)
    store = MemoryExecutionStore()
    for index in range(6):
        await store.apply_event(_event(0, conversation_id=f"conv-{index}"))
    assert len(store._docs) == 3
    assert await store.get("conv-5", EMAIL) is not None
    assert await store.get("conv-0", EMAIL) is None
    assert {conv for conv in store._by_user[EMAIL]} == {"conv-3", "conv-4", "conv-5"}


@pytest.mark.asyncio
async def test_memory_store_sweeps_expired_documents(monkeypatch):
    from atlas.application.chat.runs import execution_state

    monkeypatch.setattr(execution_state, "_SWEEP_EVERY", 1)
    store = MemoryExecutionStore()
    await store.apply_event(_event(0, conversation_id="stale"))
    store._docs[(EMAIL, "stale")]["updated_at"] = 0
    await store.apply_event(_event(0, conversation_id="fresh"))
    assert (EMAIL, "stale") not in store._docs
    assert store._by_user[EMAIL] == {"fresh"}


@pytest.mark.asyncio
async def test_redis_locks_are_per_conversation_and_released():
    store = _redis_store(fakeredis.FakeServer())
    await asyncio.gather(*(
        store.apply_event(_event(i, type="tool_start", conversation_id=f"conv-{i % 2}"))
        for i in range(10)
    ))
    assert len(public_projection(await store.get("conv-0", EMAIL))["invocations"]) == 5
    assert len(public_projection(await store.get("conv-1", EMAIL))["invocations"]) == 5
    assert store._locks == {}
