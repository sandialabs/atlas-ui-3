"""A conversation whose live run another replica owns reads as running (issue #1219)."""

import pytest

from atlas.application.chat.runs import RunRegistry, execution_state, run_control
from atlas.application.chat.runs.control_bus import set_run_control_bus
from atlas.application.chat.runs.execution_state import merge_run_snapshots
from atlas.application.chat.runs.in_flight import hidden_remote_run_ids, in_flight_conversation, remote_live_runs
from atlas.application.chat.runs.run_directory import owner_entry
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

OWNER = "Owner@Example.com"


class _Bus:
    replica_id = "replica-a"

    def __init__(self, live=()):
        self.live = list(live)

    async def runs_for_user(self, user_email):
        return self.live


def _entry(replica="replica-b", user="owner@example.com", scope=""):
    return {
        "replica": replica,
        "run_id": "run-1",
        "conversation_id": "conv-1",
        "user_email": user,
        "status": "running",
        "scope": scope,
    }


@pytest.fixture
def shared(monkeypatch):
    """Install a bus and a controllable shared run lookup."""
    state = {"entry": _entry()}
    bus = _Bus()
    state["bus"] = bus
    set_run_control_bus(bus)

    async def fake_entry(conversation_id):
        return state["entry"]

    monkeypatch.setattr(run_control, "conversation_entry", fake_entry)
    yield state
    set_run_control_bus(None)
    execution_state.reset_execution_store()


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
async def test_text_only_run_needs_no_projection(shared):
    # No projection store is installed or written: the shared claim decides.
    assert (await _view())["run_elsewhere"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["off_record", "connection_bound"])
async def test_scoped_runs_are_not_served(shared, scope):
    shared["entry"] = _entry(scope=scope)
    assert await _view() is None


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


@pytest.mark.asyncio
async def test_remote_runs_list_skips_own_and_scoped(shared):
    shared["bus"].live = [
        _entry(),
        _entry(replica="replica-a"),
        {**_entry(), "run_id": "run-2", "conversation_id": "conv-2", "scope": "off_record"},
    ]
    runs = await remote_live_runs(OWNER)
    assert [(r["run_id"], r["detached"]) for r in runs] == [("run-1", True)]


@pytest.mark.asyncio
async def test_hidden_remote_runs_are_found_by_scope(shared):
    async def scopes_of(run_ids):
        return {"run-1": "connection_bound", "run-2": "", "run-3": "off_record"}

    shared["bus"].scopes_of = scopes_of
    hidden = await hidden_remote_run_ids(["run-1", "run-2", "run-3"])
    assert hidden == {"run-1", "run-3"}


@pytest.mark.asyncio
async def test_unreadable_scopes_hide_remote_runs(shared):
    async def scopes_of(run_ids):
        raise RuntimeError("redis down")

    shared["bus"].scopes_of = scopes_of
    assert await hidden_remote_run_ids(["run-1"]) == {"run-1"}


@pytest.mark.asyncio
async def test_hidden_remote_run_adds_no_rows_to_a_conversation(shared):
    async def scopes_of(run_ids):
        return {"run-1": "connection_bound"}

    class _Store:
        async def get(self, conversation_id, user_email):
            return {"run_id": "run-1", "conversation_id": conversation_id, "status": "running",
                    "invocations": {"i": {"invocation_id": "i", "status": "calling", "tool_name": "t"}}}

    shared["bus"].scopes_of = scopes_of
    execution_state.set_execution_store(_Store())
    conversation = {"id": "conv-1", "messages": []}
    assert await execution_state.attach_execution(conversation, OWNER) == conversation


def test_owner_entry_carries_scope():
    record = {"owner": "r", "generation": "1", "run_id": "x", "scope": "off_record", "status": "running"}
    assert owner_entry(record)["scope"] == "off_record"
    assert owner_entry({**record, "scope": ""})["scope"] == ""


def _projection(status):
    return {"run_id": "run-1", "conversation_id": "conv-1", "status": status,
            "end_reason": "owner_lost", "revision": 3}


def test_snapshot_keeps_ended_run_when_watched():
    runs = merge_run_snapshots([], [_projection("failed")], ended_for="conv-1")
    assert [(r["run_id"], r["status"], r["end_reason"]) for r in runs] == [("run-1", "failed", "owner_lost")]


def test_ended_run_survives_an_older_local_run():
    older = {"run_id": "run-0", "conversation_id": "conv-1", "status": "completed", "created_at": 1}
    runs = merge_run_snapshots([older], [_projection("failed")], ended_for="conv-1")
    assert sorted(r["run_id"] for r in runs) == ["run-0", "run-1"]


def test_ended_run_yields_to_a_live_local_run():
    live = {"run_id": "run-2", "conversation_id": "conv-1", "status": "running", "created_at": 5}
    runs = merge_run_snapshots([live], [_projection("failed")], ended_for="conv-1")
    assert [r["run_id"] for r in runs] == ["run-2"]


def test_snapshot_drops_ended_run_unwatched():
    assert merge_run_snapshots([], [_projection("failed")]) == []
    assert merge_run_snapshots([], [_projection("failed")], ended_for="other") == []


def test_snapshot_lists_remote_running_run():
    runs = merge_run_snapshots([], [_projection("running")], ended_for="conv-1")
    assert runs[0]["status"] == "running" and runs[0]["detached"] is True


@pytest.mark.asyncio
async def test_strict_snapshot_read_raises():
    class _Broken:
        async def list_for_user(self, user_email):
            raise RuntimeError("redis down")

    execution_state.set_execution_store(_Broken())
    try:
        assert await execution_state.list_execution_snapshots("u@example.com") == []
        with pytest.raises(RuntimeError):
            await execution_state.list_execution_snapshots("u@example.com", strict=True)
    finally:
        execution_state.reset_execution_store()
