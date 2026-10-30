"""Approval settlement and invocation identity (issue #1122)."""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.approval_manager import ToolApprovalManager
from atlas.application.chat.approval_resolution import decide_tool_approval
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    approval_storage_key,
    get_execution_store,
    invocation_id,
    public_projection,
    reset_execution_store,
    set_execution_store,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus
from atlas.application.chat.utilities.tool_executor import execute_single_tool
from atlas.modules.mcp_tools.mcp_routing import RoutingMixin, _ElicitationRoutingContext

EMAIL = "owner@example.com"
CONV = "conv-approval"
RUN = "run-approval"
TOOL = "approval_gated_tool"


class _FakeToolManager:
    def get_tool_schema(self, tool_name):
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    def get_tools_schema(self, *args, **kwargs):
        return []

    async def execute_tool(self, tool_call_obj, context=None):
        return SimpleNamespace(content="ran", success=True, error=None)


def _call(call_id="call_0"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=TOOL, arguments='{"value": "x"}'),
    )


def _config():
    return SimpleNamespace(
        app_settings=SimpleNamespace(tool_approval_timeout_seconds=1),
        get_tool_approvals_config=lambda: {"default_requires_approval": True, "tools": {}},
    )


@pytest.fixture
def store():
    memory = MemoryExecutionStore()
    set_execution_store(memory)
    yield memory
    reset_execution_store()
    clear_current_run()


@pytest.fixture
def approval_gate(monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda tool_name, cm: (True, True, False),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.05,
    )


def _registry():
    registry = RunRegistry()
    record = registry.start(conversation_id=CONV, user_email=EMAIL)
    registry.set_status(record.run_id, RunStatus.RUNNING)
    return registry, record


async def _run_tool(frames, registry, record, session_extra=None):
    async def update_callback(message):
        frames.append(message)
        registry.note_event(record.run_id, message)

    context = {
        "user_email": EMAIL,
        "conversation_id": CONV,
        "session_id": "s1",
        "agent_step": 0,
        "run_id": record.run_id,
    }
    if session_extra:
        context.update(session_extra)
    set_current_run(record.run_id, CONV)
    return await execute_single_tool(
        _call(),
        context,
        _FakeToolManager(),
        update_callback=update_callback,
        config_manager=_config(),
    )


@pytest.mark.asyncio
async def test_timeout_settles_the_run_and_records_the_row(store, approval_gate):
    registry, record = _registry()
    frames = []
    result = await _run_tool(frames, registry, record)
    assert result.success is False
    assert result.error == "Approval timeout"
    errors = [frame for frame in frames if frame.get("type") == "tool_error"]
    assert errors and errors[-1]["status"] == "expired"
    assert errors[-1]["tool_name"] == TOOL
    assert registry.get(record.run_id).status == RunStatus.RUNNING
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["pending_requests"] == []
    assert projection["status"] != "waiting_for_input"
    assert projection["invocations"][0]["status"] == "expired"


@pytest.mark.asyncio
async def test_rejection_settles_with_a_failed_row(store, approval_gate, monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 2,
    )
    registry, record = _registry()
    frames = []

    async def update_callback(message):
        frames.append(message)
        registry.note_event(record.run_id, message)
        if message.get("type") == "tool_approval_request":
            from atlas.application.chat.approval_manager import get_approval_manager
            get_approval_manager().handle_approval_response(
                message["tool_call_id"],
                approved=False,
                reason="nope",
                user_email=EMAIL,
                invocation_id=message.get("invocation_id"),
                run_id=record.run_id,
            )

    set_current_run(record.run_id, CONV)
    result = await execute_single_tool(
        _call(),
        {
            "user_email": EMAIL,
            "conversation_id": CONV,
            "session_id": "s1",
            "agent_step": 0,
            "run_id": record.run_id,
        },
        _FakeToolManager(),
        update_callback=update_callback,
        config_manager=_config(),
    )
    assert result.success is False
    assert "nope" in result.content
    assert any(frame.get("status") == "rejected" for frame in frames)
    assert registry.get(record.run_id).status == RunStatus.RUNNING
    projection = public_projection(await store.get(CONV, EMAIL))
    assert projection["invocations"][0]["status"] == "rejected"
    assert projection["pending_requests"] == []


@pytest.mark.asyncio
async def test_timeout_uses_an_approval_that_won_the_race(store, approval_gate):
    frames = []

    async def update_callback(message):
        frames.append(message)
        if message.get("type") == "tool_approval_request":
            await store.put_resolution(
                approval_storage_key(message["invocation_id"], message["tool_call_id"], EMAIL),
                {
                    "approved": True,
                    "arguments": {"value": "x"},
                    "status": "approved",
                    "invocation_id": message["invocation_id"],
                    "tool_call_id": message["tool_call_id"],
                    "user_email": EMAIL,
                },
            )

    set_current_run(RUN, CONV)
    result = await execute_single_tool(
        _call(),
        {"user_email": EMAIL, "conversation_id": CONV, "session_id": "s1", "agent_step": 0, "run_id": RUN},
        _FakeToolManager(),
        update_callback=update_callback,
        config_manager=_config(),
    )
    assert result.success is True
    assert result.content == "ran"


@pytest.mark.asyncio
async def test_cancel_still_drops_the_approval_request(store, monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda tool_name, cm: (True, False, False),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: None,
    )
    from atlas.application.chat.approval_manager import get_approval_manager
    manager = get_approval_manager()
    seen = asyncio.Event()

    async def update_callback(message):
        if message.get("type") == "tool_approval_request":
            seen.set()

    set_current_run(RUN, CONV)
    task = asyncio.create_task(execute_single_tool(
        _call("call_cancel"),
        {"user_email": EMAIL, "conversation_id": CONV, "session_id": "s1", "agent_step": 2, "run_id": RUN},
        _FakeToolManager(),
        update_callback=update_callback,
        config_manager=_config(),
    ))
    await asyncio.wait_for(seen.wait(), timeout=5)
    task.cancel()
    outcome = None
    try:
        outcome = await task
    except asyncio.CancelledError:
        outcome = "cancelled"
    assert outcome == "cancelled"
    assert manager.find_open_request("call_cancel", invocation_id(RUN, 2, "call_cancel"), RUN) is None


@pytest.mark.asyncio
async def test_two_call_ids_do_not_clobber_and_a_duplicate_is_not_false():
    manager = ToolApprovalManager()
    first = manager.create_approval_request("call_0", "a", {}, user_email=EMAIL, invocation_id="run:0:call_0", run_id="run")
    second = manager.create_approval_request("call_0", "b", {}, user_email=EMAIL, invocation_id="run:1:call_0", run_id="run")
    assert first is not second
    assert manager.handle_approval_response("call_0", True, user_email=EMAIL) is False
    assert manager.handle_approval_response(
        "call_0", True, user_email=EMAIL, invocation_id="run:0:call_0",
    ) is True
    duplicate = manager.handle_approval_response(
        "call_0", False, user_email=EMAIL, invocation_id="run:0:call_0",
    )
    assert isinstance(duplicate, dict)
    assert duplicate["already_resolved"] is True
    assert duplicate["approved"] is True
    assert duplicate is not True
    assert manager.handle_approval_response("only", True, user_email=EMAIL) is False
    lone = manager.create_approval_request("only", "c", {}, user_email=EMAIL)
    assert manager.handle_approval_response("only", True, user_email=EMAIL) is True
    assert lone.future.done()
    # Two outstanding call_0 requests: a bare id must not drop both.
    manager.cleanup_request("call_0")
    assert "run:0:call_0" in manager.get_pending_requests()
    assert "run:1:call_0" in manager.get_pending_requests()
    manager.cleanup_request("run:1:call_0")
    assert "run:1:call_0" not in manager.get_pending_requests()
    assert "run:0:call_0" in manager.get_pending_requests()


@pytest.mark.asyncio
async def test_decide_accepts_locally_and_dedupes(store):
    from atlas.application.chat.approval_manager import get_approval_manager
    manager = get_approval_manager()
    invocation = invocation_id(RUN, 0, "call_0")
    manager.cleanup_request(invocation)
    manager.cleanup_request("call_0")
    manager.create_approval_request(
        "call_0", TOOL, {"value": "x"}, user_email=EMAIL, invocation_id=invocation, run_id=RUN,
    )
    data = {
        "tool_call_id": "call_0",
        "invocation_id": invocation,
        "run_id": RUN,
        "conversation_id": CONV,
        "approved": True,
    }
    kind, frame = await decide_tool_approval(data, EMAIL)
    assert kind == "accepted" and frame is None
    kind, frame = await decide_tool_approval(data, EMAIL)
    assert kind == "duplicate"
    assert frame["type"] == "tool_approval_settled"
    assert frame["already_resolved"] is True
    assert frame["approved"] is True
    manager.cleanup_request(invocation)


@pytest.mark.asyncio
async def test_shared_rejection_wins_over_a_later_local_approval(store):
    from atlas.application.chat.approval_manager import get_approval_manager
    manager = get_approval_manager()
    invocation = invocation_id(RUN, 0, "call_race")
    manager.cleanup_request(invocation)
    request = manager.create_approval_request(
        "call_race", TOOL, {"value": "original"}, user_email=EMAIL,
        invocation_id=invocation, run_id=RUN,
    )
    await store.put_resolution(approval_storage_key(invocation, "call_race", EMAIL), {
        "approved": False,
        "arguments": {"value": "original"},
        "reason": "nope",
        "status": "rejected",
        "invocation_id": invocation,
        "tool_call_id": "call_race",
        "run_id": RUN,
        "user_email": EMAIL,
    })
    kind, frame = await decide_tool_approval({
        "tool_call_id": "call_race",
        "invocation_id": invocation,
        "run_id": RUN,
        "conversation_id": CONV,
        "approved": True,
        "arguments": {"value": "edited"},
    }, EMAIL)
    assert kind == "duplicate"
    assert frame["approved"] is False
    assert request.future.done()
    assert request.future.result()["approved"] is False
    manager.cleanup_request(invocation)


@pytest.mark.asyncio
async def test_decide_on_a_replica_without_the_future_writes_the_outcome(store):
    invocation = invocation_id(RUN, 0, "call_9")
    await store.apply_event({
        "type": "tool_approval_request",
        "run_id": RUN,
        "conversation_id": CONV,
        "user_email": EMAIL,
        "step": 0,
        "tool_call_id": "call_9",
        "tool_name": TOOL,
        "invocation_id": invocation,
        "arguments": {},
    })
    data = {
        "tool_call_id": "call_9",
        "run_id": RUN,
        "conversation_id": CONV,
        "approved": False,
        "reason": "no",
    }
    kind, frame = await decide_tool_approval(data, EMAIL)
    assert kind == "accepted" and frame is None
    stored = await store.read_resolution(approval_storage_key(invocation, "call_9", EMAIL))
    assert stored["approved"] is False
    kind, frame = await decide_tool_approval(data, EMAIL)
    assert kind == "duplicate"
    assert frame["approved"] is False


@pytest.mark.asyncio
async def test_decide_ambiguous_bare_id_is_unknown(store):
    for step in (0, 1):
        await store.apply_event({
            "type": "tool_approval_request",
            "run_id": RUN,
            "conversation_id": CONV,
            "user_email": EMAIL,
            "step": step,
            "tool_call_id": "call_0",
            "tool_name": TOOL,
            "invocation_id": invocation_id(RUN, step, "call_0"),
        })
    kind, frame = await decide_tool_approval(
        {"tool_call_id": "call_0", "conversation_id": CONV, "approved": True},
        EMAIL,
    )
    assert kind == "unknown"
    assert frame["error_type"] == "unknown_tool_approval"


@pytest.mark.asyncio
async def test_untracked_call_ids_do_not_share_a_future():
    manager = ToolApprovalManager()
    first = manager.create_approval_request("call_0", "a", {"n": 1}, user_email=EMAIL)
    second = manager.create_approval_request("call_0", "a", {"n": 2}, user_email="other@example.com")
    assert first is not second
    assert first.future is not second.future
    assert manager.handle_approval_response("call_0", True, user_email=EMAIL) is False
    first.set_response(True, {"n": 1})
    assert second.future.done() is False


@pytest.mark.asyncio
async def test_untracked_timeout_does_not_satisfy_the_next_call(store, monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda tool_name, cm: (True, False, False),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.05,
    )
    clear_current_run()
    timed_out = await execute_single_tool(
        _call("call_0"),
        {"user_email": EMAIL, "conversation_id": CONV, "session_id": "s1"},
        _FakeToolManager(),
        config_manager=_config(),
    )
    assert timed_out.success is False
    assert "timed out" in timed_out.content

    async def approve(message):
        if message.get("type") == "tool_approval_request":
            from atlas.application.chat.approval_manager import get_approval_manager
            get_approval_manager().handle_approval_response(
                message["tool_call_id"], True, user_email="other@example.com",
            )

    fresh = await execute_single_tool(
        _call("call_0"),
        {"user_email": "other@example.com", "conversation_id": CONV, "session_id": "s2"},
        _FakeToolManager(),
        update_callback=approve,
        config_manager=_config(),
    )
    assert fresh.success is True
    assert fresh.content == "ran"


@pytest.mark.asyncio
async def test_other_user_cannot_take_a_stored_approval(store):
    from atlas.application.chat.approval_manager import get_approval_manager
    manager = get_approval_manager()
    invocation = invocation_id(RUN, 0, "call_owned")
    manager.cleanup_request(invocation)
    manager.create_approval_request(
        "call_owned", TOOL, {"value": "secret"}, user_email=EMAIL,
        invocation_id=invocation, run_id=RUN,
    )
    kind, frame = await decide_tool_approval({
        "tool_call_id": "call_owned",
        "invocation_id": invocation,
        "run_id": RUN,
        "approved": True,
        "arguments": {"value": "stolen"},
    }, "other@example.com")
    assert kind == "unknown"
    assert frame["error_type"] == "unknown_tool_approval"
    pending = manager.find_open_request("call_owned", invocation, RUN)
    assert pending is not None and pending.future.done() is False
    manager.cleanup_request(invocation)
    await store.put_resolution(approval_storage_key(invocation, "call_owned", EMAIL), {
        "approved": True,
        "user_email": EMAIL,
        "invocation_id": invocation,
        "tool_call_id": "call_owned",
        "status": "approved",
    })
    kind, frame = await decide_tool_approval({
        "tool_call_id": "call_owned",
        "invocation_id": invocation,
        "run_id": RUN,
        "approved": False,
    }, "other@example.com")
    assert kind == "unknown"
    assert frame["error_type"] == "unknown_tool_approval"


@pytest.mark.asyncio
async def test_elicitation_timeout_emits_a_settle_frame(monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.approval_manager.resolve_approval_timeout",
        lambda: 0.05,
    )

    class Host(RoutingMixin):
        def __init__(self):
            self._elicitation_routing = {}

    tool_call = SimpleNamespace(id="call_0", name="ask")
    frames = []

    async def update_cb(message):
        frames.append(message)

    host = Host()
    host._elicitation_routing[("srv", "call_0")] = _ElicitationRoutingContext(
        "srv", tool_call, update_cb, EMAIL,
    )
    handler = host._create_elicitation_handler("srv")
    result = await handler("Need a value", None, SimpleNamespace(requestedSchema={}), None)
    assert result.action == "cancel"
    settled = [frame for frame in frames if frame.get("type") == "tool_error"]
    assert settled
    assert settled[0]["status"] == "expired"
    assert settled[0]["elicitation_id"]
    assert settled[0]["tool_call_id"] == "call_0"
    assert get_execution_store() is not None
