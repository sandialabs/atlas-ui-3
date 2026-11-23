"""Child run frames keep their own identity (issue #1221).

A child launched by ``atlas_launch_subagent`` sends over the callback the
launcher was handed, which on an on-record launch is the parent's
``observe_tool_callback`` wrapper. These tests push child frames through that
real chain and pin that the parent's tool identity and projection stay out of
them.
"""

import pytest

from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.execution_state import (
    execution_identity,
    get_execution_store,
    observe_tool_callback,
    reset_execution_store,
    stamp_callback_identity,
)
from atlas.application.chat.runs.launcher import _ChildConnection
from atlas.application.chat.runs.registry import RunStatus, get_run_registry, reset_run_registry
from atlas.application.chat.utilities.tool_history import ToolCallRecorder

EMAIL = "owner@example.com"
LAUNCH = "atlas_launch_subagent"


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_execution_store()
    reset_run_registry()
    yield
    clear_current_run()
    reset_execution_store()
    reset_run_registry()


def _setup(wrap=observe_tool_callback):
    registry = get_run_registry()
    parent = registry.start(conversation_id="parent-conv", user_email=EMAIL)
    set_current_run(parent.run_id, parent.conversation_id)
    wire = []

    async def socket(payload):
        wire.append(payload)

    recorder = ToolCallRecorder(socket, EMAIL)
    identity = execution_identity(
        {"user_email": EMAIL, "agent_step": 2, "conversation_id": "parent-conv",
         "run_id": parent.run_id},
        "call_7", LAUNCH,
    )
    callback = wrap(recorder, identity)
    child = registry.start(
        conversation_id="child-conv", user_email=EMAIL,
        parent_run_id=parent.run_id, depth=1,
    )
    return parent, child, identity, callback, recorder, wire


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [observe_tool_callback, stamp_callback_identity])
async def test_child_frames_have_no_parent_tool_identity(wrap):
    parent, child, identity, callback, _recorder, wire = _setup(wrap)
    conn = _ChildConnection(callback, child.run_id, "child-conv")

    await conn.send_json({"type": "token_stream", "token": "hi", "is_first": True})
    await conn.send_json({"type": "agent_update", "update_type": "agent_start"})

    assert [w["type"] for w in wire] == ["token_stream", "agent_update"]
    for frame in wire:
        assert frame["run_id"] == child.run_id
        assert frame["conversation_id"] == "child-conv"
        for key in ("tool_name", "tool_call_id", "invocation_id", "step", "execution_revision"):
            assert key not in frame, (key, frame)


@pytest.mark.asyncio
async def test_child_frames_do_not_touch_either_projection():
    parent, child, identity, callback, recorder, _wire = _setup()
    conn = _ChildConnection(callback, child.run_id, "child-conv")

    await conn.send_json({"type": "token_stream", "token": "hi"})
    await conn.send_json({"type": "agent_update", "update_type": "agent_start"})

    store = get_execution_store()
    child_doc = await store.get("child-conv", EMAIL)
    assert not child_doc or not child_doc.get("invocations")
    parent_doc = await store.get("parent-conv", EMAIL)
    assert not parent_doc or not parent_doc.get("invocations")
    assert not recorder._calls


@pytest.mark.asyncio
async def test_child_own_tool_frames_keep_their_identity():
    parent, child, identity, callback, recorder, wire = _setup()
    conn = _ChildConnection(callback, child.run_id, "child-conv")
    child_identity = {
        "tool_call_id": "child_call", "tool_name": "atlas_sleep", "step": 0,
        "invocation_id": f"{child.run_id}:0:child_call",
    }

    await conn.send_json({"type": "tool_start", **child_identity, "arguments": {}})

    frame = wire[0]
    assert frame["tool_name"] == "atlas_sleep"
    assert frame["tool_call_id"] == "child_call"
    assert frame["invocation_id"] == child_identity["invocation_id"]
    assert not recorder._calls  # parent history stays clean


@pytest.mark.asyncio
async def test_parents_own_frames_are_still_stamped_and_projected():
    parent, child, identity, callback, _recorder, wire = _setup()

    await callback({"type": "tool_progress", "run_id": parent.run_id, "message": "x"})
    await callback({"type": "tool_progress", "message": "no run id"})

    assert all(f["tool_name"] == LAUNCH for f in wire)
    assert all(f["invocation_id"] == identity["invocation_id"] for f in wire)
    doc = await get_execution_store().get("parent-conv", EMAIL)
    assert doc["invocations"]


@pytest.mark.asyncio
async def test_grandchild_frame_does_not_mark_middle_child_waiting():
    registry = get_run_registry()
    root = registry.start(conversation_id="root", user_email=EMAIL)
    middle = registry.start(conversation_id="mid", user_email=EMAIL,
                            parent_run_id=root.run_id, depth=1)
    grand = registry.start(conversation_id="grand", user_email=EMAIL,
                           parent_run_id=middle.run_id, depth=2)
    wire = []

    async def socket(payload):
        wire.append(payload)

    middle_conn = _ChildConnection(socket, middle.run_id, "mid")
    grand_conn = _ChildConnection(middle_conn.send_json, grand.run_id, "grand")

    await grand_conn.send_json({
        "type": "tool_approval_request", "tool_call_id": "call_0",
        "tool_name": "needs_approval", "arguments": {},
    })

    assert grand.status == RunStatus.WAITING_FOR_INPUT
    assert middle.status != RunStatus.WAITING_FOR_INPUT
    assert not middle.pending_requests
    assert wire and wire[0]["run_id"] == grand.run_id


@pytest.mark.asyncio
async def test_childs_own_approval_still_marks_it_waiting():
    registry = get_run_registry()
    root = registry.start(conversation_id="root", user_email=EMAIL)
    child = registry.start(conversation_id="kid", user_email=EMAIL,
                           parent_run_id=root.run_id, depth=1)

    async def socket(payload):
        pass

    conn = _ChildConnection(socket, child.run_id, "kid")
    await conn.send_json({
        "type": "tool_approval_request", "tool_call_id": "call_0",
        "tool_name": "needs_approval", "arguments": {},
    })

    assert child.status == RunStatus.WAITING_FOR_INPUT


@pytest.mark.asyncio
async def test_non_dict_frame_is_still_forwarded_by_the_child_connection():
    registry = get_run_registry()
    root = registry.start(conversation_id="root", user_email=EMAIL)
    child = registry.start(conversation_id="kid", user_email=EMAIL,
                           parent_run_id=root.run_id, depth=1)
    wire = []

    async def socket(payload):
        wire.append(payload)

    conn = _ChildConnection(socket, child.run_id, "kid")
    await conn.send_json("plain text frame")

    assert wire == ["plain text frame"]


@pytest.mark.asyncio
async def test_grandchild_settle_frame_does_not_clear_middle_childs_own_request():
    registry = get_run_registry()
    root = registry.start(conversation_id="root", user_email=EMAIL)
    middle = registry.start(conversation_id="mid", user_email=EMAIL,
                            parent_run_id=root.run_id, depth=1)
    grand = registry.start(conversation_id="grand", user_email=EMAIL,
                           parent_run_id=middle.run_id, depth=2)

    async def socket(payload):
        pass

    middle_conn = _ChildConnection(socket, middle.run_id, "mid")
    grand_conn = _ChildConnection(middle_conn.send_json, grand.run_id, "grand")

    # The middle child is genuinely waiting on its own approval.
    await middle_conn.send_json({
        "type": "tool_approval_request", "tool_call_id": "mid_call",
        "tool_name": "needs_approval", "arguments": {},
    })
    # The grandchild asks and settles a request with the same call id.
    await grand_conn.send_json({
        "type": "tool_approval_request", "tool_call_id": "mid_call",
        "tool_name": "needs_approval", "arguments": {},
    })
    await grand_conn.send_json({"type": "tool_complete", "tool_call_id": "mid_call"})

    assert grand.status == RunStatus.RUNNING
    assert middle.status == RunStatus.WAITING_FOR_INPUT
    assert [r["tool_call_id"] for r in middle.pending_requests] == ["mid_call"]

    # Its own settle frame still clears it.
    await middle_conn.send_json({"type": "tool_complete", "tool_call_id": "mid_call"})
    assert middle.status == RunStatus.RUNNING
