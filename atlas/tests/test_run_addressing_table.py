"""Table-driven tests for docs/developer/run-addressing.md (issue #1128).

Every row of the table is either tested here (the row id names the test case)
or names an existing test in its ``test`` column. Rows the doc marks ``GAP``
are ``xfail(strict=True)``: they assert the expected outcome, which today's
code does not deliver. When a later change closes a gap, the strict xfail
fails the run and the marker has to come off together with the ``GAP`` label.

Two replicas share one fakeredis. ``cluster.registry`` is replica A's run
registry (the owner of the "remote" runs); the registry handed to the stop
handler is replica B's, the replica that received the frame.

This file describes today's behaviour and changes no production code.
"""

# ruff: noqa: F811
import asyncio
import contextlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_run_control_service import (  # noqa: F401  (fixtures and helpers)
    OWNER,
    _ApiReplicas,
    _pending_approval,
    _release,
    cluster,
)

from atlas.application.chat.agent.steering import SteeringChannel
from atlas.application.chat.runs import run_control
from atlas.application.chat.runs.commands import (
    cancel_api_run_addressed,
    steer_addressed_run,
    stop_addressed_run,
)
from atlas.application.chat.runs.launcher import LaunchRefused
from atlas.application.chat.runs.registry import RunRegistry, RunStatus

DOC = Path(__file__).resolve().parents[2] / "docs" / "developer" / "run-addressing.md"
THIS_FILE = "atlas/tests/test_run_addressing_table.py"
CONV = "conv-addr"


def gap(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


# ---------------------------------------------------------------------------
# ws stop_streaming / ws agent_control stop
# ---------------------------------------------------------------------------


class _Untracked:
    """The connection's untracked turn (``active_chat_task``)."""

    def __init__(self, live=True):
        self.task = MagicMock()
        self.task.done.return_value = not live
        self.slot = {"task": self.task}

    @property
    def cancelled(self):
        return self.task.cancel.called


# Rows with no ``cluster`` argument run with no run-control bus bound, as a
# single-instance deployment does: nothing is read from a directory.
async def ws_stop(local, data, untracked=None):
    """The stop branch of ``websocket_endpoint`` (``stop_streaming`` and
    ``agent_control`` stop both call ``stop_addressed_run``): returns (frames
    sent, untracked turn cancelled)."""
    untracked = untracked or _Untracked(live=False)
    ws = MagicMock()
    ws.send_json = AsyncMock()
    await stop_addressed_run(ws, local, OWNER, data, untracked.slot)
    return [c.args[0] for c in ws.send_json.await_args_list], untracked.cancelled


def _local_run(local, conversation_id=CONV, **kwargs):
    record = local.start(conversation_id=conversation_id, user_email=OWNER, **kwargs)
    local.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
    return record


@pytest.fixture
async def local():
    registry = RunRegistry()
    yield registry
    tasks = [r.task for r in registry.all_records() if r.task is not None]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _unreadable(cluster):
    # Let replica A's inbox reader take its first turn: the shared fixture's
    # teardown cannot stop a reader that never started (hangs on Python 3.11).
    await asyncio.sleep(0.05)
    async def fail(*_a, **_k):
        raise TimeoutError("redis timed out")

    cluster.b.bus.owner_of = fail
    cluster.b.bus.run_for_conversation = fail


async def _finished_parent_with_live_child_on_a(cluster):
    parent = await cluster.run()
    child = cluster.registry.start(conversation_id="conv-child", user_email=OWNER,
                                   parent_run_id=parent.run_id, depth=1)
    cluster.registry.attach_task(child.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.a.claim(child.run_id, child.conversation_id)
    cluster.registry.set_status(parent.run_id, RunStatus.COMPLETED)
    await _release(cluster, parent)
    return parent, child


async def _finished_with_follow_up_on_a(cluster):
    """A finished API run and a live API follow-up on the same conversation, both on A."""
    from atlas.application.chat.runs.external_runs import API_SOURCE

    first = await cluster.run()
    first.launch_spec = {"source": API_SOURCE}
    cluster.registry.set_status(first.run_id, RunStatus.COMPLETED)
    await _release(cluster, first)
    follow_up = cluster.registry.start(conversation_id=first.conversation_id, user_email=OWNER)
    follow_up.launch_spec = {"source": API_SOURCE}
    cluster.registry.attach_task(follow_up.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.a.claim(follow_up.run_id, first.conversation_id)
    for record in (first, follow_up):
        # What the owner's lease mirror writes into the shared record.
        await cluster.b.bus._client().hset(cluster.b.coordinator.keys.run(record.run_id), "source", API_SOURCE)
    return first, follow_up


def _is_unavailable(frames, run_id=None):
    assert [f.get("error_type") for f in frames] == ["run_control_unavailable"], frames
    if run_id:
        assert frames[0]["run_id"] == run_id


async def test_S01_run_id_only_tracked_local(local):
    run = _local_run(local)
    frames, untracked = await ws_stop(local, {"run_id": run.run_id}, _Untracked())
    assert local.get(run.run_id).status is RunStatus.CANCELLED and not frames and not untracked


async def test_S02_run_id_only_tracked_remote_live(cluster, local):
    run = await cluster.run()
    frames, untracked = await ws_stop(local, {"run_id": run.run_id}, _Untracked())
    assert [f["type"] for f in frames] == ["run_status"] and not untracked
    assert cluster.registry.get(run.run_id).status is RunStatus.CANCELLED


async def test_S03_run_id_only_tracked_remote_terminal(cluster, local):
    run = await cluster.run()
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    frames, _ = await ws_stop(local, {"run_id": run.run_id})
    assert [f["type"] for f in frames] == ["run_status"]
    assert cluster.registry.get(run.run_id).status is RunStatus.COMPLETED


async def test_S04_run_id_only_tracked_unreadable(cluster, local):
    run = await cluster.run()
    await _unreadable(cluster)
    frames, _ = await ws_stop(local, {"run_id": run.run_id})
    _is_unavailable(frames, run.run_id)
    assert not cluster.registry.get(run.run_id).is_terminal


async def test_S05_run_id_only_untracked_stale_id(local):
    frames, untracked = await ws_stop(local, {"run_id": "stale"}, _Untracked())
    # The id names a run, so it never reaches the connection's untracked turn.
    assert frames == [] and not untracked


async def test_S06_run_id_only_untracked_unreadable(cluster, local):
    await _unreadable(cluster)
    frames, untracked = await ws_stop(local, {"run_id": "stale"}, _Untracked())
    _is_unavailable(frames, "stale")
    assert not untracked


async def test_S07_run_id_only_finished_parent_local(local):
    parent = local.start(conversation_id=CONV, user_email=OWNER)
    child = _local_run(local, "conv-child", parent_run_id=parent.run_id, depth=1)
    local.set_status(parent.run_id, RunStatus.COMPLETED)
    await ws_stop(local, {"run_id": parent.run_id})
    assert local.get(child.run_id).status is RunStatus.CANCELLED


async def test_S08_run_id_only_finished_parent_remote_terminal(cluster, local):
    parent, child = await _finished_parent_with_live_child_on_a(cluster)
    frames, _ = await ws_stop(local, {"run_id": parent.run_id})
    assert [f["type"] for f in frames] == ["run_status"]
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


@gap("the directory record outlives only 300 s, the owner's registry 30 min: a stop naming a "
     "finished parent past the record is a silent no-op and its children keep running")
async def test_S09_run_id_only_finished_parent_record_expired(cluster, local):
    parent, child = await _finished_parent_with_live_child_on_a(cluster)
    await cluster.b.bus._client().delete(cluster.b.coordinator.keys.run(parent.run_id))
    await ws_stop(local, {"run_id": parent.run_id})
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


async def test_S10_run_id_only_finished_parent_unreadable(cluster, local):
    parent, child = await _finished_parent_with_live_child_on_a(cluster)
    await _unreadable(cluster)
    frames, _ = await ws_stop(local, {"run_id": parent.run_id})
    _is_unavailable(frames, parent.run_id)
    assert not cluster.registry.get(child.run_id).is_terminal


async def test_S11_run_id_only_api_follow_up_local(local):
    first = local.start(conversation_id=CONV, user_email=OWNER)
    local.set_status(first.run_id, RunStatus.COMPLETED)
    follow_up = _local_run(local)
    await ws_stop(local, {"run_id": first.run_id})
    # The named run only: nothing of it is live, and the follow-up keeps going.
    assert not local.get(follow_up.run_id).is_terminal


async def test_S12_conversation_only_tracked_local(local):
    run = _local_run(local)
    frames, untracked = await ws_stop(local, {"conversation_id": CONV}, _Untracked())
    assert local.get(run.run_id).status is RunStatus.CANCELLED and not frames and not untracked


async def test_S13_conversation_only_tracked_remote_live(cluster, local):
    run = await cluster.run()
    frames, _ = await ws_stop(local, {"conversation_id": run.conversation_id})
    assert [f["type"] for f in frames] == ["run_status"]
    assert cluster.registry.get(run.run_id).status is RunStatus.CANCELLED


async def test_S14_conversation_only_tracked_remote_terminal(cluster, local):
    run = await cluster.run()
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    frames, untracked = await ws_stop(local, {"conversation_id": run.conversation_id}, _Untracked())
    # Nothing is running: no frame, and the status stays what the run ended as.
    assert frames == [] and cluster.registry.get(run.run_id).status is RunStatus.COMPLETED


async def test_S15_conversation_only_tracked_unreadable(cluster, local):
    run = await cluster.run()
    await _unreadable(cluster)
    frames, _ = await ws_stop(local, {"conversation_id": run.conversation_id})
    _is_unavailable(frames)
    assert not cluster.registry.get(run.run_id).is_terminal


async def test_S16_conversation_only_untracked_local(local):
    frames, untracked = await ws_stop(local, {"conversation_id": CONV}, _Untracked())
    assert frames == [] and untracked


@gap("a stop naming conversation X cancels the connection's untracked turn even when that turn "
     "runs in conversation Y (the fallback never compares conversation ids)")
async def test_S17_conversation_only_untracked_other_conversation(local):
    untracked = _Untracked()
    untracked.slot["conversation_id"] = "conv-other"
    _frames, cancelled = await ws_stop(local, {"conversation_id": CONV}, untracked)
    assert not cancelled


async def test_S18_conversation_only_untracked_unreadable(cluster, local):
    await _unreadable(cluster)
    frames, untracked = await ws_stop(local, {"conversation_id": CONV}, _Untracked())
    # A live untracked turn here is stopped; the unreadable directory is not asked about it.
    assert frames == [] and untracked
    frames, untracked = await ws_stop(local, {"conversation_id": CONV}, _Untracked(live=False))
    _is_unavailable(frames)


@gap("a conversation-only stop looks only for a live run, so a finished parent's live children "
     "are not stopped (the registry's children_of_conversation is never consulted)")
async def test_S19_conversation_only_finished_parent_local(local):
    parent = local.start(conversation_id=CONV, user_email=OWNER)
    child = _local_run(local, "conv-child", parent_run_id=parent.run_id, depth=1)
    local.set_status(parent.run_id, RunStatus.COMPLETED)
    await ws_stop(local, {"conversation_id": CONV})
    assert local.get(child.run_id).status is RunStatus.CANCELLED


@gap("the directory's conversation key is released with the parent, so a conversation-only stop "
     "finds nothing to route and the children on the owner keep running")
async def test_S20_conversation_only_finished_parent_remote_terminal(cluster, local):
    parent, child = await _finished_parent_with_live_child_on_a(cluster)
    await ws_stop(local, {"conversation_id": parent.conversation_id})
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


async def test_S21_conversation_only_api_follow_up_local(local):
    first = local.start(conversation_id=CONV, user_email=OWNER)
    local.set_status(first.run_id, RunStatus.COMPLETED)
    follow_up = _local_run(local)
    await ws_stop(local, {"conversation_id": CONV})
    assert local.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_S22_conversation_only_api_follow_up_remote_live(cluster, local):
    first, follow_up = await _finished_with_follow_up_on_a(cluster)
    await ws_stop(local, {"conversation_id": first.conversation_id})
    assert cluster.registry.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_S23_both_tracked_local_run_id_wins(local):
    run = _local_run(local)
    other = _local_run(local, "conv-other")
    await ws_stop(local, {"run_id": run.run_id, "conversation_id": "conv-other"})
    assert local.get(run.run_id).status is RunStatus.CANCELLED
    assert not local.get(other.run_id).is_terminal


async def test_S24_both_tracked_remote_live(cluster, local):
    run = await cluster.run()
    frames, _ = await ws_stop(local, {"run_id": run.run_id, "conversation_id": run.conversation_id})
    assert [f["type"] for f in frames] == ["run_status"]
    assert cluster.registry.get(run.run_id).status is RunStatus.CANCELLED


async def test_S25_both_tracked_remote_terminal(cluster, local):
    run = await cluster.run()
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    frames, _ = await ws_stop(local, {"run_id": run.run_id, "conversation_id": run.conversation_id})
    assert [f["type"] for f in frames] == ["run_status"]


async def test_S26_both_tracked_unreadable(cluster, local):
    run = await cluster.run()
    await _unreadable(cluster)
    frames, _ = await ws_stop(local, {"run_id": run.run_id, "conversation_id": run.conversation_id})
    _is_unavailable(frames, run.run_id)


async def test_S27_both_stale_run_id_falls_back_to_conversation_local(local):
    run = _local_run(local)
    await ws_stop(local, {"run_id": "stale", "conversation_id": CONV})
    assert local.get(run.run_id).status is RunStatus.CANCELLED


async def test_S28_both_stale_run_id_falls_back_to_conversation_remote(cluster, local):
    run = await cluster.run()
    frames, _ = await ws_stop(local, {"run_id": "stale", "conversation_id": run.conversation_id})
    assert [f["type"] for f in frames] == ["run_status"]
    assert cluster.registry.get(run.run_id).status is RunStatus.CANCELLED


async def test_S29_both_untracked_stale_run_id(local):
    frames, untracked = await ws_stop(local, {"run_id": "stale", "conversation_id": CONV}, _Untracked())
    assert frames == [] and not untracked


async def test_S30_both_finished_parent_remote_terminal(cluster, local):
    parent, child = await _finished_parent_with_live_child_on_a(cluster)
    await ws_stop(local, {"run_id": parent.run_id, "conversation_id": parent.conversation_id})
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


@gap("run_id wins over conversation_id, so naming a finished run stops its cascade but never the "
     "live follow-up the same frame's conversation holds (REST cancel does stop it)")
async def test_S31_both_api_follow_up_local(local):
    from atlas.application.chat.runs.external_runs import API_SOURCE

    first = local.start(conversation_id=CONV, user_email=OWNER)
    first.launch_spec = {"source": API_SOURCE}
    local.set_status(first.run_id, RunStatus.COMPLETED)
    follow_up = _local_run(local)
    follow_up.launch_spec = {"source": API_SOURCE}
    await ws_stop(local, {"run_id": first.run_id, "conversation_id": CONV})
    assert local.get(follow_up.run_id).status is RunStatus.CANCELLED


@gap("as S31, on the owner: the stop reaches the finished run and the follow-up keeps running")
async def test_S32_both_api_follow_up_remote(cluster, local):
    first, follow_up = await _finished_with_follow_up_on_a(cluster)
    await ws_stop(local, {"run_id": first.run_id, "conversation_id": first.conversation_id})
    assert cluster.registry.get(follow_up.run_id).status is RunStatus.CANCELLED


async def test_S33_neither_stops_only_the_untracked_turn(local):
    run = _local_run(local)
    frames, untracked = await ws_stop(local, {}, _Untracked())
    assert untracked and frames == [] and not local.get(run.run_id).is_terminal
    # With no untracked turn running the frame does nothing at all.
    frames, untracked = await ws_stop(local, {}, _Untracked(live=False))
    assert frames == [] and not untracked and not local.get(run.run_id).is_terminal


# ---------------------------------------------------------------------------
# REST cancel (cancel_api_run_addressed; the local half is cancel_api_run,
# covered by test_agent_runs_api.py)
# ---------------------------------------------------------------------------


async def test_R01_api_run_remote_live(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    run = await api.live_on_a("conv-api")
    answer = await cancel_api_run_addressed(run.run_id)
    assert answer["ok"] and answer["payload"]["status"]["cancelled"] is True
    assert cluster.registry.get(run.run_id).status is RunStatus.CANCELLED


async def test_R02_api_run_remote_terminal(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    run = await api.finished_on_a()
    answer = await cancel_api_run_addressed(run.run_id)
    assert answer["ok"] and answer["payload"]["status"]["cancelled"] is False


async def test_R03_api_run_unreadable(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    run = await api.live_on_a("conv-api")
    await _unreadable(cluster)
    answer = await cancel_api_run_addressed(run.run_id)
    assert answer["ok"] is False and answer["code"] == "owner_unavailable"


async def test_R04_run_not_started_by_the_api_is_not_found(cluster, monkeypatch):
    _ApiReplicas(cluster, monkeypatch)
    run = await cluster.run()
    answer = await cancel_api_run_addressed(run.run_id)
    assert answer["ok"] is False and answer["code"] == "run_not_found"
    assert not cluster.registry.get(run.run_id).is_terminal


async def test_R05_unknown_id_is_nobodys(cluster, monkeypatch):
    _ApiReplicas(cluster, monkeypatch)
    with pytest.raises(LaunchRefused) as refused:
        await cancel_api_run_addressed("nobody")
    assert refused.value.code == "run_not_found"


async def test_R06_unknown_id_unreadable(cluster, monkeypatch):
    _ApiReplicas(cluster, monkeypatch)
    await _unreadable(cluster)
    answer = await cancel_api_run_addressed("nobody")
    assert answer["ok"] is False and answer["code"] == "owner_unavailable"


@gap("same retention mismatch as S09: past the 300 s directory record the cancel answers not found "
     "while the owner still retains the finished run and its children for 30 min")
async def test_R07_api_run_finished_record_expired(cluster, monkeypatch):
    api = _ApiReplicas(cluster, monkeypatch)
    run = await api.finished_on_a()
    child = cluster.registry.start(conversation_id="conv-child", user_email=OWNER,
                                   parent_run_id=run.run_id, depth=1)
    cluster.registry.attach_task(child.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.a.claim(child.run_id, child.conversation_id)
    await cluster.b.bus._client().delete(cluster.b.coordinator.keys.run(run.run_id))
    with contextlib.suppress(LaunchRefused):
        await cancel_api_run_addressed(run.run_id)
    assert cluster.registry.get(child.run_id).status is RunStatus.CANCELLED


# ---------------------------------------------------------------------------
# ws chat (steer)
# ---------------------------------------------------------------------------


def _active_channel():
    channel = SteeringChannel()
    channel.activate()
    return channel


async def _steer(conversation_id, **extra):
    """The remote-steer step of the ``chat`` branch: (handled, frames sent)."""
    ws = MagicMock()
    ws.send_json = AsyncMock()
    data = {"conversation_id": conversation_id, "content": "go left", **extra}
    handled = await steer_addressed_run(ws, RunRegistry(), OWNER, data)
    return handled, [c.args[0] for c in ws.send_json.await_args_list]


async def test_C01_remote_live_steer_is_queued_on_the_owner(cluster):
    channel = _active_channel()
    run = await cluster.run(steering=channel)
    handled, frames = await _steer(run.conversation_id, request_id="r1")
    assert handled and [f["update_type"] for f in frames] == ["steering_queued"]
    assert channel.queue.get_nowait() == "go left"


async def test_C02_remote_live_run_not_draining_refuses_the_steer(cluster):
    run = await cluster.run()
    handled, frames = await _steer(run.conversation_id)
    assert handled and frames[0]["error_type"] == "conversation_busy" and frames[0]["steering"] is True


async def test_C03_remote_terminal_run_is_not_steered(cluster):
    run = await cluster.run(steering=_active_channel())
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    # Not handled: the message starts a new turn.
    assert await _steer(run.conversation_id) == (False, [])


async def test_C04_unreadable_directory_refuses_instead_of_starting_a_turn(cluster):
    run = await cluster.run(steering=_active_channel())
    await _unreadable(cluster)
    handled, frames = await _steer(run.conversation_id)
    assert handled and [f["error_type"] for f in frames] == ["run_control_unavailable"]


async def test_C05_finished_parent_conversation_is_not_a_steer_target(cluster):
    parent, _child = await _finished_parent_with_live_child_on_a(cluster)
    # The parent's conversation key is released: the message starts a new turn.
    assert await _steer(parent.conversation_id) == (False, [])


async def test_C06_remote_api_follow_up_is_steered_like_any_tracked_run(cluster):
    channel = _active_channel()
    first = await cluster.run()
    cluster.registry.set_status(first.run_id, RunStatus.COMPLETED)
    await _release(cluster, first)
    follow_up = cluster.registry.start(conversation_id=first.conversation_id, user_email=OWNER,
                                       steering=channel)
    cluster.registry.attach_task(follow_up.run_id, asyncio.create_task(asyncio.sleep(30)))
    await cluster.a.claim(follow_up.run_id, first.conversation_id)
    handled, frames = await _steer(first.conversation_id)
    assert handled and frames[0]["run_id"] == follow_up.run_id
    assert frames[0]["update_type"] == "steering_queued"


async def test_C07_remote_off_record_steer_is_refused_before_it_is_written(cluster):
    run = await cluster.run(steering=_active_channel())
    handled, frames = await _steer(run.conversation_id, incognito=True)
    assert handled and frames[0]["error_type"] == "off_record"
    assert cluster.registry.get(run.run_id).steering.queue.empty()


def _local_steer_frames(chat_service, extra, steering):
    from fastapi.testclient import TestClient
    from main import app
    from test_websocket_run_directory_outage import CONV as WS_CONV
    from test_websocket_run_directory_outage import USER

    from atlas.application.chat.runs import get_run_registry
    from atlas.application.chat.runs.control_bus import set_run_control_bus

    set_run_control_bus(None)
    registry = get_run_registry()
    registry.start(conversation_id=WS_CONV, user_email=USER, steering=steering)
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": USER}) as ws:
        ws.send_json({"type": "chat", "content": "synthetic", "model": "m",
                      "conversation_id": WS_CONV, **extra})
        frame = ws.receive_json()
        while frame.get("type") not in ("error",) and frame.get("update_type") != "steering_queued":
            frame = ws.receive_json()
    return frame


from test_websocket_run_directory_outage import chat_service  # noqa: E402,F401


def test_C08_local_tracked_run_with_active_loop_is_steered(chat_service):
    frame = _local_steer_frames(chat_service, {}, _active_channel())
    assert frame["update_type"] == "steering_queued"


def test_C09_local_tracked_run_not_draining_is_refused(chat_service):
    frame = _local_steer_frames(chat_service, {}, None)
    assert frame["error_type"] == "conversation_busy"


@gap("a local steer queues incognito / not-saved content into a recorded run; the remote path "
     "refuses the same frame with off_record")
@pytest.mark.parametrize("off_record", [{"incognito": True}, {"save_mode": "local"}])
def test_C10_local_off_record_steer_is_refused(chat_service, off_record):
    frame = _local_steer_frames(chat_service, off_record, _active_channel())
    assert frame.get("error_type") == "off_record"


# ---------------------------------------------------------------------------
# ws tool_approval_response (route_approval is the owner nudge; the shared
# outcome write is decide_tool_approval, covered by test_approval_resolution)
# ---------------------------------------------------------------------------


async def test_P01_remote_live_approval_nudges_the_owner(cluster):
    run = await cluster.run()
    request = await _pending_approval(cluster, run)
    from atlas.application.chat.runs.execution_state import approval_storage_key

    await cluster.store.put_resolution(approval_storage_key(request.invocation_id, "call_0", OWNER),
                                       {"approved": True})
    answer = await run_control.route_approval(OWNER, run.run_id, request.invocation_id, "call_0")
    assert answer["ok"] and answer["payload"]["applied"] is True and request.future.done()


async def test_P02_approval_naming_no_run_sends_no_nudge(cluster):
    run = await cluster.run()
    request = await _pending_approval(cluster, run)
    # The executor still polls the shared outcome; only the wake-up is skipped.
    assert await run_control.route_approval(OWNER, None, request.invocation_id, "call_0") is None
    assert not request.future.done()


async def test_P03_remote_terminal_run_gets_no_nudge(cluster):
    run = await cluster.run()
    request = await _pending_approval(cluster, run)
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    assert await run_control.route_approval(OWNER, run.run_id, request.invocation_id, "call_0") is None


async def test_P04_unreadable_directory_is_best_effort(cluster):
    run = await cluster.run()
    request = await _pending_approval(cluster, run)
    await _unreadable(cluster)
    assert await run_control.route_approval(OWNER, run.run_id, request.invocation_id, "call_0") is None


def _two_pending_call_zero():
    from atlas.application.chat.approval_manager import ToolApprovalManager

    manager = ToolApprovalManager()
    for run_id in ("run-1", "run-2"):
        manager.create_approval_request("call_0", "server_tool", {}, user_email=OWNER,
                                        invocation_id=f"{run_id}:0:call_0", run_id=run_id)
    return manager


async def test_P07_bare_tool_call_id_shared_by_two_runs_is_ambiguous():
    # No hint: the response settles neither run's request (the client gets unknown_tool_approval).
    assert _two_pending_call_zero().find_open_request("call_0", None, None, OWNER) is None


async def test_P08_run_hint_selects_one_of_two_bare_tool_call_ids():
    request = _two_pending_call_zero().find_open_request("call_0", None, "run-2", OWNER)
    assert request is not None and request.run_id == "run-2"


# ---------------------------------------------------------------------------
# The doc and this file agree
# ---------------------------------------------------------------------------

_ROW = re.compile(r"^\|\s*`([A-Z]\d+)`", re.M)


def _doc_rows():
    text = DOC.read_text()
    rows = {}
    for line in text.splitlines():
        match = _ROW.match(line)
        if match:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows[match.group(1)] = cells
    return rows


def _tested_ids():
    ids = set()
    for name in globals():
        match = re.match(r"test_([A-Z]\d+)_", name)
        if match:
            ids.add(match.group(1))
    return ids


def test_every_tested_row_is_in_the_doc_and_every_doc_row_names_its_test():
    rows = _doc_rows()
    tested = _tested_ids()
    assert rows, "no table rows found"
    assert tested <= set(rows), f"tests without a doc row: {sorted(tested - set(rows))}"
    repo = Path(__file__).resolve().parents[2]
    corpus = "\n".join(p.read_text() for p in list((repo / "atlas" / "tests").glob("test_*.py"))
                       + list((repo / "test" / "multireplica").glob("test_*.py")))
    for row_id, cells in rows.items():
        test_cell = cells[-1]
        if row_id in tested:
            assert THIS_FILE in test_cell, f"{row_id}: test column must cite {THIS_FILE}"
            continue
        names = re.findall(r"`(test_\w+)`", test_cell)
        assert names or "n/a" in test_cell, f"{row_id}: names no test"
        for name in names:
            assert f"def {name}(" in corpus, f"{row_id}: {name} does not exist"


UNTESTED_GAPS = {"C12"}  # needs two real processes; see the doc row


def test_gap_rows_in_the_doc_are_exactly_the_strict_xfails():
    rows = _doc_rows()
    for row_id, cells in rows.items():
        assert len(cells) == 7, f"{row_id}: expected 7 columns, found {len(cells)}"
    # A GAP label anywhere in the row counts, not only in the code-path column.
    doc_gaps = {i for i, cells in rows.items() if any("GAP" in c for c in cells)}
    marked = set()
    for name, obj in globals().items():
        match = re.match(r"test_([A-Z]\d+)_", name)
        if match and any(m.name == "xfail" and m.kwargs.get("strict") is True
                         for m in getattr(obj, "pytestmark", [])):
            marked.add(match.group(1))
    assert doc_gaps - UNTESTED_GAPS == marked
    assert UNTESTED_GAPS <= doc_gaps
