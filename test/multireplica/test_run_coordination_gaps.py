"""#1128 acceptance criteria that main does not meet yet.

Each test is ``xfail(strict=True, raises=GapNotClosed)``. Preconditions use a
plain ``assert`` (a broken precondition is a real failure); only the final
criterion uses ``expect(...)``, which raises ``GapNotClosed``. When a #1128 PR
closes a gap the test XPASSes, strict mode fails the run, and that PR removes
the marker -- the test then guards the behavior from then on.
"""

import time

import pytest
from harness.cluster import RUN_CAP, GapNotClosed, directive, expect

gap = pytest.mark.xfail(strict=True, raises=GapNotClosed, reason="#1128 distributed run coordination")


@gap
def test_non_owner_can_inspect_an_active_run(cluster):
    user = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, user, directive("inspect", sleep=6))
    try:
        assert cluster.wait_status(cluster.a, handle["run_id"], "running")["status"] == "running"
        status, body = cluster.run_status(cluster.b, handle["run_id"])
        expect(status == 200 and body.get("status") == "running",
               f"replica B cannot see run {handle['run_id']} owned by A: {status} {body}")
    finally:
        cluster.cancel_everywhere(handle["run_id"])


@gap
def test_stop_sent_to_non_owner_reaches_the_owner(cluster):
    user = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, user, directive("stop", sleep=8))
    try:
        cluster.wait_status(cluster.a, handle["run_id"], "running")
        status, body = cluster.cancel(cluster.b, handle["run_id"])
        expect(status == 200, f"cancel via B refused: {status} {body}")
        final = cluster.wait_terminal(cluster.a, handle["run_id"], timeout=10)
        expect(final["status"] == "cancelled", f"run on A not cancelled by B: {final}")
    finally:
        cluster.cancel_everywhere(handle["run_id"])


@gap
def test_per_user_cap_holds_across_replicas(cluster):
    """A user at the cap on A cannot start one more run on B."""
    user = cluster.new_user()
    runs = []
    try:
        for i in range(RUN_CAP):
            runs.append(cluster.launch_ok(cluster.a, user, directive(f"cap{i}", sleep=8))["run_id"])
        # The per-replica cap is enforced today; prove the harness sees it.
        status, body = cluster.launch(cluster.a, user, directive("cap-over-a", sleep=8))
        assert status == 429, f"cap not enforced on A itself: {status} {body}"

        status, body = cluster.launch(cluster.b, user, directive("cap-over-b", sleep=8))
        if status in (200, 202):
            runs.append(body["run_id"])
        expect(status == 429, f"B admitted run {RUN_CAP + 1} for a user at the cap: {status} {body}")
    finally:
        for run_id in runs:
            cluster.cancel_everywhere(run_id)


@gap
def test_concurrent_admission_is_atomic_across_replicas(cluster):
    """Launches racing on A and B at once: no more than the cap are admitted."""
    from concurrent.futures import ThreadPoolExecutor

    user = cluster.new_user()
    cluster.ensure_workspace(user)
    targets = [cluster.a, cluster.b] * (RUN_CAP + 1)
    with ThreadPoolExecutor(len(targets)) as pool:
        results = list(pool.map(
            lambda pair: cluster.launch(pair[1], user, directive(f"race{pair[0]}", sleep=8)),
            enumerate(targets),
        ))
    # Transport contract first: every answer is a JSON object, so a proxy error
    # page fails the test instead of hiding behind the criterion.
    assert all(isinstance(body, dict) for _, body in results), results
    admitted = [body["run_id"] for status, body in results if status in (200, 202)]
    try:
        refused = [status for status, _ in results if status not in (200, 202)]
        assert set(refused) <= {429}, results
        expect(len(admitted) <= RUN_CAP, f"{len(admitted)} runs admitted across replicas with a cap of {RUN_CAP}")
    finally:
        for run_id in admitted:
            cluster.cancel_everywhere(run_id)


@gap
def test_owner_loss_is_reported_as_interrupted(cluster):
    """Kill the owner mid-run: another replica reports the run interrupted, keeping completed work."""
    user = cluster.new_user()
    key = f"lost-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=3, steps=2))
    cluster.wait_side_effects(key, 1)
    cluster.a.kill()

    deadline = time.monotonic() + 30
    status, body = None, None
    while time.monotonic() < deadline:
        status, body = cluster.run_status(cluster.b, handle["run_id"])
        if status == 200 and body.get("status") in ("failed", "cancelled", "completed"):
            break
        time.sleep(0.5)
    expect(status == 200 and body.get("status") == "failed",
           f"replica B does not report the lost run as interrupted: {status} {body}")
    assert len(cluster.side_effects(key)) == 1, "side effect replayed"


@gap
def test_paused_owner_cannot_write_after_being_declared_lost(cluster):
    """Fencing: an owner frozen past its lease must not finish the run once another replica failed it."""
    user = cluster.new_user()
    key = f"fence-{user.split('@')[0]}"
    handle = cluster.launch_ok(cluster.a, user, directive(key, tool="side_effect", sleep=2, steps=3))
    cluster.wait_side_effects(key, 1)
    cluster.a.pause()
    try:
        deadline = time.monotonic() + 30
        status, body = None, None
        while time.monotonic() < deadline:
            status, body = cluster.run_status(cluster.b, handle["run_id"])
            if status == 200 and body.get("status") == "failed":
                break
            time.sleep(0.5)
        expect(status == 200 and body.get("status") == "failed",
               f"run was never declared lost while its owner was frozen: {status} {body}")
    finally:
        cluster.a.resume()
    time.sleep(8)  # let the resumed owner try to carry on
    status, body = cluster.run_status(cluster.b, handle["run_id"])
    expect(body.get("status") == "failed", f"resumed owner overwrote the failover outcome: {body}")
    expect(len(cluster.side_effects(key)) == 1,
           f"fenced owner kept executing tools: {cluster.side_effects(key)}")
