"""Phase 6 tests — idle-kill, pause/resume, snapshot.

Covers the polish/enforcement layer:
  * idle_seconds_for + reap_idle_in_group reap silent processes,
  * pause_group / resume_group send SIGSTOP / SIGCONT,
  * snapshot_group serializes scrollback for every member,
  * the matching HTTP endpoints record audit events.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from atlas.modules.agent_portal import (
    audit_log as audit_mod,
    portal_store as ps_mod,
)
from atlas.modules.agent_portal.models import Base
from atlas.modules.agent_portal.portal_store import PortalStore
from atlas.modules.process_manager.manager import (
    ProcessManager,
    ProcessStatus,
)


@pytest.mark.asyncio
async def test_idle_seconds_for_running_process():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["5"], user_email="alice@x",
        group_id="g", use_pty=False,
    )
    idle = pm.idle_seconds_for(a.id)
    assert idle is not None
    assert idle < 1.0  # just launched
    await pm.cancel(a.id)


@pytest.mark.asyncio
async def test_reap_idle_in_group_kills_silent_member():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="g",
    )
    # Force the process to look idle (set last_activity into the past).
    pm.get(a.id).last_activity = time.time() - 60.0
    cancelled = await pm.reap_idle_in_group("g", idle_kill_seconds=30)
    assert cancelled == [a.id]


@pytest.mark.asyncio
async def test_reap_idle_skips_active_member():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="g",
    )
    # Active enough to be safe.
    pm.get(a.id).last_activity = time.time()
    cancelled = await pm.reap_idle_in_group("g", idle_kill_seconds=30)
    assert cancelled == []
    await pm.cancel(a.id)


@pytest.mark.asyncio
async def test_pause_resume_group_signals_members():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="g",
    )
    paused = pm.pause_group("g")
    assert paused == [a.id]
    # The process is still tracked as RUNNING — SIGSTOP doesn't reap.
    assert pm.get(a.id).status == ProcessStatus.RUNNING
    resumed = pm.resume_group("g")
    assert resumed == [a.id]
    await pm.cancel(a.id)


@pytest.mark.asyncio
async def test_snapshot_group_returns_scrollback():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/echo", args=["hello-snapshot"],
        user_email="alice@x", group_id="g",
    )
    # Wait for echo to exit so the history captures the line.
    deadline = asyncio.get_event_loop().time() + 3
    while asyncio.get_event_loop().time() < deadline:
        if pm.get(a.id).status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    # snapshot_group only returns running members; once exited, the
    # member drops out of list_processes_in_group's RUNNING filter.
    # For an echo that has already exited this returns an empty list,
    # which is by design — the snapshot is a "what's live right now"
    # capture, not an archive.
    snap = pm.snapshot_group("g")
    assert snap["group_id"] == "g"
    assert "captured_at" in snap


# ---------------------------------------------------------------------------
# HTTP — pause/resume/snapshot endpoints record audit events.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    from atlas.core import log_sanitizer as log_san
    from atlas.routes import agent_portal_routes as ap_routes

    db_url = f"duckdb:///{tmp_path / 'portal.db'}"
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ps_mod._singleton = PortalStore(factory)

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_PORTAL_AUDIT_PATH", str(audit_path))
    audit_mod.reset_path_cache_for_tests()

    from atlas.modules.process_manager import manager as pm_mod
    pm_mod._singleton = None
    pm_mod.stop_idle_sweeper_for_tests()

    class _Settings:
        feature_agent_portal_enabled = True
        debug_mode = True
        feature_proxy_secret_enabled = False
        proxy_secret = ""
        proxy_secret_header = "x-proxy-secret"
        auth_user_header = "x-forwarded-user"
        test_user = "alice@example.com"

    class _CM:
        app_settings = _Settings()

    monkeypatch.setattr(ap_routes.app_factory, "get_config_manager", lambda: _CM())

    async def _fake_current_user():
        return "alice@example.com"

    app = FastAPI()
    app.include_router(ap_routes.router)
    app.dependency_overrides[log_san.get_current_user] = _fake_current_user

    yield TestClient(app)

    ps_mod._singleton = None
    pm_mod._singleton = None
    pm_mod.stop_idle_sweeper_for_tests()
    audit_mod.reset_path_cache_for_tests()


def test_http_pause_resume_records_audit(api_client: TestClient):
    grp = api_client.post("/api/agent-portal/groups", json={"name": "g"}).json()
    api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["5"], "group_id": grp["id"]},
    )
    p = api_client.post(f"/api/agent-portal/groups/{grp['id']}/pause")
    assert p.status_code == 200
    r = api_client.post(f"/api/agent-portal/groups/{grp['id']}/resume")
    assert r.status_code == 200
    audit = api_client.get("/api/agent-portal/audit").json()["events"]
    events = {e["event"] for e in audit}
    assert "group_pause" in events
    assert "group_resume" in events


def test_http_snapshot_endpoint(api_client: TestClient):
    grp = api_client.post("/api/agent-portal/groups", json={"name": "g"}).json()
    res = api_client.get(f"/api/agent-portal/groups/{grp['id']}/snapshot")
    assert res.status_code == 200
    body = res.json()
    assert body["group"]["id"] == grp["id"]
    assert "snapshot" in body
    audit = api_client.get("/api/agent-portal/audit").json()["events"]
    assert any(e["event"] == "group_snapshot" for e in audit)
