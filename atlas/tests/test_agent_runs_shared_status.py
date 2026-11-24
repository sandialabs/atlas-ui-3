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



@pytest.mark.asyncio
async def test_shared_listing_skips_unreadable_snapshot(monkeypatch, caplog):
    from atlas.application.chat.runs import external_runs

    def raw(run_id, snapshot):
        return {
            "run_id": run_id, "conversation_id": "conv",
            "user_email": "owner@example.com", "source": external_runs.API_SOURCE,
            "status": "completed", "created_at": "1000", "snapshot": snapshot,
        }

    class Coordinator:
        async def read_api_runs(self, user_email):
            return [raw("good", "{}"), raw("bad", "{"), raw("malformed", '{"created_at": "bad"}')]

    monkeypatch.setattr(external_runs, "get_run_coordinator", lambda: Coordinator())
    records = await external_runs.list_api_runs_anywhere("owner@example.com")
    assert [record["run_id"] for record in records] == ["good"]
    assert "Skipping unreadable API run snapshot" in caplog.text
    assert "Skipping API run record with invalid listing metadata" in caplog.text
