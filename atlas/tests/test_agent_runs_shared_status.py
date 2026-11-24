"""Agent Runs API projection parity across replicas (issue #1220)."""

import pytest

from atlas.application.chat.runs.external_runs import _status, _status_from_shared
from atlas.application.chat.runs.leases import run_snapshot
from atlas.application.chat.runs.registry import RunRegistry, RunStatus


@pytest.mark.parametrize("status,reason", [
    (RunStatus.COMPLETED, "completed"),
    (RunStatus.CANCELLED, "user_cancelled"),
    (RunStatus.FAILED, "error"),
])
def test_shared_projection_preserves_terminal_reason(status, reason):
    registry = RunRegistry()
    record = registry.start(conversation_id="conv", user_email="owner@example.com")
    registry.set_status(record.run_id, status, reason=reason)
    shared = _status_from_shared(run_snapshot(record))
    assert shared["end_reason"] == _status(record)["end_reason"] == reason
