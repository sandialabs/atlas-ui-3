"""Phase 5 tests — synchronize-input broadcast to a group.

Walks the broadcast surface (manager helper + HTTP endpoint) without
spinning up real PTYs — broadcast_input on a non-PTY process is a
no-op, so we use that path to confirm the budget enforcement and
audit event behavior. End-to-end PTY broadcast is exercised via the
existing test_process_manager.py PTY tests + this group fan-out.
"""

from __future__ import annotations

import base64
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
from atlas.modules.process_manager.manager import ProcessManager


@pytest.mark.asyncio
async def test_broadcast_input_skips_non_pty_members():
    """The broadcast helper only writes to PTY-backed members. Non-PTY
    members are silently ignored so a mixed group can still broadcast
    without raising."""
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-1",
    )
    # No PTY — should be skipped by broadcast.
    recipients = pm.broadcast_input("grp-1", b"hello\n")
    assert recipients == []  # neither member is PTY-backed
    await pm.cancel(a.id)


@pytest.mark.asyncio
async def test_broadcast_input_returns_only_running_pty_members():
    pm = ProcessManager()
    # Start a PTY-backed long-runner so broadcast has a recipient.
    a = await pm.launch(
        command="/usr/bin/cat", user_email="alice@x",
        group_id="grp-1", use_pty=True,
    )
    recipients = pm.broadcast_input("grp-1", b"x")
    assert recipients == [a.id]
    await pm.cancel(a.id)


# ---------------------------------------------------------------------------
# HTTP — broadcast endpoint records an audit event with N recipients.
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

    client = TestClient(app)
    client._audit_path = audit_path
    yield client

    ps_mod._singleton = None
    pm_mod._singleton = None
    audit_mod.reset_path_cache_for_tests()


def test_http_broadcast_unknown_group_404(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/groups/nope/broadcast",
        json={"data_base64": base64.b64encode(b"hi").decode()},
    )
    assert res.status_code == 404


def test_http_broadcast_records_audit_event(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "syncme"},
    ).json()
    res = api_client.post(
        f"/api/agent-portal/groups/{grp['id']}/broadcast",
        json={"data_base64": base64.b64encode(b"pwd\n").decode()},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["recipients"] == []  # no PTY-backed members yet
    assert body["bytes"] == 4
    audit = api_client.get("/api/agent-portal/audit").json()["events"]
    assert any(e["event"] == "sync_input" for e in audit)


def test_http_broadcast_invalid_base64_400(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "syncme"},
    ).json()
    res = api_client.post(
        f"/api/agent-portal/groups/{grp['id']}/broadcast",
        json={"data_base64": "not!!!base64"},
    )
    assert res.status_code == 400
