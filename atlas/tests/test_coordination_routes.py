"""Coordination admin routes, live database probe and shutdown interrupt (issue #1129)."""

import asyncio

import main
import pytest
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
    return TestClient(main.app)


@pytest.fixture
def fake_engine(monkeypatch):
    """Keep the probes off the process-wide chat-history engine."""
    import atlas.modules.chat_history as chat_history

    monkeypatch.setattr(chat_history, "get_engine", object)


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
    body = client.get("/admin/coordination?include_terminal=true&refresh=true", headers=_admin()).json()
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
    assert dependency_probes._db_state["last"] is None  # forced probes do not populate the cache


def test_cached_probe_is_reused_within_the_window(monkeypatch, fake_engine):
    calls = []
    monkeypatch.setattr(app_factory, "conversation_repository", object())
    monkeypatch.setattr(dependency_probes, "_select_one", lambda engine: calls.append(1))
    monkeypatch.setattr(dependency_probes, "database_dependency_name", lambda engine: "postgres")

    async def _probe():
        a = await dependency_probes.probe_chat_history_db(app_factory)
        b = await dependency_probes.probe_chat_history_db(app_factory)
        c = await dependency_probes.probe_chat_history_db(app_factory, use_cache=False)
        return a, b, c

    a, b, c = asyncio.run(_probe())
    assert a["status"] == b["status"] == c["status"] == "ok"
    assert len(calls) == 2  # the second call was served from cache; the forced one probed


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


def test_dependency_known_only_from_errors_does_not_pin_degraded(client, monkeypatch):
    """Top-level status comes from live probes; a past error alone is listed, not a verdict."""
    coordination_metrics().dependency_failed("postgres", ConnectionError("x"), op="conversation_save")

    async def _all_ok(factory, use_cache=True):
        return {"redis": {"status": "ok"}}

    import atlas.routes.coordination_routes as routes

    monkeypatch.setattr(routes, "probe_all", _all_ok)
    body = client.get("/admin/coordination", headers=_admin()).json()
    assert body["status"] == "ok"
    assert body["dependencies"]["postgres"]["state"]["status"] == "unavailable"


def test_shutdown_failure_does_not_skip_cleanup(monkeypatch):
    """A failing interrupt step is logged; MCP cleanup and shared-infra teardown still run."""
    calls = []

    async def _boom(*args, **kwargs):
        raise RuntimeError("interrupt failed")

    async def _stop():
        calls.append("stop_shared_infrastructure")

    import atlas.core.security_config as security_config

    # This module runs with DEBUG_MODE on (admin fixture); startup would refuse it.
    monkeypatch.setattr(security_config, "validate_debug_configuration", lambda *a, **k: None)
    monkeypatch.setattr(main, "interrupt_runs_for_shutdown", _boom)
    monkeypatch.setattr(app_factory, "stop_shared_infrastructure", _stop)
    with TestClient(main.app):
        pass
    assert calls == ["stop_shared_infrastructure"]


def test_probe_stops_starting_threads_when_all_are_parked(monkeypatch, fake_engine):
    """A black-holed database parks at most DB_PROBE_MAX_THREADS probe threads."""
    import threading

    release = threading.Event()
    calls = []

    def _hang(engine):
        calls.append(1)
        release.wait(5)

    monkeypatch.setattr(app_factory, "conversation_repository", object())
    monkeypatch.setattr(dependency_probes, "_select_one", _hang)
    monkeypatch.setattr(dependency_probes, "database_dependency_name", lambda engine: "postgres")
    monkeypatch.setattr(dependency_probes, "DB_PROBE_STALE_SECONDS", 0.0)  # every probe counts as stale

    async def _probe_many():
        return [
            await dependency_probes.probe_chat_history_db(app_factory, timeout=0.1, use_cache=False)
            for _ in range(5)
        ]

    try:
        results = asyncio.run(_probe_many())
    finally:
        release.set()
    assert all(r["status"] == "unavailable" for r in results)
    assert len(calls) == dependency_probes.DB_PROBE_MAX_THREADS
    assert results[-1]["error"] == "ProbeSaturated"


def test_budget_timeout_does_not_mark_a_slow_database_down(monkeypatch, fake_engine):
    """A caller's short budget expiring is not an outage; the probe's own result decides."""
    import time as _time

    monkeypatch.setattr(app_factory, "conversation_repository", object())
    monkeypatch.setattr(dependency_probes, "_select_one", lambda engine: _time.sleep(0.4))
    monkeypatch.setattr(dependency_probes, "database_dependency_name", lambda engine: "postgres")

    async def _scenario():
        first = await dependency_probes.probe_chat_history_db(app_factory, timeout=0.1)
        down_after_budget = coordination_metrics().dependencies().get("postgres", {}).get("status")
        await asyncio.sleep(0.6)  # the probe finishes within its own bound
        return first, down_after_budget

    first, state_after_budget = asyncio.run(_scenario())
    assert first["status"] == "unavailable"
    assert state_after_budget != "unavailable"
    assert coordination_metrics().dependencies()["postgres"]["status"] == "ok"
    assert dependency_probes._db_state["last"] is None  # the budget miss was not cached
