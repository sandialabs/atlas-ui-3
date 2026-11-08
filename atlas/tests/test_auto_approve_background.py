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
async def test_call_with_no_run_uses_the_session_flag(approval_manager, monkeypatch):
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
    """A child launched from a plain turn has no parent_run_id.

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


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_tracked_run_keeps_its_own_bit_when_the_session_disagrees(
    registry, approval_manager, monkeypatch
):
    """The owning run record outlives the socket and wins over the session copy."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(conversation_id="conv-authority", user_email=USER)
    record.note_auto_approve({"auto_approve_tools": True})
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


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_plain_turn_stops_auto_approving_when_the_switch_goes_off(
    registry, approval_manager, monkeypatch
):
    """A plain turn is a run: the toggle moves its record while it executes."""
    _patch_gate(monkeypatch, admin_required=False, timeout=0.2)
    record = registry.start(
        conversation_id="conv-plain", user_email=USER, session_id="s-plain", connection_bound=True
    )
    record.note_auto_approve({"auto_approve_tools": True})
    set_current_run(record.run_id, record.conversation_id)
    context = {"user_email": USER, "session_id": "s-plain", "auto_approve_tools": True}
    frames = []

    first = await _run("call_plain_on", context, frames)
    assert first.success is True
    assert not any(frame.get("type") == "tool_approval_request" for frame in frames)

    assert registry.set_auto_approve_user_tools(USER, False) == 1
    second = await _run("call_plain_off", context, frames)
    assert second.success is False
    assert second.error == "Approval timeout"
    assert any(frame.get("type") == "tool_approval_request" for frame in frames)


# A chat frame for another conversation must not orphan the plain turn still
# running on the connection (review of #1171).


async def _sleep_forever():
    await asyncio.sleep(3600)


def _bound_turn(registry, session_id, conversation_id, work=None):
    record = registry.start(
        conversation_id=conversation_id, user_email=USER, session_id=session_id, connection_bound=True
    )
    task = asyncio.create_task(_sleep_forever())
    if work is not None:
        work.track(task)  # as the endpoint does when it starts the turn
    registry.attach_task(record.run_id, task)
    return record, task


@pytest.mark.asyncio
async def test_superseded_turn_is_cancelled_and_awaited(registry):
    import atlas.main as main_module

    work = main_module._ConnectionWork()
    record, task = _bound_turn(registry, "s-sup", "c1", work)
    other_session, other_task = _bound_turn(registry, "s-other", "c2")
    await asyncio.sleep(0)

    assert await main_module._cancel_connection_turns(registry, "s-sup", USER, work) is True
    assert task.cancelled()
    assert registry.get(record.run_id).status.value == "cancelled"
    # Another connection's turn is not this connection's to cancel.
    assert not other_task.done() and not registry.get(other_session.run_id).is_terminal
    other_task.cancel()


@pytest.mark.asyncio
async def test_superseded_turn_cancel_leaves_finished_and_other_work_alone(registry):
    import atlas.main as main_module

    work = main_module._ConnectionWork()
    assert await main_module._cancel_connection_turns(registry, "s-idle", USER, work) is False

    record, task = _bound_turn(registry, "s-done", "c1", work)
    registry.set_status(record.run_id, main_module.RunStatus.COMPLETED)
    task.cancel()
    await asyncio.wait([task])
    assert await main_module._cancel_connection_turns(registry, "s-done", USER, work) is False

    # A run with its own session is not bound to the connection.
    own = registry.start(conversation_id="c-own", user_email=USER, session_id="s-done")
    own_task = asyncio.create_task(_sleep_forever())
    registry.attach_task(own.run_id, own_task)
    # A compaction is left alone unless the caller asks for it (reset does).
    compaction = asyncio.create_task(_sleep_forever())
    work.compaction = compaction
    await asyncio.sleep(0)
    try:
        assert await main_module._cancel_connection_turns(registry, "s-done", USER, work) is False
        assert not own_task.done() and not compaction.done()
        assert await main_module._cancel_connection_turns(
            registry, "s-done", USER, work, include_compaction=True
        ) is True
        assert compaction.cancelled() and not own_task.done()
    finally:
        own_task.cancel()
        await asyncio.wait([own_task, compaction], timeout=1.0)


@pytest.mark.asyncio
async def test_superseded_turn_cancel_is_bounded(registry, monkeypatch):
    import atlas.main as main_module

    monkeypatch.setattr(main_module, "SUPERSEDED_TURN_CANCEL_TIMEOUT", 0.05)

    async def _resists_cancel():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)

    record = registry.start(
        conversation_id="c-stubborn", user_email=USER, session_id="s-stubborn", connection_bound=True
    )
    work = main_module._ConnectionWork()
    stubborn = asyncio.create_task(_resists_cancel())
    work.track(stubborn)
    registry.attach_task(record.run_id, stubborn)
    await asyncio.sleep(0)
    try:
        started = asyncio.get_running_loop().time()
        assert await main_module._cancel_connection_turns(registry, "s-stubborn", USER, work) is True
        assert asyncio.get_running_loop().time() - started < 1.0
        # Still reachable: Stop, reset and the disconnect cleanup cancel it again.
        assert work.stragglers == [stubborn] and work.busy()
        assert work.cancel_stragglers() == 1
        assert not stubborn.done()
    finally:
        stubborn.cancel()
        stubborn.cancel()
        await asyncio.wait([stubborn], timeout=1.0)


@pytest.mark.asyncio
async def test_reset_waits_for_a_turn_that_stop_already_cancelled(registry):
    """Stop marks the run terminal and drops its task; the turn may still be committing."""
    import atlas.main as main_module

    committing = asyncio.Event()
    release = asyncio.Event()

    async def commits_after_cancel():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            committing.set()
            while not release.is_set():  # the interrupted turn being written
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass

    work = main_module._ConnectionWork()
    record = registry.start(
        conversation_id="c-stop", user_email=USER, session_id="s-stop", connection_bound=True
    )
    task = asyncio.create_task(commits_after_cancel())
    work.track(task)
    registry.attach_task(record.run_id, task)
    await asyncio.sleep(0)
    registry.cancel(record.run_id, USER)  # the Stop frame
    await committing.wait()
    assert registry.active_bound_to("s-stop") == [] and not task.done() and work.busy()

    reset = asyncio.create_task(main_module._cancel_connection_turns(registry, "s-stop", USER, work))
    await asyncio.sleep(0.05)
    assert not reset.done(), "reset did not wait for the turn that is still committing"
    release.set()
    assert await reset is True and task.done()
