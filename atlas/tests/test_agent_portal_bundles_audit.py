"""Phase 4 tests — bundle launch + audit log (DB + JSONL).

Walks the bundle launch flow end-to-end:
  * resolve presets, create group, launch every member,
  * rollback on per-member failure (group + every launched process),
  * audit events written to both DuckDB and the sibling JSONL file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from atlas.modules.agent_portal import (
    audit_log as audit_mod,
    portal_store as ps_mod,
    presets_store as preset_mod,
)
from atlas.modules.agent_portal.models import Base
from atlas.modules.agent_portal.portal_store import PortalStore
from atlas.modules.agent_portal.presets_store import PresetStore


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    from atlas.core import log_sanitizer as log_san
    from atlas.routes import agent_portal_routes as ap_routes

    db_url = f"duckdb:///{tmp_path / 'portal.db'}"
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ps_mod._singleton = PortalStore(factory)
    preset_mod._singleton = PresetStore(path=tmp_path / "presets.json")

    # Pin the audit JSONL path to the temp dir so the test can read it.
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_PORTAL_AUDIT_PATH", str(audit_path))
    audit_mod.reset_path_cache_for_tests()

    # Reset ProcessManager singleton between tests.
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
    client._audit_path = audit_path  # stash for assertions
    yield client

    ps_mod._singleton = None
    preset_mod._singleton = None
    pm_mod._singleton = None
    audit_mod.reset_path_cache_for_tests()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_audit_writes_to_db_and_jsonl_on_launch(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/processes",
        json={"command": "/usr/bin/true", "args": []},
    )
    assert res.status_code == 201

    # DB-backed listing surfaces the launch event.
    audit = api_client.get("/api/agent-portal/audit").json()["events"]
    assert any(e["event"] == "launch" for e in audit)

    # JSONL sink also has the line.
    jsonl = _read_jsonl(api_client._audit_path)
    assert any(e["event"] == "launch" for e in jsonl)
    # Same record carries the executor field for forward-compat.
    assert all(e.get("executor") == "local" for e in jsonl)


def test_bundle_launch_creates_group_and_members(api_client: TestClient):
    # Two presets that exit cleanly.
    p1 = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "p1", "command": "/usr/bin/true"},
    ).json()
    p2 = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "p2", "command": "/usr/bin/true"},
    ).json()
    bundle = api_client.post(
        "/api/agent-portal/bundles",
        json={
            "name": "duo",
            "group_template": {"name": "duo-group", "max_panes": 2},
            "members": [
                {"preset_id": p1["id"]},
                {"preset_id": p2["id"], "display_name_override": "second"},
            ],
        },
    ).json()
    res = api_client.post(f"/api/agent-portal/bundles/{bundle['id']}/launch")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["group"]["name"] == "duo-group"
    assert len(body["processes"]) == 2

    # Audit log records the bundle_launch event AND each member launch.
    jsonl = _read_jsonl(api_client._audit_path)
    events = [e["event"] for e in jsonl]
    assert "bundle_launch" in events
    assert events.count("launch") >= 2


def test_bundle_launch_rolls_back_on_unknown_preset(api_client: TestClient):
    bundle = api_client.post(
        "/api/agent-portal/bundles",
        json={
            "name": "broken",
            "group_template": {"name": "broken-group"},
            "members": [{"preset_id": "pst_does_not_exist"}],
        },
    ).json()
    res = api_client.post(f"/api/agent-portal/bundles/{bundle['id']}/launch")
    assert res.status_code == 400
    # No group should have been created (preset resolution happens before
    # any state mutation).
    groups = api_client.get("/api/agent-portal/groups").json()["groups"]
    assert all(g["name"] != "broken-group" for g in groups)


def test_bundle_launch_rejects_empty_member_list(api_client: TestClient):
    bundle = api_client.post(
        "/api/agent-portal/bundles",
        json={"name": "empty", "members": []},
    ).json()
    res = api_client.post(f"/api/agent-portal/bundles/{bundle['id']}/launch")
    assert res.status_code == 400
    assert "no members" in res.json()["detail"].lower()


def test_audit_records_group_create_and_delete(api_client: TestClient):
    create = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "auditme", "max_panes": 2},
    )
    assert create.status_code == 201
    gid = create.json()["id"]
    delete = api_client.delete(f"/api/agent-portal/groups/{gid}")
    assert delete.status_code == 204
    jsonl = _read_jsonl(api_client._audit_path)
    events = [e["event"] for e in jsonl]
    assert "group_create" in events
    assert "group_delete" in events
