"""A conversation whose live run another replica owns reads as running (issue #1219)."""

import pytest

from atlas.application.chat.runs import RunRegistry, execution_state, run_control
from atlas.application.chat.runs.control_bus import set_run_control_bus
from atlas.application.chat.runs.execution_state import merge_run_snapshots
from atlas.application.chat.runs.in_flight import (
    compose_run_list,
    in_flight_conversation,
    remote_live_runs,
    shareable_remote_run_ids,
)
from atlas.application.chat.runs.run_directory import owner_entry
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

OWNER = "Owner@Example.com"


class _Bus:
    replica_id = "replica-a"

    def __init__(self, live=()):
        self.live = list(live)
        self.scopes = {}
        self.scopes_error = None
        self.live_error = None

    async def runs_for_user(self, user_email):
        if self.live_error:
            raise self.live_error
        return self.live

    async def scopes_of(self, run_ids):
        if self.scopes_error:
            raise self.scopes_error
        return {r: self.scopes.get(r) for r in run_ids}


def _entry(replica="replica-b", user="owner@example.com", scope="shared"):
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
@pytest.mark.parametrize("scope", ["off_record", "connection_bound", "other"])
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


# The rule, one test per row. A remote run is shown to another replica only
# when its shared record was read AND says "shared"; everything else is hidden,
# and a failed read also marks the list degraded.
#
#   record        scope field          read     -> shown   degraded
#   present       shared               ok          yes     no
#   present       off_record           ok          no      no
#   present       connection_bound     ok          no      no
#   present       (field missing)      ok          no      no
#   present       unknown value        ok          no      no
#   absent        -                    ok          no      no
#   -             -                    fails       no      yes
#   hidden projection beside a live claim of the conversation: the claim shows
#   stale shared projection beside a newer live claim: the claim shows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope, shown",
    [("shared", True), ("off_record", False), ("connection_bound", False), (None, False), ("other", False)],
)
async def test_scope_rule_for_a_read_record(shared, scope, shown):
    shared["bus"].scopes = {"run-1": scope}
    assert (await shareable_remote_run_ids(["run-1"]) == {"run-1"}) is shown


@pytest.mark.asyncio
async def test_scope_rule_for_an_absent_record(shared):
    # The bus answers with no entry for the id: the record is gone.
    assert await shareable_remote_run_ids(["run-1"]) == set()


@pytest.mark.asyncio
async def test_scope_rule_fails_closed_on_a_failed_read(shared):
    from atlas.application.chat.runs.in_flight import ScopeLookupUnavailable

    shared["bus"].scopes_error = RuntimeError("redis down")
    with pytest.raises(ScopeLookupUnavailable):
        await shareable_remote_run_ids(["run-1"])


def _store(projections):
    class _Store:
        async def list_for_user(self, user_email):
            return projections

        async def get(self, conversation_id, user_email):
            return None

    execution_state.set_execution_store(_Store())


def _proj(run_id="run-1", conversation_id="conv-1", status="running"):
    return {"run_id": run_id, "conversation_id": conversation_id, "status": status, "revision": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["off_record", "connection_bound", None, "other"])
async def test_list_hides_a_projection_without_the_shared_scope(shared, scope):
    _store([_proj()])
    shared["bus"].scopes = {"run-1": scope}
    runs, degraded = await compose_run_list(OWNER, [])
    assert runs == [] and degraded is False


@pytest.mark.asyncio
async def test_list_shows_a_shared_projection(shared):
    _store([_proj()])
    shared["bus"].scopes = {"run-1": "shared"}
    runs, degraded = await compose_run_list(OWNER, [])
    assert [r["run_id"] for r in runs] == ["run-1"] and degraded is False


@pytest.mark.asyncio
async def test_list_hides_a_projection_whose_record_is_gone(shared):
    _store([_proj()])
    runs, degraded = await compose_run_list(OWNER, [])
    assert runs == [] and degraded is False


@pytest.mark.asyncio
async def test_list_is_degraded_and_local_only_when_scopes_cannot_be_read(shared):
    _store([_proj()])
    shared["bus"].scopes_error = RuntimeError("redis down")
    local = [{"run_id": "run-local", "conversation_id": "conv-9", "status": "running"}]
    runs, degraded = await compose_run_list(OWNER, local)
    assert runs == local and degraded is True


@pytest.mark.asyncio
async def test_list_is_degraded_when_the_claims_cannot_be_read(shared):
    _store([_proj()])
    shared["bus"].scopes = {"run-1": "shared"}
    shared["bus"].live_error = RuntimeError("redis down")
    runs, degraded = await compose_run_list(OWNER, [])
    assert runs == [] and degraded is True


@pytest.mark.asyncio
async def test_list_is_degraded_when_projections_cannot_be_read(shared):
    class _Broken:
        async def list_for_user(self, user_email):
            raise RuntimeError("redis down")

    execution_state.set_execution_store(_Broken())
    runs, degraded = await compose_run_list(OWNER, [])
    assert runs == [] and degraded is True


@pytest.mark.asyncio
async def test_hidden_projection_does_not_suppress_a_live_claim(shared):
    # An old scoped projection of the conversation, and a live shared claim.
    _store([_proj(run_id="run-old")])
    shared["bus"].scopes = {"run-old": "connection_bound"}
    shared["bus"].live = [_entry()]
    runs, degraded = await compose_run_list(OWNER, [])
    assert [r["run_id"] for r in runs] == ["run-1"] and degraded is False


@pytest.mark.asyncio
async def test_stale_shared_projection_yields_to_a_newer_claim(shared):
    _store([_proj(run_id="run-old")])
    shared["bus"].scopes = {"run-old": "shared"}
    shared["bus"].live = [_entry()]
    runs, degraded = await compose_run_list(OWNER, [])
    assert [r["run_id"] for r in runs] == ["run-1"] and degraded is False


@pytest.mark.asyncio
async def test_a_scoped_projection_adds_no_rows_to_a_conversation(shared):
    shared["bus"].scopes = {"run-1": "connection_bound"}

    class _Store:
        async def get(self, conversation_id, user_email):
            return {"run_id": "run-1", "conversation_id": conversation_id, "status": "running",
                    "invocations": {"i": {"invocation_id": "i", "status": "calling", "tool_name": "t"}}}

    execution_state.set_execution_store(_Store())
    conversation = {"id": "conv-1", "messages": []}
    assert await execution_state.attach_execution(conversation, OWNER) == conversation


@pytest.mark.asyncio
async def test_a_failed_scope_read_adds_no_rows_to_a_conversation(shared):
    shared["bus"].scopes_error = RuntimeError("redis down")

    class _Store:
        async def get(self, conversation_id, user_email):
            return {"run_id": "run-1", "conversation_id": conversation_id, "status": "running",
                    "invocations": {"i": {"invocation_id": "i", "status": "calling", "tool_name": "t"}}}

    execution_state.set_execution_store(_Store())
    conversation = {"id": "conv-1", "messages": []}
    assert await execution_state.attach_execution(conversation, OWNER) == conversation


@pytest.mark.asyncio
async def test_a_claim_without_a_scope_is_not_served(shared):
    # A record written before the field existed.
    shared["entry"] = _entry(scope=None)
    assert await _view() is None


def test_owner_entry_carries_scope():
    record = {"owner": "r", "generation": "1", "run_id": "x", "scope": "off_record", "status": "running"}
    assert owner_entry(record)["scope"] == "off_record"
    assert owner_entry({k: v for k, v in record.items() if k != "scope"})["scope"] is None


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
