"""End-to-end: the ``smoke`` profile against two real replicas (~1-2 minutes).

Needs podman (or ``ATLAS_TEST_REDIS_URL`` + ``ATLAS_TEST_POSTGRES_URL``), as
the multi-replica harness does. Skipped when neither is available, unless
``ATLAS_LOADTEST_REQUIRED=1`` (the PR validation script sets it), in which
case missing infrastructure fails the test.

The assertions are about the measurement, not about Atlas being fast: every
section of the report the acceptance criteria name must be present and
populated from real traffic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _infra_offered() -> bool:
    return bool(shutil.which("podman")) or bool(
        os.environ.get("ATLAS_TEST_REDIS_URL") and os.environ.get("ATLAS_TEST_POSTGRES_URL"))


@pytest.fixture(scope="module")
def smoke_report(tmp_path_factory):
    if not _infra_offered():
        if os.environ.get("ATLAS_LOADTEST_REQUIRED"):
            pytest.fail("load smoke test needs podman or ATLAS_TEST_REDIS_URL/ATLAS_TEST_POSTGRES_URL")
        pytest.skip("no podman and no ATLAS_TEST_*_URL")
    out = tmp_path_factory.mktemp("smoke")
    result = subprocess.run([sys.executable, "-m", "atlas_load", "--profile", "smoke", "--out", str(out)],
                            cwd=HERE, capture_output=True, text=True, timeout=900)
    print(result.stdout[-6000:])
    print(result.stderr[-4000:], file=sys.stderr)
    assert (out / "report.json").exists(), "the run produced no report"
    report = json.loads((out / "report.json").read_text())
    report["_exit"] = result.returncode
    report["_out"] = out
    return report


def test_smoke_profile_passes_its_slo(smoke_report):
    failed = [c for c in smoke_report["slo"] if not c["pass"]]
    assert not failed, failed
    assert smoke_report["_exit"] == 0


def test_report_has_latency_percentiles_for_every_load_path(smoke_report):
    ops = smoke_report["operations"]
    for op in ("ws_connect", "ws_reconnect", "chat_turn", "chat_first_token", "api_run_launch", "api_run_total"):
        assert ops[op]["ok"] > 0, op
        assert ops[op]["p50_ms"] is not None and ops[op]["p95_ms"] >= ops[op]["p50_ms"], (op, ops[op])


def test_report_has_connections_runs_and_resources(smoke_report):
    peaks = smoke_report["peaks"]
    assert peaks["client.open_connections"] == 20
    assert peaks["client.active_runs"] > 0
    # Server-side view: the replicas really held the sockets and the API runs.
    assert sum(peaks[f"server.replicas.{r}.server_conns"] for r in ("A", "B")) >= 20
    assert sum(peaks.get(f"server_active_api_runs.{r}", 0) for r in ("A", "B")) > 0
    for replica in ("A", "B"):
        assert peaks[f"server.replicas.{replica}.rss_mb"] > 0
        assert peaks[f"server.replicas.{replica}.fds"] > 0
    assert peaks["server.redis.connected_clients"] > 0
    assert peaks["server.postgres.connections"] > 0
    assert smoke_report["mock_stats"]["llm"]["completions"] > 0


def test_outputs_are_written(smoke_report):
    out = smoke_report["_out"]
    assert (out / "summary.txt").read_text().rstrip().endswith("RESULT: PASS")
    header = (out / "timeline.csv").read_text().splitlines()[0].split(",")
    assert header[0] == "t" and "client.open_connections" in header
