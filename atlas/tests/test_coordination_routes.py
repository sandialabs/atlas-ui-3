"""Coordination admin routes, live database probe and shutdown interrupt (issue #1129)."""

import asyncio

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.application.chat.runs import registry as registry_module
from atlas.application.chat.runs.registry import RunStatus, get_run_registry, reset_run_registry
from atlas.core.coordination_metrics import coordination_metrics, reset_coordination_metrics
from atlas.infrastructure import dependency_probes
from atlas.infrastructure.app_factory import app_factory
from atlas.infrastructure.replica_lifecycle import interrupt_runs_for_shutdown
from atlas.modules.config import config_manager

pytestmark = pytest.mark.usefixtures("mock_admin_authorization")


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_run_registry()
    reset_coordination_metrics()
    dependency_probes.reset_probe_cache()
    yield
    reset_run_registry()
    reset_coordination_metrics()
    dependency_probes.reset_probe_cache()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_engine(monkeypatch):
    """Keep the probes off the process-wide chat-history engine."""
    import atlas.modules.chat_history as chat_history

    monkeypatch.setattr(chat_history, "get_engine", lambda: object())


def _admin():
    return {"X-User-Email": config_manager.app_settings.admin_test_user}


def test_coordination_status_lists_runs_without_user_identity(client):
    registry = get_run_registry()
    record = registry.start(conversation_id="conv-1", user_email="someone@example.com")
    registry.set_status(record.run_id, RunStatus.RUNNING)
    registry.mark_detached(record.run_id)

    response = client.get("/admin/coordination", headers=_admin())
    assert response.status_code == 200
    body = response.json()
    assert body["replica"]["replica_id"]
    assert body["runs"]["counts"]["running"] == 1
    assert body["runs"]["counts"]["detached"] == 1
    [item] = body["runs"]["items"]
    assert item["run_id"] == record.run_id and item["status"] == "running"
    assert "someone@example.com" not in response.text
    assert "conv-1" not in response.text


def test_coordination_status_includes_terminal_on_request(client):
    registry = get_run_registry()
    record = registry.start(conversation_id="conv-1", user_email="someone@example.com")
    registry.interrupt_active()
    body = client.get("/admin/coordination?include_terminal=true", headers=_admin()).json()
    [item] = body["runs"]["items"]
    assert item["outcome"] == "interrupted" and item["end_reason"] == "replica_shutdown"
    assert item["run_id"] == record.run_id
    assert body["runs"]["counts"]["interrupted"] == 1


def test_metrics_endpoint_is_prometheus_text(client):
    get_run_registry().start(conversation_id="conv-1", user_email="someone@example.com")
    response = client.get("/admin/coordination/metrics", headers=_admin())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'atlas_runs{replica="' in response.text
    assert 'status="queued"} 1' in response.text
    assert "atlas_runs_admitted_total" in response.text


@pytest.mark.parametrize("path", ["/admin/coordination", "/admin/coordination/metrics"])
def test_non_admin_is_refused(client, path):
    assert client.get(path, headers={"X-User-Email": "user@example.com"}).status_code in (302, 403)


def test_health_reports_database_outage_after_startup(client, monkeypatch, fake_engine):
    """A database that goes away after boot degrades /api/health (still HTTP 200)."""
    monkeypatch.setitem(app_factory.startup_outcomes, "chat_history", "ok")
    monkeypatch.setattr(app_factory, "conversation_repository", object())

    def _down(engine):
        raise ConnectionRefusedError("db down")

    monkeypatch.setattr(dependency_probes, "_select_one", _down)
    monkeypatch.setattr(dependency_probes, "database_dependency_name", lambda engine: "postgres")
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["chat_history_db"] == "unavailable"
    assert body["status"] == "degraded"
    assert "db down" not in response.text  # detail stays server-side
    assert coordination_metrics().dependencies()["postgres"]["status"] == "unavailable"

    dependency_probes.reset_probe_cache()
    monkeypatch.setattr(dependency_probes, "_select_one", lambda engine: None)
    body = client.get("/api/health").json()
    assert body["checks"]["chat_history_db"] == "ok"
    assert coordination_metrics().dependencies()["postgres"]["status"] == "ok"


def test_database_probe_is_bounded_and_single_flight(monkeypatch, fake_engine):
    import threading

    release = threading.Event()
    calls = []

    def _hang(engine):
        calls.append(1)
        release.wait(5)

    monkeypatch.setattr(app_factory, "conversation_repository", object())
    monkeypatch.setattr(dependency_probes, "_select_one", _hang)
    monkeypatch.setattr(dependency_probes, "database_dependency_name", lambda engine: "postgres")

    async def _probe_twice():
        first = await dependency_probes.probe_chat_history_db(app_factory, timeout=0.2, use_cache=False)
        second = await dependency_probes.probe_chat_history_db(app_factory, timeout=0.2, use_cache=False)
        return first, second

    try:
        first, second = asyncio.run(_probe_twice())
    finally:
        release.set()
    assert first["status"] == second["status"] == "unavailable"
    assert first["error"] == "Timeout"
    assert len(calls) == 1  # the hung probe was reused, not stacked


def test_shutdown_interrupts_runs_and_waits_for_them():
    async def scenario():
        registry = get_run_registry()
        record = registry.start(conversation_id="conv-1", user_email="someone@example.com")
        unwound = asyncio.Event()

        async def body():
            try:
                await asyncio.sleep(30)
            finally:
                registry.set_status(record.run_id, RunStatus.CANCELLED)  # what a run's own handler does
                unwound.set()

        registry.attach_task(record.run_id, asyncio.create_task(body()))
        await asyncio.sleep(0)
        count = await interrupt_runs_for_shutdown(grace_seconds=2)
        return record, count, unwound.is_set()

    record, count, unwound = asyncio.run(scenario())
    assert count == 1 and unwound
    # Sticky terminal state: the handler's own CANCELLED did not overwrite it.
    assert record.status == RunStatus.FAILED and record.end_reason == "replica_shutdown"
    assert not registry_module._PROJECTION_TASKS
