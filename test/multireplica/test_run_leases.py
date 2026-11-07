"""Ownership leases, heartbeats and fencing across real replicas (#1128).

The gap tests in ``test_run_coordination_gaps.py`` state the acceptance
criteria (owner loss reported as interrupted, a paused owner fenced). These
go one level down and check the coordination state itself in the shared
Redis: the fencing generation, that completed tool output survives a crash,
that a resumed owner writes nothing, and that heartbeats keep a run that
outlives its lease TTL alive.
"""

import time

from harness.cluster import LEASE_TTL, directive


def _wait_failed(cluster, replica, run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        status, body = cluster.run_status(replica, run_id)
        if status == 200 and body.get("status") == "failed":
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} never reported failed on {replica.name}: {body}")


def test_heartbeat_keeps_a_run_longer_than_its_lease(cluster):
    user = cluster.new_user()
    key = f"long-{user.split('@')[0]}"
    steps, seconds = 4, 3
    assert steps * seconds > 1.5 * LEASE_TTL
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=seconds, steps=steps))
    claimed = cluster.run_record(handle["run_id"])
    final = cluster.wait_terminal(cluster.a, handle["run_id"], timeout=60)
    assert final["status"] == "completed", final
    assert final["interrupted"] is False
    assert len(cluster.side_effects(key)) == steps

    record = cluster.run_record(handle["run_id"])
    assert record["status"] == "completed", record
    assert record["generation"] == claimed["generation"], "an orphan sweep fenced a healthy owner"
    assert record["interrupted"] is False, record
    _, seen_by_b = cluster.run_status(cluster.b, handle["run_id"])
    assert seen_by_b["status"] == "completed", seen_by_b


def test_killed_owner_keeps_completed_output_and_bumps_the_generation(cluster):
    user = cluster.new_user()
    key = f"kill-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=4, steps=3))
    run_id = handle["run_id"]
    claimed = cluster.run_record(run_id)
    assert claimed["owner"], "the owner never claimed the run"
    cluster.wait_side_effects(key, 2)  # the first call finished, the second is running
    cluster.a.kill()

    body = _wait_failed(cluster, cluster.b, run_id)
    assert body["interrupted"] is True, body

    record = cluster.run_record(run_id)
    assert record["generation"] > claimed["generation"], record
    assert record["owner"] is None and record["interrupted"] is True, record
    doc = cluster.execution_doc(user, handle["conversation_id"])
    assert doc is not None and doc["status"] == "failed", doc
    statuses = sorted(row["status"] for row in doc["invocations"].values())
    assert statuses == ["completed", "interrupted"], doc["invocations"]

    time.sleep(LEASE_TTL)  # nothing may pick the run back up
    assert len(cluster.side_effects(key)) == 2, cluster.side_effects(key)


def test_resumed_owner_is_fenced_out_of_shared_state(cluster):
    user = cluster.new_user()
    key = f"stop-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=2, steps=3))
    run_id = handle["run_id"]
    cluster.wait_side_effects(key, 1)
    cluster.a.pause()
    try:
        _wait_failed(cluster, cluster.b, run_id)
        record = cluster.run_record(run_id)
        doc = cluster.execution_doc(user, handle["conversation_id"])
    finally:
        cluster.a.resume()
    time.sleep(LEASE_TTL + 2)  # the owner wakes, finishes its tool call, tries to go on

    assert cluster.run_record(run_id) == record, "the fenced owner rewrote the run record"
    assert cluster.execution_doc(user, handle["conversation_id"]) == doc, (
        "the fenced owner rewrote the execution projection"
    )
    assert len(cluster.side_effects(key)) == 1, cluster.side_effects(key)
    # The old owner itself now reports what the cluster decided.
    status, own_view = cluster.run_status(cluster.a, run_id)
    assert status == 200 and own_view["status"] == "failed" and own_view["interrupted"], own_view
