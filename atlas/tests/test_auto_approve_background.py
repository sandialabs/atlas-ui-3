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
from atlas.application.chat.auto_approve_state import (
    begin_untracked_turn,
    clear_session_auto_approve,
    end_untracked_turn,
    session_auto_approve,
    set_session_auto_approve,
)
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
    clear_session_auto_approve()
    yield get_run_registry()
    clear_current_run()
    reset_run_registry()
    clear_session_auto_approve()


@pytest.fixture(autouse=True)
def _reset_session_auto_approve():
    clear_session_auto_approve()
    yield
    clear_session_auto_approve()


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


@pytest.mark.asyncio
async def test_child_without_a_parent_record_is_not_auto_approved(
    registry, approval_manager, monkeypatch
):
    """An untracked turn launches a child with no parent_run_id.

    Depth and the launch spec are what mark it. The user switch must not
    approve its tools, even if something wrote the bit onto the record.
    """
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    child = registry.start(conversation_id="conv-orphan-child", user_email=USER, depth=1)
    child.launch_spec = {"source": "atlas_launch", "model": "m"}
    child.note_auto_approve({"auto_approve_tools": True})
    assert child.auto_approve_user_tools is False
    assert registry.set_auto_approve_user_tools(USER, True) == 0
    assert child.auto_approve_user_tools is False

    child.auto_approve_user_tools = True
    begin_untracked_turn(child.session_id, True)
    set_current_run(child.run_id, child.conversation_id)
    frames = []
    result = await _run(
        "call_orphan_child",
        {
            "user_email": USER,
            "session_id": child.session_id,
            "auto_approve_tools": True,
        },
        frames,
    )
    assert result.success is False
    assert result.error == "Approval timeout"

    api_run = registry.start(conversation_id="conv-api", user_email=USER)
    api_run.launch_spec = {"source": "agent_runs_api", "model": "m"}
    api_run.note_auto_approve({"auto_approve_tools": True})
    assert api_run.auto_approve_user_tools is False
    assert registry.set_auto_approve_user_tools(USER, True) == 0


def test_session_bit_is_strict_and_does_not_cross_sessions():
    begin_untracked_turn("session-a", True)
    begin_untracked_turn("session-b", False)
    assert session_auto_approve("session-a") is True
    assert session_auto_approve("session-b") is False
    assert set_session_auto_approve("session-a", "true") is True  # type: ignore[arg-type]
    assert session_auto_approve("session-a") is False
    begin_untracked_turn(None, True)
    assert session_auto_approve(None) is None


def test_toggle_without_a_running_turn_leaves_no_entry():
    """A toggle with no untracked turn running has nothing to update."""
    assert set_session_auto_approve("idle", True) is False
    assert session_auto_approve("idle") is None


def test_off_toggle_during_admission_is_not_overwritten():
    """The entry opens before the task, so a toggle in the startup window sticks.

    Nothing writes the admission value again once the turn has begun; the
    service no longer touches the map after its awaits.
    """
    begin_untracked_turn("s-startup", True)
    # The user turns the switch off before the turn reaches its first tool.
    assert set_session_auto_approve("s-startup", False) is True
    assert session_auto_approve("s-startup") is False


def test_overlapping_turns_keep_the_latest_toggle_until_the_last_finishes():
    """One turn finishing must not drop the off-toggle another turn relies on."""
    begin_untracked_turn("s-overlap", True)  # turn being torn down
    begin_untracked_turn("s-overlap", True)  # the next turn
    set_session_auto_approve("s-overlap", False)

    end_untracked_turn("s-overlap")
    assert session_auto_approve("s-overlap") is False

    end_untracked_turn("s-overlap")
    assert session_auto_approve("s-overlap") is None
    end_untracked_turn("s-overlap")  # an extra release is harmless
    assert session_auto_approve("s-overlap") is None


@pytest.mark.asyncio
async def test_untracked_turn_stops_auto_approving_when_the_switch_goes_off(
    approval_manager, monkeypatch
):
    """No run record: the admission snapshot stays true, the live bit moves."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    clear_current_run()
    begin_untracked_turn("s-live", True)
    frames = []

    first = await _run(
        "call_live_on",
        {"user_email": USER, "session_id": "s-live", "auto_approve_tools": True},
        frames,
    )
    assert first.success is True
    assert not any(frame.get("type") == "tool_approval_request" for frame in frames)

    set_session_auto_approve("s-live", False)
    frames.clear()
    second = await _run(
        "call_live_off",
        {"user_email": USER, "session_id": "s-live", "auto_approve_tools": True},
        frames,
    )
    assert second.success is False
    assert second.error == "Approval timeout"
    assert any(frame.get("type") == "tool_approval_request" for frame in frames)


@pytest.mark.asyncio
async def test_untracked_turn_still_approves_when_the_client_is_gone(
    approval_manager, monkeypatch
):
    """Closing the browser does not clear a switch that is still on."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    clear_current_run()
    begin_untracked_turn("s-closed", True)

    result = await execute_single_tool(
        _tool_call("call_closed_untracked"),
        {"user_email": USER, "session_id": "s-closed", "auto_approve_tools": True},
        _FakeToolManager(),
        update_callback=None,
        config_manager=SimpleNamespace(),
    )

    assert result.success is True
    assert result.content == "ran"


@pytest.mark.asyncio
async def test_tracked_run_keeps_its_own_bit_when_the_session_disagrees(
    registry, approval_manager, monkeypatch
):
    """The owning run record outlives the socket and wins over the session bit."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(conversation_id="conv-authority", user_email=USER)
    record.note_auto_approve({"auto_approve_tools": True})
    begin_untracked_turn(record.session_id, False)
    set_current_run(record.run_id, record.conversation_id)

    approved = await execute_single_tool(
        _tool_call("call_record_wins"),
        {
            "user_email": USER,
            "session_id": record.session_id,
            "auto_approve_tools": False,
        },
        _FakeToolManager(),
        update_callback=None,
        config_manager=SimpleNamespace(),
    )
    assert approved.success is True

    record.note_auto_approve({"auto_approve_tools": False})
    set_session_auto_approve(record.session_id, True)
    frames = []
    refused = await _run(
        "call_record_off",
        {
            "user_email": USER,
            "session_id": record.session_id,
            "auto_approve_tools": True,
        },
        frames,
    )
    assert refused.success is False
    assert refused.error == "Approval timeout"
    assert any(frame.get("type") == "tool_approval_request" for frame in frames)


class _EscalatingHooks:
    """A PermissionRequest policy hook that escalates every call to "ask"."""

    def has_hooks(self, event):
        from atlas.hooks import HookEvent

        return event == HookEvent.PERMISSION_REQUEST

    async def run_event(self, event, payload, **kwargs):
        return SimpleNamespace(
            verdict="require_approval", reason="policy", modified=False, payload={}
        )


@pytest.mark.asyncio
async def test_hook_escalation_still_waits_when_the_switch_is_on(
    registry, approval_manager, monkeypatch
):
    """A policy hook asked for a person; the user switch does not answer it."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.get_hook_manager",
        _EscalatingHooks,
    )
    record = registry.start(conversation_id="conv-hook", user_email=USER)
    record.note_auto_approve({"auto_approve_tools": True})
    set_current_run(record.run_id, record.conversation_id)
    frames = []

    result = await _run(
        "call_hook_escalated",
        {"user_email": USER, "session_id": record.session_id, "auto_approve_tools": True},
        frames,
    )

    assert result.success is False
    assert result.error == "Approval timeout"
    request = next(f for f in frames if f.get("type") == "tool_approval_request")
    assert request["admin_required"] is False
    assert request["auto_approvable"] is False


@pytest.mark.asyncio
async def test_approval_frame_tells_the_browser_whether_it_may_auto_answer(
    registry, approval_manager, monkeypatch
):
    """The browser responders honour this field, so it must match the gate."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)

    # Switch off, user-started turn: the browser may answer once it is on.
    clear_current_run()
    frames = []
    await _run("call_frame_plain", {"user_email": USER, "session_id": "s-frame"}, frames)
    request = next(f for f in frames if f.get("type") == "tool_approval_request")
    assert request["auto_approvable"] is True

    # A launched child of an untracked turn: no parent_run_id, still excluded.
    child = registry.start(conversation_id="conv-frame-child", user_email=USER, depth=1)
    child.launch_spec = {"source": "atlas_launch", "model": "m"}
    set_current_run(child.run_id, child.conversation_id)
    assert child.parent_run_id is None
    frames = []
    await _run("call_frame_child", {"user_email": USER, "session_id": child.session_id}, frames)
    request = next(f for f in frames if f.get("type") == "tool_approval_request")
    assert request["auto_approvable"] is False

    # Admin-required: never the user's switch to answer.
    clear_current_run()
    _patch_gate(monkeypatch, admin_required=True, timeout=0.2)
    frames = []
    await _run("call_frame_admin", {"user_email": USER, "session_id": "s-frame"}, frames)
    request = next(f for f in frames if f.get("type") == "tool_approval_request")
    assert request["auto_approvable"] is False


@pytest.mark.asyncio
async def test_run_without_a_record_is_not_auto_approved(
    registry, approval_manager, monkeypatch
):
    """A bound run id the registry does not know is not covered by the switch."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    set_current_run("run-unknown", "conv-unknown")
    begin_untracked_turn("s-unknown", True)
    frames = []

    result = await _run(
        "call_unknown_run",
        {"user_email": USER, "session_id": "s-unknown", "auto_approve_tools": True},
        frames,
    )

    assert result.success is False
    assert result.error == "Approval timeout"
    request = next(f for f in frames if f.get("type") == "tool_approval_request")
    assert request["auto_approvable"] is False


# ---------------------------------------------------------------------------
# A chat frame for another conversation must not orphan the untracked turn
# still running on the connection (review of #1171).
# ---------------------------------------------------------------------------


async def _sleep_forever():
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_superseded_untracked_turn_is_cancelled_and_awaited():
    import atlas.main as main_module

    previous = asyncio.create_task(_sleep_forever())
    await asyncio.sleep(0)
    slot = {"task": previous, "kind": "chat", "steering": None, "conversation_id": "c1"}

    assert await main_module._cancel_superseded_untracked_turn(slot) is True
    assert previous.done()
    assert previous.cancelled()


@pytest.mark.asyncio
async def test_superseded_turn_cancel_leaves_finished_and_other_work_alone():
    import atlas.main as main_module

    # Nothing running on the connection: a tracked run lives in the registry,
    # not in this slot, so an empty slot is the background-run case.
    cancelled_empty = await main_module._cancel_superseded_untracked_turn({"task": None, "kind": None})
    assert cancelled_empty is False

    finished = asyncio.create_task(asyncio.sleep(0))
    await asyncio.wait([finished])
    assert finished.done()
    cancelled_finished = await main_module._cancel_superseded_untracked_turn({"task": finished, "kind": "chat"})
    assert cancelled_finished is False

    compacting = asyncio.create_task(_sleep_forever())
    await asyncio.sleep(0)
    try:
        cancelled_compact = await main_module._cancel_superseded_untracked_turn({"task": compacting, "kind": "compact"})
        assert cancelled_compact is False
        assert not compacting.done()
    finally:
        compacting.cancel()
        await asyncio.wait([compacting])


@pytest.mark.asyncio
async def test_superseded_turn_cancel_is_bounded(monkeypatch):
    import atlas.main as main_module

    monkeypatch.setattr(main_module, "SUPERSEDED_TURN_CANCEL_TIMEOUT", 0.05)

    async def _resists_cancel():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)

    stubborn = asyncio.create_task(_resists_cancel())
    await asyncio.sleep(0)
    try:
        started = asyncio.get_running_loop().time()
        assert await main_module._cancel_superseded_untracked_turn({"task": stubborn, "kind": "chat"}) is True
        assert asyncio.get_running_loop().time() - started < 1.0
    finally:
        stubborn.cancel()
        stubborn.cancel()
        await asyncio.wait([stubborn], timeout=1.0)
