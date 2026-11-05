"""Bugs the harness found that are not part of #1128's coordination work.

Open bugs are strict xfail like the gap tests: the fix flips the test to XPASS,
which fails the run until the marker is removed. Fixed ones stay as
regression tests.
"""

from harness.cluster import Cluster, directive, expect


def test_configured_cap_applies_before_any_websocket(harness_dir, shared_infra, mock_llm):
    """A replica configured with a cap of 2 must refuse a third API run.

    Found by PR #1146: the registry's cap used to be set from settings only
    inside the WebSocket handler, so a replica that had served only REST
    traffic (the agent-runs API, a fresh pod) used the built-in default of 5.
    Fixed in #1128: every admission applies the configured cap.
    """
    single = Cluster(harness_dir / "cap-bug", shared_infra, mock_llm, size=1,
                     overrides={"MAX_CONCURRENT_RUNS_PER_USER": "2"})
    runs = []
    try:
        single.start()
        user = single.new_user()
        for i in range(2):
            runs.append(single.launch_ok(single.a, user, directive(f"capbug{i}", sleep=8))["run_id"])
        status, body = single.launch(single.a, user, directive("capbug-over", sleep=8))
        if status in (200, 202):
            runs.append(body["run_id"])
        expect(status == 429, f"third run admitted with MAX_CONCURRENT_RUNS_PER_USER=2: {status}")
    finally:
        for run_id in runs:
            single.cancel_everywhere(run_id)
        single.close()
