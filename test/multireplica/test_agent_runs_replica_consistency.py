"""Remaining cross-replica Agent Runs API contract (issue #1220)."""

from harness.cluster import directive


def test_fresh_follow_up_survives_original_replica_shutdown(cluster):
    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("fu-origin", sleep=0))
    run_id = first["run_id"]
    terminal = cluster.wait_terminal(cluster.b, run_id)
    assert terminal["status"] == "completed", terminal
    cluster.a.stop()
    status, follow = cluster.call(
        cluster.b, "POST", f"/api/agent-runs/{run_id}/follow-up", service=True,
        json_body={"message": "continue on B", "idempotency_key": "fresh-follow-up"},
    )
    assert status == 202, (status, follow)
    assert follow["run_id"] != run_id
    assert follow["conversation_id"] == first["conversation_id"]
    assert follow["previous_run_id"] == run_id
    try:
        final = cluster.wait_terminal(cluster.b, follow["run_id"])
        assert final["status"] == "completed", final
        status, replay = cluster.call(
            cluster.b, "POST", f"/api/agent-runs/{run_id}/follow-up", service=True,
            json_body={"message": "continue on B", "idempotency_key": "fresh-follow-up"},
        )
        assert status == 200 and replay["run_id"] == follow["run_id"], (status, replay)
    finally:
        cluster.cancel(cluster.b, follow["run_id"])


def test_terminal_status_agrees_across_replicas(cluster):
    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("status-parity", sleep=0))
    local = cluster.wait_terminal(cluster.a, first["run_id"])
    remote = cluster.wait_terminal(cluster.b, first["run_id"])
    assert local["status"] == remote["status"] == "completed"
    assert local["end_reason"] == remote["end_reason"] == "completed"


def test_retained_api_list_is_replica_independent(cluster):
    user = cluster.new_user()
    other_user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("list-parity", sleep=0))
    cluster.wait_terminal(cluster.b, first["run_id"])
    other = cluster.launch_ok(cluster.b, other_user, directive("other-user", sleep=0))
    cluster.wait_terminal(cluster.a, other["run_id"])
    second = cluster.launch_ok(cluster.b, user, directive("list-newest", sleep=0))
    cluster.wait_terminal(cluster.a, second["run_id"])
    for replica in (cluster.a, cluster.b):
        status, body = cluster.call(
            replica, "GET", "/api/agent-runs?user=" + user.upper(), service=True
        )
        assert status == 200, (status, body)
        assert [run["run_id"] for run in body["runs"]] == [second["run_id"], first["run_id"]], body
        assert all(run["end_reason"] == "completed" for run in body["runs"])
        status, body = cluster.call(
            replica, "GET", "/api/agent-runs?user=" + other_user, service=True
        )
        assert status == 200
        assert [run["run_id"] for run in body["runs"]] == [other["run_id"]]


def test_racing_follow_ups_across_replicas_admit_only_one(cluster):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("race-origin", sleep=0))
    cluster.wait_terminal(cluster.b, first["run_id"])
    gate = Barrier(2)

    def follow(replica):
        gate.wait(timeout=5)
        return cluster.call(
            replica, "POST", f"/api/agent-runs/{first['run_id']}/follow-up",
            service=True, json_body={
                "message": directive("race-follow", sleep=5),
                "idempotency_key": "follow-" + replica.name,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(follow, (cluster.a, cluster.b)))
    assert sorted(status for status, _ in results) == [202, 409], results
    winner = next(body for status, body in results if status == 202)
    refused = next(body for status, body in results if status == 409)
    assert refused["detail"]["code"] == "conversation_busy", refused
    try:
        status, body = cluster.call(
            cluster.a, "GET", "/api/agent-runs?user=" + user, service=True
        )
        assert status == 200
        assert {r["run_id"] for r in body["runs"]} == {first["run_id"], winner["run_id"]}
    finally:
        cluster.cancel_everywhere(winner["run_id"])


def test_missing_continuation_refuses_on_both_replicas(cluster):
    import redis

    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("missing-origin", sleep=0))
    cluster.wait_terminal(cluster.b, first["run_id"])
    key = f"{cluster.infra.redis_key_prefix}:run-api-latest:{first['conversation_id']}"
    with redis.Redis.from_url(cluster.infra.redis_url) as client:
        deleted = client.delete(key)
        assert deleted == 1
    for replica in (cluster.a, cluster.b):
        status, body = cluster.call(
            replica, "POST", f"/api/agent-runs/{first['run_id']}/follow-up",
            service=True, json_body={"message": "must not run"},
        )
        assert status == 503 and body["detail"]["code"] == "unavailable", (status, body)
