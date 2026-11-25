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


def test_refused_publication_reconciles_after_lease_expiry(shared_infra, monkeypatch):
    """Real Redis: lost publication replies and cleanup outage outlive a lease."""
    import asyncio
    from uuid import uuid4

    import pytest
    import redis.asyncio as redis
    from redis.exceptions import ConnectionError

    from atlas.application.chat.runs.coordination import RunCoordinationUnavailableError
    from atlas.application.chat.runs.leases import RunLeaseGuard
    from atlas.application.chat.runs.registry import RunRegistry
    from atlas.infrastructure.redis import RedisClientManager, RedisSettings
    from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
    from atlas.infrastructure.redis.run_leases import RedisRunLeases

    async def exercise():
        prefix = f"{shared_infra.redis_key_prefix}:refusal-{uuid4().hex}"
        settings = RedisSettings(url=shared_infra.redis_url, key_prefix=prefix)
        async with redis.Redis.from_url(shared_infra.redis_url) as client:
            manager = RedisClientManager(settings, client=client)
            a = RedisRunCoordinator(manager, replica_id="refusal-a", lease_seconds=0.5,
                                    retention_seconds=30)
            b = RedisRunCoordinator(manager, replica_id="refusal-b", lease_seconds=0.5,
                                    retention_seconds=30)
            user = "refusal@example.com"
            spec = {"source": "agent_runs_api", "tools": ["read"]}

            async def claim(run_id, previous=None):
                return await a.claim(
                    run_id=run_id, conversation_id="conversation", user_email=user,
                    max_runs_per_user=5, source="agent_runs_api",
                    api_continuation=spec, follow_up_of=previous,
                )

            first = await claim("first")
            await a.publish_api_admission(first)
            await a.release(first, "completed")
            refused = await claim("refused", first.run_id)

            publish = a._publish_api

            async def lose_reply(**kwargs):
                await publish(**kwargs)
                raise ConnectionError("publication reply lost")

            with monkeypatch.context() as faults:
                faults.setattr(a, "_publish_api", lose_reply)
                with pytest.raises(RunCoordinationUnavailableError):
                    await a.publish_api_admission(refused)

                async def unavailable(**kwargs):
                    raise ConnectionError("cleanup unavailable")

                faults.setattr(a, "_release", unavailable)
                released = await a.release(refused, "admission_refused")
                assert not released
                await asyncio.sleep(0.7)
            # The original coordinator and its process-local retry queue are
            # no longer available. A fresh replica must discover the obligation.
            recovered = RedisRunCoordinator(manager, replica_id="replacement",
                                            lease_seconds=0.5, retention_seconds=30)
            guard = RunLeaseGuard(recovered, RedisRunLeases(recovered), RunRegistry())
            await guard.sweep_once()
            latest = await b.read_api_continuation("conversation")
            assert latest["run_id"] == first.run_id
            assert [r["run_id"] for r in await b.read_api_runs(user)] == [first.run_id]
            assert await b.read(refused.run_id) is None
            slots = await client.zcard(b.keys.user(user))
            assert slots == 0
            # Repeating cleanup must not change a subsequently admitted turn.
            next_run = await claim("next", first.run_id)
            await a.publish_api_admission(next_run)
            held = await a.renew([next_run], {next_run.run_id: "running"})
            assert held == {next_run.run_id: True}
            assert (await b.read(next_run.run_id))["api_admission_unconfirmed"] == "1"
            activated = await a.activate_api(next_run)
            assert activated is True
            assert "api_admission_unconfirmed" not in await b.read(next_run.run_id)
            await a.release(refused, "admission_refused")
            assert (await b.read_api_continuation("conversation"))["run_id"] == next_run.run_id
            # A genuinely activated run must survive owner loss as interrupted,
            # not roll back to the previous turn.
            await asyncio.sleep(0.7)
            await guard.sweep_once()
            latest = await b.read_api_continuation("conversation")
            assert latest["run_id"] == next_run.run_id
            assert latest["interrupted"] == "1"
            assert latest["status"] == "failed"
            await a.release(next_run, "admission_refused")
            assert (await b.read_api_continuation("conversation"))["run_id"] == next_run.run_id

    asyncio.run(exercise())
