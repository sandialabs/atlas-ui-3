"""Server-side auto-approve must not wait on a connected browser.

The switch used to be answered only by the page that received
``tool_approval_request``. Closing that page left the run parked until
``TOOL_APPROVAL_TIMEOUT``. The preference now lives on the run, and the tool
gate approves user-level tools itself. Admin-mandated tools, a switch that is
off, and a launched child still wait for an explicit answer.
"""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.registry import get_run_registry, reset_run_registry
from atlas.application.chat.utilities.tool_executor import execute_single_tool

USER = "owner@example.com"


class _FakeToolManager:
    def __init__(self):
        self.calls = 0

    def get_tool_schema(self, tool_name):
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    def get_tools_schema(self, *args, **kwargs):
        return []

    async def execute_tool(self, tool_call_obj, context=None):
        self.calls += 1
        return SimpleNamespace(content="ran", success=True, error=None)


def _tool_call(call_id):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name="approval_gated_tool", arguments='{"value": "x"}'),
    )


@pytest.fixture
def registry():
    reset_run_registry()
    clear_current_run()
    yield get_run_registry()
    clear_current_run()
    reset_run_registry()


@pytest.fixture
def approval_manager():
    manager = get_approval_manager()
    yield manager
    for request in list(manager.get_pending_requests().values()):
        manager.cleanup_request(request.tool_call_id, user_email=request.user_email)


def _patch_gate(monkeypatch, *, admin_required, timeout):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda tool_name, cm: (True, True, admin_required),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: timeout,
    )


async def _run(call_id, session_context, frames, **kwargs):
    async def update_callback(message):
        frames.append(message)

    return await execute_single_tool(
        _tool_call(call_id),
        session_context,
        _FakeToolManager(),
        update_callback=update_callback,
        config_manager=SimpleNamespace(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_opted_in_run_executes_without_a_client(registry, approval_manager, monkeypatch):
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(conversation_id="conv-1", user_email=USER)
    record.note_auto_approve({"auto_approve_tools": True})
    set_current_run(record.run_id, record.conversation_id)
    frames = []

    result = await _run("call_auto_1", {"user_email": USER, "session_id": "s1"}, frames)

    assert result.success is True
    assert result.content == "ran"
    assert not any(frame.get("type") == "tool_approval_request" for frame in frames)
    assert not any(
        request.tool_call_id == "call_auto_1"
        for request in approval_manager.get_pending_requests().values()
    )


@pytest.mark.asyncio
async def test_opted_in_run_still_executes_when_no_callback_is_attached(
    registry, approval_manager, monkeypatch
):
    """A detached run has nowhere to send the approval frame."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(conversation_id="conv-detached", user_email=USER)
    record.auto_approve_user_tools = True
    set_current_run(record.run_id, record.conversation_id)

    result = await execute_single_tool(
        _tool_call("call_auto_detached"),
        {"user_email": USER, "session_id": "s1"},
        _FakeToolManager(),
        update_callback=None,
        config_manager=SimpleNamespace(),
    )

    assert result.success is True
    assert result.content == "ran"


@pytest.mark.asyncio
async def test_switch_off_waits_for_an_explicit_answer(registry, approval_manager, monkeypatch):
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(conversation_id="conv-manual", user_email=USER)
    record.note_auto_approve({"auto_approve_tools": False})
    set_current_run(record.run_id, record.conversation_id)
    frames = []

    result = await _run("call_manual_timeout", {"user_email": USER, "session_id": "s1"}, frames)

    assert result.success is False
    assert result.error == "Approval timeout"
    assert any(frame.get("type") == "tool_approval_request" for frame in frames)


@pytest.mark.asyncio
async def test_switch_off_still_runs_after_an_explicit_approval(
    registry, approval_manager, monkeypatch
):
    _patch_gate(monkeypatch, admin_required=False, timeout=2)
    record = registry.start(conversation_id="conv-answer", user_email=USER)
    set_current_run(record.run_id, record.conversation_id)
    seen = asyncio.Event()
    frames = []

    async def update_callback(message):
        frames.append(message)
        if message.get("type") == "tool_approval_request":
            seen.set()

    task = asyncio.create_task(
        execute_single_tool(
            _tool_call("call_manual_ok"),
            {"user_email": USER, "session_id": "s1"},
            _FakeToolManager(),
            update_callback=update_callback,
            config_manager=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(seen.wait(), timeout=2)
    assert approval_manager.handle_approval_response(
        "call_manual_ok", True, user_email=USER, run_id=record.run_id
    ) is True

    result = await asyncio.wait_for(task, timeout=2)
    assert result.success is True
    assert result.content == "ran"


@pytest.mark.asyncio
async def test_admin_required_tool_still_waits_when_the_switch_is_on(
    registry, approval_manager, monkeypatch
):
    _patch_gate(monkeypatch, admin_required=True, timeout=0.2)
    record = registry.start(conversation_id="conv-admin", user_email=USER)
    record.auto_approve_user_tools = True
    set_current_run(record.run_id, record.conversation_id)
    frames = []

    result = await _run("call_admin", {"user_email": USER, "session_id": "s1"}, frames)

    assert result.success is False
    assert result.error == "Approval timeout"
    assert frames[0]["type"] == "tool_approval_request"
    assert frames[0]["admin_required"] is True


@pytest.mark.asyncio
async def test_launched_child_is_not_covered_by_the_parent_switch(
    registry, approval_manager, monkeypatch
):
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    parent = registry.start(conversation_id="conv-parent", user_email=USER)
    parent.auto_approve_user_tools = True
    child = registry.start(
        conversation_id="conv-child",
        user_email=USER,
        parent_run_id=parent.run_id,
        parent_conversation_id=parent.conversation_id,
        depth=1,
    )
    child.auto_approve_user_tools = True
    child.note_auto_approve({"auto_approve_tools": True})
    assert child.auto_approve_user_tools is True
    set_current_run(child.run_id, child.conversation_id)
    frames = []

    result = await _run(
        "call_child",
        {"user_email": USER, "session_id": "s1", "auto_approve_tools": True},
        frames,
    )

    assert result.success is False
    assert result.error == "Approval timeout"
    assert any(frame.get("type") == "tool_approval_request" for frame in frames)


@pytest.mark.asyncio
async def test_untracked_turn_uses_the_session_flag(approval_manager, monkeypatch):
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    clear_current_run()
    frames = []

    result = await _run(
        "call_session",
        {"user_email": USER, "session_id": "s1", "auto_approve_tools": True},
        frames,
    )

    assert result.success is True
    assert not any(frame.get("type") == "tool_approval_request" for frame in frames)

    frames.clear()
    refused = await _run(
        "call_session_string",
        {"user_email": USER, "session_id": "s1", "auto_approve_tools": "true"},
        frames,
    )
    assert refused.success is False
    assert refused.error == "Approval timeout"


def test_preference_frame_is_strict_and_does_not_cross_users(registry):
    mine = registry.start(conversation_id="conv-mine", user_email=USER)
    other = registry.start(conversation_id="conv-other", user_email="other@example.com")
    parent = mine
    child = registry.start(
        conversation_id="conv-launched",
        user_email=USER,
        parent_run_id=parent.run_id,
        parent_conversation_id=parent.conversation_id,
        depth=1,
    )

    mine.note_auto_approve({})
    assert mine.auto_approve_user_tools is False
    mine.note_auto_approve({"auto_approve_tools": "true"})
    assert mine.auto_approve_user_tools is False
    mine.note_auto_approve({"auto_approve_tools": True})
    assert mine.auto_approve_user_tools is True

    changed = registry.set_auto_approve_user_tools(USER, False)
    assert changed == 1
    assert mine.auto_approve_user_tools is False
    assert other.auto_approve_user_tools is False
    assert child.auto_approve_user_tools is False

    child.auto_approve_user_tools = True
    registry.set_auto_approve_user_tools(USER, False)
    assert child.auto_approve_user_tools is True
