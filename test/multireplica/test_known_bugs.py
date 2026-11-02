"""Bugs the harness found that are not part of #1128's coordination work.

Strict xfail like the gap tests: the fix flips the test to XPASS, which fails
the run until the marker is removed.
"""

import pytest
from harness.cluster import Cluster, GapNotClosed, directive, expect


@pytest.mark.xfail(
    strict=True,
    raises=GapNotClosed,
    reason="MAX_CONCURRENT_RUNS_PER_USER is only applied when a WebSocket connects (atlas/main.py)",
)
def test_configured_cap_applies_before_any_websocket(harness_dir, shared_infra, mock_llm):
    """A replica configured with a cap of 2 must refuse a third API run.

    The registry's cap is set from settings inside the WebSocket handler, so a
    replica that has only served REST traffic (the agent-runs API, a fresh pod)
    still uses the registry's built-in default of 5.
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
