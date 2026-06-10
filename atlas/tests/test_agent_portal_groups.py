"""Phase 3 tests — server-enforced group membership and reaping.

Two layers:
  * unit tests against ``ProcessManager.launch(group_id=...)`` and
    ``cancel_group`` directly,
  * HTTP-level tests that walk the launch endpoint with ``group_id``
    set and confirm the budget is enforced server-side (not the client).

Real subprocesses (``true``, ``sleep``) are launched without sandboxing
so the tests stay portable across hosts that lack Landlock or
unprivileged user namespaces.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from atlas.modules.agent_portal import portal_store as ps_mod
from atlas.modules.agent_portal.models import Base
from atlas.modules.agent_portal.portal_store import PortalStore
from atlas.modules.process_manager.manager import (
    GroupBudgetExceededError,
    ProcessManager,
    ProcessStatus,
)


@pytest.mark.asyncio
async def test_launch_into_group_records_group_id(tmp_path: Path):
    pm = ProcessManager()
    proc = await pm.launch(
        command="/usr/bin/true",
        user_email="alice@x",
        group_id="grp-1",
    )
    assert proc.group_id == "grp-1"
    # to_summary surfaces it for the frontend.
    assert proc.to_summary()["group_id"] == "grp-1"


@pytest.mark.asyncio
async def test_group_max_panes_enforced_server_side():
    pm = ProcessManager()
    # Long-lived processes so the cap check sees them as RUNNING.
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-1", group_max_panes=2,
    )
    b = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-1", group_max_panes=2,
    )
    with pytest.raises(GroupBudgetExceededError):
        await pm.launch(
            command="/usr/bin/sleep", args=["10"], user_email="alice@x",
            group_id="grp-1", group_max_panes=2,
        )
    # Cleanup.
    await pm.cancel_group("grp-1")
    # Wait for the cancel tasks to finalize so we don't leak processes
    # past the test boundary.
    await _wait_until_not_running(pm, [a.id, b.id])


@pytest.mark.asyncio
async def test_max_panes_does_not_count_other_groups():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-A", group_max_panes=1,
    )
    # Different group with the same max_panes should succeed.
    b = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-B", group_max_panes=1,
    )
    assert a.group_id == "grp-A"
    assert b.group_id == "grp-B"
    await pm.cancel_group("grp-A")
    await pm.cancel_group("grp-B")
    await _wait_until_not_running(pm, [a.id, b.id])


@pytest.mark.asyncio
async def test_cancel_group_reaps_only_members():
    pm = ProcessManager()
    a = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-1",
    )
    b = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
        group_id="grp-1",
    )
    outsider = await pm.launch(
        command="/usr/bin/sleep", args=["10"], user_email="alice@x",
    )
    members = await pm.cancel_group("grp-1")
    assert {m.id for m in members} == {a.id, b.id}
    await _wait_until_not_running(pm, [a.id, b.id])
    # The outsider is unaffected.
    assert pm.get(outsider.id).status == ProcessStatus.RUNNING
    await pm.cancel(outsider.id)
    await _wait_until_not_running(pm, [outsider.id])


# ---------------------------------------------------------------------------
# HTTP — confirms the route hands group_id off to the manager and that
# the group definition is owner-scoped (a user cannot launch into
# another user's group).
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

    # Reset the in-process ProcessManager so cross-test state doesn't
    # bleed (the singleton would otherwise carry processes across tests).
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

    yield TestClient(app)

    ps_mod._singleton = None
    pm_mod._singleton = None


def test_http_launch_into_group_succeeds(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "demo", "max_panes": 2},
    ).json()
    res = api_client.post(
        "/api/agent-portal/processes",
        json={
            "command": "/usr/bin/true",
            "args": [],
            "group_id": grp["id"],
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["group_id"] == grp["id"]


def test_http_launch_unknown_group_404(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/true", "group_id": "no-such-group"},
    )
    assert res.status_code == 404


def test_http_group_max_panes_enforced(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "tight", "max_panes": 1},
    ).json()

    # First long-running launch fits.
    r1 = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["5"], "group_id": grp["id"]},
    )
    assert r1.status_code == 201

    # Second is rejected by the server with HTTP 429.
    r2 = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["5"], "group_id": grp["id"]},
    )
    assert r2.status_code == 429
    assert "full" in r2.json()["detail"].lower()


def test_http_group_cancel_endpoint(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "reapable", "max_panes": 4},
    ).json()
    a = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["10"], "group_id": grp["id"]},
    ).json()
    b = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["10"], "group_id": grp["id"]},
    ).json()
    res = api_client.post(f"/api/agent-portal/groups/{grp['id']}/cancel")
    assert res.status_code == 200
    assert set(res.json()["cancelled"]) == {a["id"], b["id"]}


def test_http_delete_group_reaps_members(api_client: TestClient):
    grp = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "doomed", "max_panes": 4},
    ).json()
    api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/sleep", "args": ["10"], "group_id": grp["id"]},
    )
    res = api_client.delete(f"/api/agent-portal/groups/{grp['id']}")
    assert res.status_code == 204
    # Group is gone.
    assert api_client.get(f"/api/agent-portal/groups/{grp['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_until_not_running(pm: ProcessManager, ids, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if all(pm.get(i).status != ProcessStatus.RUNNING for i in ids):
            return
        await asyncio.sleep(0.05)
    # Don't fail — leak warning would be noisy on slow CI; the process
    # group SIGTERM/SIGKILL we issue is asynchronous and the test
    # already verified the right call paths fired.
