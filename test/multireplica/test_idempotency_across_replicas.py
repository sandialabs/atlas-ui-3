"""Agent Runs API idempotency holds across replicas (issue #1220).

The same launch body and idempotency key sent to replica A and then to replica
B (as a client retrying through a load balancer does) must launch one run and
create one conversation; the replay on B answers 200 with A's run handle.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from harness.cluster import directive
from harness.infra import freeze_container, thaw_container


def _conversations(cluster, user):
    return cluster.conversation_ids(cluster.a, user)


def test_same_key_and_body_on_another_replica_returns_the_original_run(cluster):
    user = cluster.new_user()
    message = directive("idem-xrep", sleep=3)
    first_status, first = cluster.launch(cluster.a, user, message, idempotency_key="xrep-key")
    assert first_status == 202, (first_status, first)
    run_id = first["run_id"]
    try:
        second_status, second = cluster.launch(cluster.b, user, message, idempotency_key="xrep-key")
        assert second_status == 200, (second_status, second)
        assert second["run_id"] == run_id
        assert second["conversation_id"] == first["conversation_id"]
        assert second["idempotent_replay"] is True
        # The status is the run's real one, read from the shared record.
        assert second["status"] in ("queued", "running", "completed"), second
        # And a replay on the owner agrees with it.
        third_status, third = cluster.launch(cluster.a, user, message, idempotency_key="xrep-key")
        assert third_status == 200 and third["run_id"] == run_id, (third_status, third)

        cluster.wait_terminal(cluster.a, run_id, timeout=45)
        deadline = time.monotonic() + 30
        ids = _conversations(cluster, user)
        while first["conversation_id"] not in ids and time.monotonic() < deadline:
            time.sleep(0.5)
            ids = _conversations(cluster, user)
        assert ids == [first["conversation_id"]], f"one key, expected one conversation: {ids}"
        # Replica B never held a record of a second run.
        status, body = cluster.call(cluster.b, "GET", "/api/agent-runs?user=" + user, service=True)
        assert status == 200
        assert len(body["runs"]) <= 1
    finally:
        cluster.cancel_everywhere(run_id)


def test_same_key_with_a_different_body_on_another_replica_is_refused(cluster):
    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("idem-one", sleep=3), idempotency_key="diff-key")
    try:
        status, body = cluster.launch(cluster.b, user, directive("idem-two", sleep=3), idempotency_key="diff-key")
        assert status == 409, (status, body)
        assert body["detail"]["code"] == "idempotency_conflict", body
    finally:
        cluster.cancel_everywhere(first["run_id"])


def test_simultaneous_same_key_requests_on_both_replicas_launch_once(cluster):
    user = cluster.new_user()
    cluster.ensure_workspace(user)
    message = directive("idem-race", sleep=3)

    def send(replica):
        return cluster.launch(replica, user, message, idempotency_key="race-key")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, [cluster.a, cluster.b, cluster.a, cluster.b]))
    statuses = sorted(status for status, _ in results)
    try:
        assert statuses.count(202) == 1, results
        # The others replay (200) or are told the first is still being admitted (409).
        assert all(status in (200, 409) for status in statuses if status != 202), results
        winner = next(body for status, body in results if status == 202)
        for status, body in results:
            if status == 200:
                assert body["run_id"] == winner["run_id"], results
    finally:
        cluster.cancel_everywhere(winner["run_id"])


def test_a_keyless_launch_is_never_deduplicated(cluster):
    user = cluster.new_user()
    message = directive("idem-none", sleep=3)
    one = cluster.launch_ok(cluster.a, user, message)
    two = cluster.launch_ok(cluster.b, user, message)
    try:
        assert one["run_id"] != two["run_id"]
    finally:
        cluster.cancel_everywhere(one["run_id"])
        cluster.cancel_everywhere(two["run_id"])


def test_redis_outage_refuses_keyed_launches_and_launches_nothing(cluster):
    container = cluster.infra.redis_container
    if not container:
        pytest.fail("this test needs the harness-managed Redis container")
    user = cluster.new_user()
    cluster.ensure_workspace(user)
    message = directive("idem-down", sleep=1)
    freeze_container(container)
    try:
        status, body = cluster.launch(cluster.a, user, message, idempotency_key="down-key")
    finally:
        thaw_container(container)
    assert status == 503, (status, body)
    assert body["detail"]["code"] == "unavailable", body
    # The frozen Redis may still have executed the reserve command it had
    # buffered when it thawed, so the key can read as "still admitting" until
    # its pending reservation expires; it must never have launched a run.
    deadline = time.monotonic() + 60
    retry = None
    while time.monotonic() < deadline:
        retry = cluster.launch(cluster.a, user, message, idempotency_key="down-key")
        if retry[0] == 202:
            break
        assert retry[0] == 409 and retry[1]["detail"]["code"] == "idempotency_in_progress", retry
        time.sleep(5)
    status, runs = cluster.call(cluster.a, "GET", "/api/agent-runs?user=" + user, service=True)
    assert status == 200
    assert len(runs["runs"]) <= 1, runs
    # A fresh key is fully usable straight away.
    fresh = cluster.launch(cluster.a, user, message, idempotency_key="after-outage-key")
    assert fresh[0] == 202, fresh
    cluster.cancel_everywhere(fresh[1]["run_id"])
    if retry[0] == 202:
        cluster.cancel_everywhere(retry[1]["run_id"])


def test_follow_up_retry_on_another_replica_replays_even_naming_a_sibling_run(cluster):
    """The follow-up itself is served by the owner; its retry through the
    load balancer lands on B, which holds no such run, and must replay."""
    user = cluster.new_user()
    first = cluster.launch_ok(cluster.a, user, directive("fu-first", sleep=0))
    cluster.wait_terminal(cluster.a, first["run_id"], timeout=45)
    body = {"message": directive("fu-second", sleep=2), "idempotency_key": "fu-key"}
    status, follow = cluster.call(
        cluster.a, "POST", f"/api/agent-runs/{first['run_id']}/follow-up", service=True, json_body=body
    )
    assert status == 202, (status, follow)
    follow_id = follow["run_id"]
    try:
        # B, naming the run the follow-up addressed.
        status, replay = cluster.call(
            cluster.b, "POST", f"/api/agent-runs/{first['run_id']}/follow-up", service=True, json_body=body
        )
        assert status == 200, (status, replay)
        assert replay["run_id"] == follow_id and replay["idempotent_replay"] is True, replay
        assert replay["conversation_id"] == first["conversation_id"]
        # B, naming the sibling run the follow-up created.
        status, replay = cluster.call(
            cluster.b, "POST", f"/api/agent-runs/{follow_id}/follow-up", service=True, json_body=body
        )
        assert status == 200 and replay["run_id"] == follow_id, (status, replay)
        # A different body under the same key is still refused on B.
        status, refused = cluster.call(
            cluster.b, "POST", f"/api/agent-runs/{first['run_id']}/follow-up", service=True,
            json_body={**body, "message": "something else"},
        )
        assert status == 409 and refused["detail"]["code"] == "idempotency_conflict", (status, refused)
        cluster.wait_terminal(cluster.a, follow_id, timeout=45)
    finally:
        cluster.cancel_everywhere(follow_id)
