"""Fixtures for the multi-replica harness (issue #1128).

One Redis, one Postgres and one scripted mock LLM per test session; two Atlas
replicas started once and healed (restarted or resumed) after any test that
kills or pauses one. Run with ``bash test/multireplica_tests.sh``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from harness.cluster import Cluster
from harness.infra import InfraUnavailable, start_shared_infra
from harness.replica import MockLLM


@pytest.fixture(scope="session")
def harness_dir():
    keep = os.environ.get("ATLAS_MULTIREPLICA_KEEP")
    path = Path(tempfile.mkdtemp(prefix="atlas-multireplica-"))
    print(f"\n[multireplica] work dir: {path}")
    yield path
    if not keep:
        shutil.rmtree(path, ignore_errors=True)


def _infra_absent() -> bool:
    """Nothing was offered at all: no podman and no ATLAS_TEST_*_URL."""
    return (
        shutil.which("podman") is None
        and not os.environ.get("ATLAS_TEST_REDIS_URL")
        and not os.environ.get("ATLAS_TEST_POSTGRES_URL")
    )


@pytest.fixture(scope="session")
def shared_infra():
    """Redis + Postgres for the session.

    The only skip is a machine that offers no infrastructure at all, and only
    when the run is not required (``test/multireplica_tests.sh`` sets
    ``ATLAS_MULTIREPLICA_REQUIRED=1``). A broken podman, a failed image pull, a
    readiness timeout or an unreachable supplied URL fails the run: a gate that
    "passes" with zero coverage is worse than one that fails loudly.
    """
    try:
        infra = start_shared_infra()
    except (InfraUnavailable, ImportError) as exc:
        message = f"multi-replica harness needs Redis and Postgres: {exc}"
        if _infra_absent() and not os.environ.get("ATLAS_MULTIREPLICA_REQUIRED"):
            pytest.skip(message)
        pytest.fail(message, pytrace=False)
        raise  # unreachable (skip/fail raise); tells static analysis `infra` is always bound below
    try:
        yield infra
    finally:
        infra.close()


@pytest.fixture(scope="session")
def mock_llm(harness_dir):
    mock = MockLLM(harness_dir)
    try:
        mock.start()
        yield mock
    finally:
        mock.stop()


@pytest.fixture(scope="session")
def _session_cluster(harness_dir, shared_infra, mock_llm):
    cluster = Cluster(harness_dir, shared_infra, mock_llm, size=2)
    try:
        cluster.start()
    except BaseException:
        cluster.close()
        raise
    try:
        yield cluster
    finally:
        cluster.close()


@pytest.fixture
def cluster(_session_cluster, request):
    """Two running replicas, A and B. Restored to running after the test."""
    _session_cluster.heal()
    request.node._multireplica_cluster = _session_cluster
    yield _session_cluster
    _session_cluster.heal()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    cluster = getattr(item, "_multireplica_cluster", None)
    if cluster is not None and report.when == "call" and report.failed:
        for replica in cluster.replicas:
            report.sections.append((f"replica {replica.name} log tail", replica.log_tail()))
