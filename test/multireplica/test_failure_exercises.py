"""Failure exercises with per-replica observability (issue #1129).

Each test breaks something a horizontally scaled deployment depends on -- a
replica (graceful stop), Redis, PostgreSQL -- and checks that the failure is
*visible*: in ``/api/health``, in ``/admin/coordination`` on the affected
replica, and as ``[COORD]`` log lines naming that replica. These pass on main;
they guard the observability contract documented in
``docs/admin/multi-replica-observability.md``.

The dependency outages freeze the harness's own podman containers
(``podman pause``), so they are skipped when the suite runs against supplied
``ATLAS_TEST_*_URL`` servers it must not touch.
"""

from __future__ import annotations

import time

import pytest
from harness.cluster import directive
from harness.infra import freeze_container, thaw_container

# Generous: on a loaded machine a bounded probe can time out once on its own,
# and recovery waits for the next probe after the container thaws.
WAIT_SECONDS = 60.0


def _wait(predicate, what: str, timeout: float = WAIT_SECONDS, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"{what} not observed within {timeout:.0f}s (last: {last!r})")


def _log_lines(replica, *needles: str):
    """``[COORD]`` lines from the replica's console log and its JSON app log.

    The console shows WARNING and above; INFO events are only in
    ``APP_LOG_DIR/app.jsonl``.
    """
    lines = []
    for path in (replica.log_path, replica.workdir / "logs" / "app.jsonl"):
        if path.exists():
            lines += path.read_text(errors="replace").splitlines()
    return [line for line in lines if "[COORD]" in line and all(n in line for n in needles)]


def test_admin_coordination_is_per_replica(cluster):
    """The owner lists its run as running; the other replica reports nothing for it."""
    user = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, user, directive("coord", sleep=6))
    run_id = handle["run_id"]
    try:
        cluster.wait_status(cluster.a, run_id, "running")
        on_a = cluster.coordination(cluster.a)
        assert on_a["replica"]["replica_id"] == "replica-A"
        assert any(r["run_id"] == run_id and r["status"] == "running" for r in on_a["runs"]["items"])
        assert on_a["runs"]["counts"]["running"] >= 1
        assert on_a["dependencies"]["redis"]["status"] == "ok"
        assert on_a["dependencies"]["postgres"]["status"] == "ok"
        on_b = cluster.coordination(cluster.b)
        assert on_b["replica"]["replica_id"] == "replica-B"
        assert all(r["run_id"] != run_id for r in on_b["runs"]["items"])
        assert _log_lines(cluster.a, "event=run_admitted", f"run_id={run_id}", "replica=replica-A")
    finally:
        cluster.cancel_everywhere(run_id)
    final = cluster.wait_terminal(cluster.a, run_id)
    assert final["status"] == "cancelled" and final["end_reason"] == "user_cancelled"


def test_graceful_stop_reports_run_interrupted(cluster):
    """SIGTERM (rollout / scale-in): the owner reports its live run interrupted before it exits.

    The terminal state reaches the shared projection, so the other replica
    serves the conversation as failed with ``end_reason=replica_shutdown``
    instead of "running" until the document expires. The side effect that had
    already run is not replayed.
    """
    user = cluster.new_user()
    key = f"term-{user.split('@')[0]}"
    # The tool records its side effect, then sleeps: 8 s per step leaves a wide
    # margin for the stop to land while step 1 is still running, so a second
    # record could only come from a replay.
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=8, steps=3))
    run_id, conversation_id = handle["run_id"], handle["conversation_id"]
    cluster.wait_side_effects(key, 1)
    status, body = cluster.run_status(cluster.a, run_id)
    assert status == 200 and body["status"] == "running", (status, body)
    assert len(cluster.side_effects(key)) == 1, "step 2 started before the stop; widen the sleep"

    cluster.a.stop()

    assert _log_lines(cluster.a, "event=run_interrupted", f"run_id={run_id}", "reason=replica_shutdown"), \
        cluster.a.log_tail()
    assert _log_lines(cluster.a, "event=replica_shutdown", "interrupted_runs=1")

    def _b_view():
        status, body = cluster.call(cluster.b, "GET", f"/api/conversations/{conversation_id}", user=user)
        execution = body.get("execution") if status == 200 and isinstance(body, dict) else None
        return execution if execution and execution.get("status") == "failed" else None

    execution = _wait(_b_view, "replica B serving the interrupted run as failed", timeout=15)
    assert execution["end_reason"] == "replica_shutdown"
    time.sleep(2)
    assert len(cluster.side_effects(key)) == 1, "side effect replayed after shutdown"


def _attributed(coord, *, down: str, up: str):
    deps = coord["dependencies"]
    ok = coord["status"] == "degraded" and deps[down]["status"] == "unavailable" and deps[up]["status"] == "ok"
    return coord if ok else False


def _require_container(name):
    if not name:
        pytest.skip("dependency outage exercises need the harness's own podman containers")
    return name


def test_redis_outage_is_visible_and_recovers(cluster):
    container = _require_container(cluster.infra.redis_container)
    freeze_container(container)
    try:
        health = _wait(
            lambda: (h := cluster.health(cluster.a))["status"] == "degraded"
            and h.get("session_store", {}).get("status") == "unavailable" and h,
            "degraded /api/health during the Redis outage",
        )
        assert health["session_store"]["status"] == "unavailable", health
        # The outage is attributed to Redis, not reported as a blanket failure.
        _wait(lambda: _attributed(cluster.coordination(cluster.a), down="redis", up="postgres"),
              "/admin/coordination attributing the outage to Redis")
        _wait(lambda: _log_lines(cluster.a, "event=dependency_down", "dependency=redis"), "dependency_down log")
    finally:
        thaw_container(container)
    _wait(lambda: cluster.health(cluster.a)["status"] == "healthy", "healthy /api/health after Redis returned")
    _wait(lambda: _log_lines(cluster.a, "event=dependency_recovered", "dependency=redis"),
          "dependency_recovered log")
    assert cluster.coordination(cluster.a)["dependencies"]["redis"]["status"] == "ok"


def test_postgres_outage_is_visible_and_recovers(cluster):
    """Before #1129 /api/health kept reporting healthy: chat history was only checked at startup."""
    container = _require_container(cluster.infra.postgres_container)
    freeze_container(container)
    try:
        health = _wait(
            lambda: (h := cluster.health(cluster.b))["status"] == "degraded"
            and h["checks"].get("chat_history_db") == "unavailable" and h,
            "degraded /api/health during the PostgreSQL outage",
        )
        assert health["checks"]["chat_history_db"] == "unavailable", health
        assert health["checks"]["chat_history"] == "ok", health  # startup outcome is unchanged
        _wait(lambda: _attributed(cluster.coordination(cluster.b), down="postgres", up="redis"),
              "/admin/coordination attributing the outage to PostgreSQL")
        _wait(lambda: _log_lines(cluster.b, "event=dependency_down", "dependency=postgres"), "dependency_down log")
    finally:
        thaw_container(container)
    _wait(lambda: cluster.health(cluster.b)["status"] == "healthy", "healthy /api/health after PostgreSQL returned")
    _wait(lambda: _log_lines(cluster.b, "event=dependency_recovered", "dependency=postgres"),
          "dependency_recovered log")
