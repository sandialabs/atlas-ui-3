"""Subagent run and result tools served from shared state (issue #1222).

Replica A launches a child; the parent's next turn runs on replica B, whose
local run registry is empty. Both replicas share one fakeredis server (the run
records) and one conversation store.
"""

import json
from types import SimpleNamespace

import fakeredis
import pytest

from atlas.application.chat.runs import control_bus
from atlas.application.chat.runs.admission import admit_run, stop_lease_heartbeat
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.control_bus import RedisRunControlBus
from atlas.application.chat.runs.coordination import set_run_coordinator
from atlas.application.chat.runs.execution_state import (
    _attach_run_lineage,
    merge_run_snapshots,
)
from atlas.application.chat.runs.launcher import (
    LaunchRefused,
    get_child_result,
    list_child_runs,
)
from atlas.application.chat.runs.registry import RunStatus, get_run_registry, reset_run_registry
from atlas.application.chat.runs.run_directory import lineage_from_record, owner_entry
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator

USER = "owner@example.com"
REFUSAL = "That run is not a sub-conversation of this conversation."


class Replica:
    def __init__(self, server, replica_id):
        manager = RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=server),
        )
        self.coordinator = RedisRunCoordinator(manager, replica_id=replica_id)
        self.bus = RedisRunControlBus(manager, self.coordinator)

    async def launch_child(self, run_id="child-run", conversation_id="child-conv",
                           parent="parent-conv", user=USER, title="do the thing"):
        return await self.coordinator.claim(
            run_id=run_id, conversation_id=conversation_id, user_email=user,
            max_runs_per_user=5, source="atlas_launch", parent_run_id="parent-run",
            parent_conversation_id=parent, title=title, depth=1,
        )

    async def finish(self, run_id, status="completed", result="the answer"):
        key = self.coordinator.keys.run(run_id)
        await self.bus._redis.client.hset(key, mapping={
            "status": status,
            "snapshot": json.dumps({"status": status, "result": result, "waiting_on": None}),
        })


class FakeStore:
    """The shared conversation store, with the repository's user scoping."""

    def __init__(self):
        self.rows = {}

    def add(self, conversation_id, user, parent, launch_run_id=None, reply="stored answer", follow_ups=0,
            status="completed", depth=None):
        metadata = {"parent_conversation_id": parent}
        if status:
            metadata["launch_status"] = status
        if depth:
            metadata["launch_depth"] = depth
        if launch_run_id:
            metadata["launch_run_id"] = launch_run_id
        self.rows[conversation_id] = {
            "id": conversation_id, "user": user, "title": "saved child", "metadata": metadata,
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:01:00",
            "messages": [{"role": "user", "content": "go"}]
            + ([{"role": "assistant", "content": reply}] if reply is not None else [])
            + [{"role": "user", "content": "more"}, {"role": "assistant", "content": "later answer"}] * follow_ups,
        }

    def list_child_conversations(self, parent_conversation_id, user_email, limit=200, child_ref=None):
        return [
            {k: v for k, v in row.items() if k not in ("user", "messages")}
            for row in self.rows.values()
            if row["user"] == user_email
            and row["metadata"].get("parent_conversation_id") == parent_conversation_id
            and (not child_ref or child_ref in (row["id"], row["metadata"].get("launch_run_id")))
        ]

    def get_conversation(self, conversation_id, user_email):
        row = self.rows.get(conversation_id)
        return row if row and row["user"] == user_email else None


@pytest.fixture
def cluster():
    server = fakeredis.FakeServer()
    a, b = Replica(server, "replica-a"), Replica(server, "replica-b")
    reset_run_registry()
    clear_current_run()
    control_bus.set_run_control_bus(b.bus)
    yield a, b
    control_bus.set_run_control_bus(None)
    set_run_coordinator(None)
    clear_current_run()
    reset_run_registry()


@pytest.fixture
def store():
    return FakeStore()


def _on_replica_b(store, conversation="parent-conv"):
    """The parent's next turn: a run of the parent conversation on replica B."""
    set_current_run("parent-turn-2", conversation)
    return {"user_email": USER}, SimpleNamespace(conversation_repository=store)


async def test_record_carries_lineage(cluster):
    a, b = cluster
    await a.launch_child()

    raw = await b.coordinator.read("child-run")

    assert raw["parent_conversation_id"] == "parent-conv"
    assert raw["title"] == "do the thing"
    assert raw["depth"] == "1"
    assert lineage_from_record(raw)["parent_run_id"] == "parent-run"
    assert owner_entry(raw)["parent_conversation_id"] == "parent-conv"


def test_old_record_lineage_from_snapshot():
    raw = {"snapshot": json.dumps({"parent_conversation_id": "p", "title": "t", "depth": 2})}

    assert lineage_from_record(raw)["parent_conversation_id"] == "p"
    assert lineage_from_record(raw)["depth"] == 2


async def test_live_child_listed_from_b(cluster, store):
    a, _ = cluster
    await a.launch_child()
    context, factory = _on_replica_b(store)

    runs = await list_child_runs(context, factory)

    assert [r["run_id"] for r in runs] == ["child-run"]
    assert runs[0]["parent_conversation_id"] == "parent-conv"
    assert runs[0]["parent_run_id"] == "parent-run"
    assert runs[0]["title"] == "do the thing"


async def test_stored_child_listed_after_reap(cluster, store):
    store.add("child-conv", USER, "parent-conv", launch_run_id="gone-run")
    context, factory = _on_replica_b(store)

    runs = await list_child_runs(context, factory)

    assert [(r["run_id"], r["conversation_id"]) for r in runs] == [("gone-run", "child-conv")]
    assert runs[0]["status"] == "completed"


async def test_listing_has_no_duplicates(cluster, store):
    a, _ = cluster
    await a.launch_child()
    await a.finish("child-run")
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    context, factory = _on_replica_b(store)

    runs = await list_child_runs(context, factory)

    assert [r["run_id"] for r in runs] == ["child-run"]
    assert runs[0]["status"] == "completed"


async def test_listing_hides_foreign_children(cluster, store):
    a, _ = cluster
    await a.launch_child(run_id="other-user", conversation_id="c1", user="eve@example.com")
    await a.launch_child(run_id="other-conv", conversation_id="c2", parent="elsewhere")
    store.add("c3", "eve@example.com", "parent-conv")
    store.add("c4", USER, "elsewhere")
    context, factory = _on_replica_b(store)

    assert await list_child_runs(context, factory) == []


async def test_running_child_result_pending(cluster, store):
    a, _ = cluster
    await a.launch_child()
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["status"] == "queued"
    assert result["result_status"] == "pending"
    assert result["result"] is None


async def test_finished_child_result_shared(cluster, store):
    a, _ = cluster
    await a.launch_child()
    await a.finish("child-run")
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["status"] == "completed"
    assert result["result"] == "the answer"
    assert result["result_status"] == "available"


async def test_result_from_stored_conversation(cluster, store):
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["conversation_id"] == "child-conv"
    assert result["result"] == "stored answer"
    assert result["result_status"] == "available"


async def test_result_by_conversation_id(cluster, store):
    store.add("child-conv", USER, "parent-conv")
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-conv", context, factory)

    assert result["result"] == "stored answer"


async def test_stored_child_without_reply(cluster, store):
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run", reply=None)
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["result_status"] == "empty"


async def test_result_works_without_redis(store):
    reset_run_registry()
    control_bus.set_run_control_bus(None)
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["result"] == "stored answer"
    clear_current_run()
    reset_run_registry()


async def test_refusals_are_identical(cluster, store):
    a, _ = cluster
    await a.launch_child(run_id="foreign-user", conversation_id="c1", user="eve@example.com")
    await a.launch_child(run_id="foreign-conv", conversation_id="c2", parent="elsewhere")
    store.add("c3", "eve@example.com", "parent-conv", launch_run_id="stored-foreign-user")
    store.add("c4", USER, "elsewhere", launch_run_id="stored-foreign-conv")
    context, factory = _on_replica_b(store)

    messages = []
    for run_id in (
        "foreign-user", "foreign-conv", "stored-foreign-user", "stored-foreign-conv",
        "c1", "c3", "c4", "no-such-run",
    ):
        with pytest.raises(LaunchRefused) as raised:
            await get_child_result(run_id, context, factory)
        messages.append(str(raised.value))

    assert set(messages) == {REFUSAL}


async def test_other_user_is_refused(cluster, store):
    a, _ = cluster
    await a.launch_child()
    await a.finish("child-run")
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    set_current_run("parent-turn-2", "parent-conv")

    with pytest.raises(LaunchRefused) as raised:
        await get_child_result(
            "child-run", {"user_email": "eve@example.com"},
            SimpleNamespace(conversation_repository=store),
        )

    assert str(raised.value) == REFUSAL


async def test_store_outage_is_not_not_found(cluster):
    class Broken:
        def list_child_conversations(self, *args, **kwargs):
            raise RuntimeError("db down")

    set_current_run("parent-turn-2", "parent-conv")

    with pytest.raises(LaunchRefused) as raised:
        await get_child_result(
            "child-run", {"user_email": USER}, SimpleNamespace(conversation_repository=Broken())
        )

    assert raised.value.code == "unavailable"


async def test_local_child_still_answers_first(cluster, store):
    registry = get_run_registry()
    parent = registry.start(conversation_id="parent-conv", user_email=USER)
    child = registry.start(
        conversation_id="local-child", user_email=USER, parent_run_id=parent.run_id,
        parent_conversation_id="parent-conv", depth=1,
    )
    registry.set_status(child.run_id, RunStatus.RUNNING)
    set_current_run(parent.run_id, "parent-conv")

    runs = await list_child_runs({"user_email": USER}, SimpleNamespace(conversation_repository=store))

    assert [r["run_id"] for r in runs] == [child.run_id]


async def test_snapshot_keeps_lineage(cluster):
    a, b = cluster
    await a.launch_child()
    projection = {
        "run_id": "child-run", "conversation_id": "child-conv", "status": "running",
        "activity": "executing", "revision": 3,
    }

    enriched = await _attach_run_lineage([projection], USER)
    merged = merge_run_snapshots([], enriched)

    assert merged[0]["parent_conversation_id"] == "parent-conv"
    assert merged[0]["parent_run_id"] == "parent-run"
    assert merged[0]["title"] == "do the thing"
    assert merged[0]["depth"] == 1
    assert merged[0]["detached"] is True


async def test_snapshot_lineage_user_checked(cluster):
    a, _ = cluster
    await a.launch_child(user="eve@example.com")
    projection = {"run_id": "child-run", "conversation_id": "child-conv", "status": "running"}

    enriched = await _attach_run_lineage([projection], USER)

    assert "parent_conversation_id" not in enriched[0]


async def test_admission_writes_lineage(cluster):
    a, b = cluster
    set_run_coordinator(a.coordinator)

    try:
        record = await admit_run(
            conversation_id="child-conv", user_email=USER, source="atlas_launch",
            parent_run_id="parent-run", parent_conversation_id="parent-conv",
            depth=1, title="do the thing",
        )
    finally:
        await stop_lease_heartbeat()

    raw = await b.coordinator.read(record.run_id)
    assert raw["parent_conversation_id"] == "parent-conv"
    assert raw["parent_run_id"] == "parent-run"
    assert raw["title"] == "do the thing"
    assert raw["depth"] == "1"


# ---------------------------------------------------------------------------
# The repository query, against a real store
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
    from atlas.modules.chat_history.database import reset_engine

    reset_engine()
    init_database(f"duckdb:///{tmp_path / 'children.db'}")
    yield ConversationRepository(get_session_factory())
    reset_engine()


def _save(repo, conversation_id, user, metadata=None):
    repo.save_conversation(
        conversation_id, user, "title", "model",
        [{"role": "user", "content": "go", "message_type": "chat"},
         {"role": "assistant", "content": "done", "message_type": "chat"}],
        metadata=metadata,
    )


def test_repo_lists_only_own_children(repo):
    _save(repo, "parent", USER)
    _save(repo, "kid-1", USER, {"parent_conversation_id": "parent", "launch_run_id": "run-1"})
    _save(repo, "kid-2", USER, {"parent_conversation_id": "other-parent"})
    _save(repo, "kid-3", "eve@example.com", {"parent_conversation_id": "parent"})
    _save(repo, "mentions", USER, {"note": "parent"})

    children = repo.list_child_conversations("parent", USER)

    assert [c["id"] for c in children] == ["kid-1"]
    assert children[0]["metadata"]["launch_run_id"] == "run-1"
    assert [c["id"] for c in repo.list_child_conversations("parent", "eve@example.com")] == ["kid-3"]


async def test_off_record_run_shares_no_title(cluster):
    a, b = cluster
    set_run_coordinator(a.coordinator)

    try:
        record = await admit_run(
            conversation_id="incognito-conv", user_email=USER, title="private prompt", off_record=True,
        )
    finally:
        await stop_lease_heartbeat()

    raw = await b.coordinator.read(record.run_id)
    assert raw["title"] == ""
    assert "private prompt" not in json.dumps(raw)


async def test_followed_up_child_by_run_id(cluster, store):
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run", follow_ups=1)
    context, factory = _on_replica_b(store)

    by_run = await get_child_result("child-run", context, factory)
    by_conversation = await get_child_result("child-conv", context, factory)

    assert by_run["result"] is None and by_run["result_status"] == "unavailable"
    assert "separately" in by_run["result_error"]
    assert by_conversation["result"] == "later answer"


async def test_live_follow_up_not_substituted(cluster, store):
    a, _ = cluster
    await a.launch_child(run_id="follow-run", conversation_id="child-conv")
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run", follow_ups=1)
    context, factory = _on_replica_b(store)

    result = await get_child_result("child-run", context, factory)

    assert result["run_id"] == "child-run"
    assert result["result"] is None


def test_child_found_beyond_the_listing_limit(repo):
    _save(repo, "parent", USER)
    for i in range(205):
        _save(repo, f"kid-{i}", USER, {"parent_conversation_id": "parent", "launch_run_id": f"run-{i}"})

    assert len(repo.list_child_conversations("parent", USER)) == 200
    found = repo.list_child_conversations("parent", USER, child_ref="run-204")
    assert [c["id"] for c in found] == ["kid-204"]
    assert [c["id"] for c in repo.list_child_conversations("parent", USER, child_ref="kid-203")] == ["kid-203"]
    assert repo.list_child_conversations("parent", USER, child_ref="nope") == []


def test_later_save_keeps_launch_run_id(repo):
    _save(repo, "kid", USER, {"parent_conversation_id": "parent", "launch_run_id": "run-1"})

    _save(repo, "kid", USER, {"parent_conversation_id": "parent"})

    assert repo.get_conversation("kid", USER)["metadata"]["launch_run_id"] == "run-1"


async def test_stored_child_reports_recorded_outcome(cluster, store):
    store.add("c-failed", USER, "parent-conv", launch_run_id="r-failed", status="failed", depth=2)
    store.add("c-old", USER, "parent-conv", launch_run_id="r-old", status=None)
    context, factory = _on_replica_b(store)

    runs = {r["run_id"]: r for r in await list_child_runs(context, factory)}

    assert runs["r-failed"]["status"] == "failed" and runs["r-failed"]["depth"] == 2
    # No recorded outcome: never shown as a success, and its depth defaults to a direct child.
    assert runs["r-old"]["status"] == "failed"
    assert "not recorded" in runs["r-old"]["error"]
    assert runs["r-old"]["depth"] == 1
    assert (await get_child_result("r-failed", context, factory))["result"] is None
    assert (await get_child_result("r-failed", context, factory))["status"] == "failed"


async def test_unrecorded_outcome_keeps_reply(cluster, store):
    store.add("c-old", USER, "parent-conv", launch_run_id="r-old", status=None)
    context, factory = _on_replica_b(store)

    result = await get_child_result("r-old", context, factory)

    assert result["status"] == "failed" and result["error"]
    assert result["result"] == "stored answer"


async def test_unreadable_record_is_not_absent(cluster, store):
    _, b = cluster
    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    context, factory = _on_replica_b(store)

    async def down(run_id):
        raise ConnectionError("redis down")

    b.bus.directory.record = down

    with pytest.raises(LaunchRefused) as raised:
        await get_child_result("child-run", context, factory)
    assert raised.value.code == "unavailable"
    assert str(raised.value) != REFUSAL
    with pytest.raises(LaunchRefused) as listing:
        await list_child_runs(context, factory)
    assert listing.value.code == "unavailable"


async def test_unreadable_live_lookup_fails_listing(cluster, store):
    _, b = cluster
    context, factory = _on_replica_b(store)

    async def down(user_email):
        raise ConnectionError("redis down")

    b.bus.runs_for_user = down

    with pytest.raises(LaunchRefused) as raised:
        await list_child_runs(context, factory)
    assert raised.value.code == "unavailable"


async def test_read_failure_is_counted(cluster, store):
    from atlas.core.coordination_metrics import coordination_metrics

    _, b = cluster
    context, factory = _on_replica_b(store)

    async def down(user_email):
        raise ConnectionError("redis down")

    b.bus.runs_for_user = down
    before = coordination_metrics().counter("atlas_shared_child_read_failures_total")
    with pytest.raises(LaunchRefused):
        await list_child_runs(context, factory)

    assert coordination_metrics().counter("atlas_shared_child_read_failures_total") == before + 1


async def test_observation_tools_use_injected_factory(cluster, store):
    from atlas.application.chat.runs.launcher import execute_observation_tool

    store.add("child-conv", USER, "parent-conv", launch_run_id="child-run")
    context, factory = _on_replica_b(store)
    context = {**context, "factory": factory}

    runs = await execute_observation_tool(SimpleNamespace(id="1", arguments={}), context, "atlas_get_subagent_runs")
    result = await execute_observation_tool(
        SimpleNamespace(id="2", arguments={"run_id": "child-run"}), context, "atlas_get_subagent_result"
    )

    assert runs.success and "child-conv" in runs.content
    assert result.success and "stored answer" in result.content


async def test_run_outcome_recorded_on_child(repo):
    from atlas.application.chat.runs.launcher import _record_outcome

    _save(repo, "kid", USER, {"parent_conversation_id": "parent", "launch_run_id": "run-1"})
    chat_service = SimpleNamespace(conversation_repository=repo)
    record = SimpleNamespace(conversation_id="kid", user_email=USER, depth=2, run_id="run-1")

    await _record_outcome(chat_service, record, RunStatus.CANCELLED)

    metadata = repo.get_conversation("kid", USER)["metadata"]
    assert metadata["launch_status"] == "cancelled" and metadata["launch_depth"] == 2
    assert metadata["launch_run_id"] == "run-1"
    # A later save from a session that never loaded them keeps all three.
    _save(repo, "kid", USER, {"parent_conversation_id": "parent"})
    metadata = repo.get_conversation("kid", USER)["metadata"]
    assert (metadata["launch_status"], metadata["launch_depth"], metadata["launch_run_id"]) == ("cancelled", 2, "run-1")


def test_outcome_not_recorded_for_other_user(repo):
    _save(repo, "kid", USER, {"parent_conversation_id": "parent"})

    assert repo.record_launch_outcome("kid", "eve@example.com", "completed", 1) is False
    assert "launch_status" not in repo.get_conversation("kid", USER)["metadata"]


def test_mentions_do_not_fill_the_limit(repo):
    _save(repo, "parent", USER)
    for i in range(210):
        _save(repo, f"note-{i:03}", USER, {"note": "parent", "parent_conversation_id": "parentX"})
    for i in range(3):
        _save(repo, f"real-{i}", USER, {"parent_conversation_id": "parent"})

    children = repo.list_child_conversations("parent", USER, limit=5)

    assert [c["id"] for c in children] == ["real-0", "real-1", "real-2"]


def test_listing_pages_past_two_hundred(repo):
    _save(repo, "parent", USER)
    for i in range(230):
        _save(repo, f"kid-{i:03}", USER, {"parent_conversation_id": "parent"})

    assert len(repo.list_child_conversations("parent", USER, limit=500)) == 230
    assert len(repo.list_child_conversations("parent", USER, limit=10)) == 10


async def test_follow_up_outcome_keeps_launch_outcome(repo):
    from atlas.application.chat.runs.launcher import _record_outcome

    _save(repo, "kid", USER, {"parent_conversation_id": "parent", "launch_run_id": "run-1"})
    chat_service = SimpleNamespace(conversation_repository=repo)
    first = SimpleNamespace(conversation_id="kid", user_email=USER, depth=1, run_id="run-1")
    follow_up = SimpleNamespace(conversation_id="kid", user_email=USER, depth=1, run_id="run-2")

    await _record_outcome(chat_service, first, RunStatus.COMPLETED)
    await _record_outcome(chat_service, follow_up, RunStatus.FAILED)

    assert repo.get_conversation("kid", USER)["metadata"]["launch_status"] == "completed"


async def test_all_stored_children_are_listed(repo):
    control_bus.set_run_control_bus(None)
    reset_run_registry()
    _save(repo, "parent-conv", USER)
    for i in range(230):
        _save(repo, f"kid-{i:03}", USER, {
            "parent_conversation_id": "parent-conv", "launch_run_id": f"run-{i:03}",
            "launch_status": "completed",
        })
    set_current_run("turn-2", "parent-conv")

    runs = await list_child_runs({"user_email": USER}, SimpleNamespace(conversation_repository=repo))

    assert len(runs) == 230
    assert {r["run_id"] for r in runs} == {f"run-{i:03}" for i in range(230)}
    result = await get_child_result("run-229", {"user_email": USER}, SimpleNamespace(conversation_repository=repo))
    assert result["conversation_id"] == "kid-229" and result["result"] == "done"
    clear_current_run()
    reset_run_registry()


def test_undecodable_metadata_is_not_rewritten(repo):
    from atlas.modules.chat_history.models import ConversationRecord

    _save(repo, "kid", USER, {"parent_conversation_id": "parent"})
    with repo._get_session() as session:
        session.query(ConversationRecord).filter(ConversationRecord.id == "kid").update(
            {"metadata_json": "{not json"}
        )
        session.commit()

    assert repo.record_launch_outcome("kid", USER, "completed", 1, "run-1") is False

    with repo._get_session() as session:
        assert session.query(ConversationRecord).filter(
            ConversationRecord.id == "kid"
        ).one().metadata_json == "{not json"


async def test_conversation_lookup_uses_latest_outcome(cluster, store):
    store.add("failed-follow-up", USER, "parent-conv", launch_run_id="r1", status="completed")
    store.rows["failed-follow-up"]["metadata"]["latest_status"] = "failed"
    store.add("good-follow-up", USER, "parent-conv", launch_run_id="r2", status="failed")
    store.rows["good-follow-up"]["metadata"]["latest_status"] = "completed"
    context, factory = _on_replica_b(store)

    assert (await get_child_result("failed-follow-up", context, factory))["status"] == "failed"
    assert (await get_child_result("good-follow-up", context, factory))["status"] == "completed"
    assert (await get_child_result("good-follow-up", context, factory))["result"] == "stored answer"
    # The launch run id keeps the launch run's own outcome.
    assert (await get_child_result("r1", context, factory))["status"] == "completed"
    assert (await get_child_result("r2", context, factory))["status"] == "failed"
    listed = {r["conversation_id"]: r["status"] for r in await list_child_runs(context, factory)}
    assert listed == {"failed-follow-up": "failed", "good-follow-up": "completed"}


async def test_follow_up_outcome_is_the_latest(repo):
    from atlas.application.chat.runs.launcher import _record_outcome

    _save(repo, "kid", USER, {"parent_conversation_id": "parent", "launch_run_id": "run-1"})
    chat_service = SimpleNamespace(conversation_repository=repo)
    first = SimpleNamespace(conversation_id="kid", user_email=USER, depth=1, run_id="run-1")
    follow_up = SimpleNamespace(conversation_id="kid", user_email=USER, depth=1, run_id="run-2")

    await _record_outcome(chat_service, first, RunStatus.COMPLETED)
    await _record_outcome(chat_service, follow_up, RunStatus.CANCELLED)

    metadata = repo.get_conversation("kid", USER)["metadata"]
    assert (metadata["launch_status"], metadata["latest_status"]) == ("completed", "cancelled")


async def test_partial_listing_keeps_local_children(cluster, store):
    from atlas.application.chat.runs.launcher import execute_observation_tool

    _, b = cluster
    registry = get_run_registry()
    parent = registry.start(conversation_id="parent-conv", user_email=USER)
    registry.start(
        conversation_id="local-child", user_email=USER, parent_run_id=parent.run_id,
        parent_conversation_id="parent-conv", depth=1,
    )
    set_current_run(parent.run_id, "parent-conv")

    async def down(user_email):
        raise ConnectionError("redis down")

    b.bus.runs_for_user = down
    context = {"user_email": USER, "factory": SimpleNamespace(conversation_repository=store)}

    outcome = await execute_observation_tool(SimpleNamespace(id="1", arguments={}), context, "atlas_get_subagent_runs")

    assert outcome.success
    payload = json.loads(outcome.content)
    assert payload["partial"] is True and "warning" in payload
    assert [r["conversation_id"] for r in payload["runs"]] == ["local-child"]
