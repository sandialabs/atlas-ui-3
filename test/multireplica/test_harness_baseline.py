"""What already works across replicas, and the harness checking itself.

These must pass on main. Between them they prove the replicas really share
state: durable data through PostgreSQL, live sessions through Redis. If one fails, either the harness is broken or a change
regressed something multi-replica deployments rely on today.
"""

import time

from harness.cluster import directive


def test_replicas_are_independent_processes(cluster):
    a, b = cluster.a, cluster.b
    assert a.port != b.port
    assert a.proc.pid != b.proc.pid
    for replica in (a, b):
        status, _ = cluster.call(replica, "GET", "/api/health")
        assert status == 200


def test_durable_state_is_shared_through_postgres(cluster):
    """A workspace and a finished run's conversation written via A are readable via B."""
    user = cluster.new_user()
    cluster.ensure_workspace(user, replica=cluster.a)
    status, workspaces = cluster.call(cluster.b, "GET", "/api/workspaces", user=user)
    assert status == 200, workspaces
    assert "Multireplica harness" in str(workspaces)

    handle = cluster.launch_ok(cluster.a, user, "hello from replica A")
    final = cluster.wait_terminal(cluster.a, handle["run_id"])
    assert final["status"] == "completed", final
    assert final["result"] == "ECHO: hello from replica A", final
    assert handle["conversation_id"] in cluster.conversation_ids(cluster.b, user)


def test_live_sessions_are_shared_through_redis(cluster):
    """A run's session is written to the shared Redis, not a replica's memory.

    Guards against a silent fallback to the process-local session store, which
    would leave every other test measuring single-replica behaviour.
    """
    import json

    import redis

    user = cluster.new_user()
    message = f"session probe {user}"
    handle = cluster.launch_ok(cluster.a, user, message)
    final = cluster.wait_terminal(cluster.a, handle["run_id"])
    assert final["status"] == "completed", final

    client = redis.Redis.from_url(cluster.infra.redis_url)
    try:
        pattern = f"{cluster.infra.redis_key_prefix}:session:*"
        sessions = []
        for key in client.scan_iter(match=pattern, count=500):
            raw = client.hget(key, "data")
            if raw:
                sessions.append(json.loads(raw))
    finally:
        client.close()
    mine = [s for s in sessions if s.get("user_email") == user]
    assert mine, f"no session for {user} under {pattern}; found {len(sessions)} other session(s)"
    contents = json.dumps([s.get("history") for s in mine])
    assert message in contents and f"ECHO: {message}" in contents, contents[:2000]


def test_side_effect_tool_counts_every_execution(cluster):
    """Harness self-check: each tool call is recorded once, attributed to its replica."""
    user = cluster.new_user()
    key = f"count-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.b, user, directive(key, tool="side_effect", steps=2))
    final = cluster.wait_terminal(cluster.b, handle["run_id"])
    assert final["status"] == "completed", final
    records = cluster.side_effects(key)
    assert len(records) == 2, records
    assert {r["replica"] for r in records} == {"B"}, records


def test_crashed_owner_never_replays_a_side_effect(cluster):
    """Kill the owner mid-run: the executed step is not run again, by anyone.

    Guards the #1128 rule "do not automatically replay side-effecting tools"
    against the failover work that is still to come.
    """
    user = cluster.new_user()
    key = f"noreplay-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=3, steps=2))
    assert handle["run_id"]
    cluster.wait_side_effects(key, 1)
    cluster.a.kill()

    cluster.a.start()
    # Long enough for a lease to lapse and an orphan sweep to run once the
    # coordination work lands; today nothing should touch the run at all.
    time.sleep(5)
    records = cluster.side_effects(key)
    assert len(records) == 1, f"side effect replayed after owner loss: {records}"


def test_pause_and_resume_a_replica(cluster):
    """Harness self-check: a paused replica stops answering and recovers on resume."""
    import httpx
    import pytest

    cluster.b.pause()
    with pytest.raises(httpx.TimeoutException):
        cluster.call(cluster.b, "GET", "/api/health", timeout=1.5)
    status, _ = cluster.call(cluster.a, "GET", "/api/health")
    assert status == 200
    cluster.b.resume()
    status, _ = cluster.call(cluster.b, "GET", "/api/health")
    assert status == 200
