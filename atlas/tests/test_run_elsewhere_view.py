"""A conversation whose live run another replica owns reads as running (issue #1219)."""

import pytest

from atlas.application.chat.runs import RunRegistry, run_control
from atlas.application.chat.runs.control_bus import set_run_control_bus
from atlas.application.chat.runs.execution_state import merge_run_snapshots
from atlas.application.chat.runs.in_flight import in_flight_conversation
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

OWNER = "Owner@Example.com"


class _Bus:
    replica_id = "replica-a"


def _entry(replica="replica-b", user="owner@example.com"):
    return {
        "replica": replica,
        "run_id": "run-1",
        "conversation_id": "conv-1",
        "user_email": user,
        "status": "running",
    }


@pytest.fixture
def shared(monkeypatch):
    """Install a bus and a controllable shared run lookup."""
    state = {"entry": _entry()}
    set_run_control_bus(_Bus())

    async def fake_entry(conversation_id):
        return state["entry"]

    monkeypatch.setattr(run_control, "conversation_entry", fake_entry)
    yield state
    set_run_control_bus(None)


async def _view():
    return await in_flight_conversation(InMemorySessionRepository(), RunRegistry(), "conv-1", OWNER)


@pytest.mark.asyncio
async def test_remote_run_reads_as_running(shared):
    view = await _view()
    assert view["run_elsewhere"] is True
    assert view["run_id"] == "run-1"
    assert view["run_status"] == "running"
    assert view["messages"] == []
    assert "in_flight" not in view


@pytest.mark.asyncio
async def test_remote_run_is_user_scoped(shared):
    shared["entry"] = _entry(user="someone-else@example.com")
    assert await _view() is None


@pytest.mark.asyncio
async def test_own_replica_run_is_not_remote(shared):
    shared["entry"] = _entry(replica="replica-a")
    assert await _view() is None


@pytest.mark.asyncio
async def test_no_shared_run_is_a_miss(shared):
    shared["entry"] = None
    assert await _view() is None


def _projection(status):
    return {"run_id": "run-1", "conversation_id": "conv-1", "status": status,
            "end_reason": "owner_lost", "revision": 3}


def test_snapshot_keeps_ended_run_when_watched():
    runs = merge_run_snapshots([], [_projection("failed")], ended_for="conv-1")
    assert [(r["run_id"], r["status"], r["end_reason"]) for r in runs] == [("run-1", "failed", "owner_lost")]


def test_snapshot_drops_ended_run_unwatched():
    assert merge_run_snapshots([], [_projection("failed")]) == []
    assert merge_run_snapshots([], [_projection("failed")], ended_for="other") == []


def test_snapshot_lists_remote_running_run():
    runs = merge_run_snapshots([], [_projection("running")], ended_for="conv-1")
    assert runs[0]["status"] == "running" and runs[0]["detached"] is True
