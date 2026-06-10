"""Tests for the Agent Portal server-side state store (PortalStore + HTTP).

Covers:

* PortalStore CRUD per collection (history, configs, layout, groups,
  bundles, audit) with strict per-user filtering on every path.
* HTTP endpoints under ``/api/agent-portal/state/*`` and ``/groups/*``,
  ``/bundles/*``, ``/audit``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from atlas.modules.agent_portal import portal_store as ps_mod
from atlas.modules.agent_portal.models import Base
from atlas.modules.agent_portal.portal_store import PortalStore

# ---------------------------------------------------------------------------
# Store-level tests — DuckDB-backed PortalStore against a temp db file.
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> PortalStore:
    db_url = f"duckdb:///{tmp_path / 'portal.db'}"
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return PortalStore(factory)


def test_launch_history_upsert_and_dedup(store: PortalStore):
    e1 = store.upsert_launch_history(
        "alice@x", {"command": "ls", "argsString": "-l", "cwd": "/tmp", "sandboxMode": "off"}
    )
    e2 = store.upsert_launch_history(
        "alice@x", {"command": "ls", "argsString": "-l", "cwd": "/tmp", "sandboxMode": "off"}
    )
    listed = store.list_launch_history("alice@x")
    # Same identity → de-duped to a single row.
    assert len(listed) == 1
    assert listed[0]["command"] == "ls"
    assert e1["lastUsed"] <= e2["lastUsed"]


def test_launch_history_per_user(store: PortalStore):
    store.upsert_launch_history("alice@x", {"command": "ls"})
    store.upsert_launch_history("bob@x", {"command": "pwd"})
    assert [e["command"] for e in store.list_launch_history("alice@x")] == ["ls"]
    assert [e["command"] for e in store.list_launch_history("bob@x")] == ["pwd"]


def test_launch_history_replace_preserves_order(store: PortalStore):
    payload = [
        {"command": "first"},
        {"command": "second"},
        {"command": "third"},
    ]
    out = store.replace_launch_history("alice@x", payload)
    assert [e["command"] for e in out] == ["first", "second", "third"]


def test_launch_history_delete(store: PortalStore):
    store.upsert_launch_history("alice@x", {"command": "ls"})
    # Recompute the dedup key the store uses
    from atlas.modules.agent_portal.portal_store import _make_dedup_key
    key = _make_dedup_key({"command": "ls"})
    assert store.delete_launch_history_entry("alice@x", key) is True
    assert store.list_launch_history("alice@x") == []
    # Idempotent / cross-user safe.
    assert store.delete_launch_history_entry("alice@x", key) is False


def test_launch_configs_replace_and_per_user(store: PortalStore):
    store.replace_launch_configs(
        "alice@x", [{"name": "a", "command": "ls"}, {"name": "b", "command": "pwd"}]
    )
    store.replace_launch_configs("bob@x", [{"name": "c", "command": "echo"}])
    alice = store.list_launch_configs("alice@x")
    bob = store.list_launch_configs("bob@x")
    assert {c["name"] for c in alice} == {"a", "b"}
    assert {c["name"] for c in bob} == {"c"}
    # Replace with empty list clears.
    store.replace_launch_configs("alice@x", [])
    assert store.list_launch_configs("alice@x") == []


def test_layout_get_put(store: PortalStore):
    assert store.get_layout("alice@x") is None
    saved = store.put_layout("alice@x", {"mode": "2x2", "slots": [None, None, None, None]})
    assert saved["mode"] == "2x2"
    fetched = store.get_layout("alice@x")
    assert fetched is not None
    assert fetched["mode"] == "2x2"
    # Per-user.
    assert store.get_layout("bob@x") is None


def test_layout_put_overwrites(store: PortalStore):
    store.put_layout("alice@x", {"mode": "single"})
    store.put_layout("alice@x", {"mode": "3x2"})
    assert store.get_layout("alice@x") == {"mode": "3x2"}


def test_groups_crud_per_owner(store: PortalStore):
    g = store.create_group("alice@x", {"name": "demo", "max_panes": 4})
    assert g["name"] == "demo"
    assert g["max_panes"] == 4

    # bob cannot see alice's group
    assert store.get_group("bob@x", g["id"]) is None
    assert store.list_groups("bob@x") == []

    # update applies only to owner
    updated = store.update_group("alice@x", g["id"], {"max_panes": 6})
    assert updated["max_panes"] == 6
    assert store.update_group("bob@x", g["id"], {"max_panes": 99}) is None

    # delete is owner-scoped
    assert store.delete_group("bob@x", g["id"]) is False
    assert store.delete_group("alice@x", g["id"]) is True
    assert store.get_group("alice@x", g["id"]) is None


def test_groups_create_requires_name(store: PortalStore):
    with pytest.raises(ValueError):
        store.create_group("alice@x", {"name": "  "})


def test_bundles_crud(store: PortalStore):
    b = store.create_bundle(
        "alice@x",
        {
            "name": "trio",
            "group_template": {"max_panes": 3},
            "members": [{"preset_id": "pst_1"}, {"preset_id": "pst_2"}],
        },
    )
    assert b["name"] == "trio"
    assert b["group_template"]["max_panes"] == 3
    assert len(b["members"]) == 2
    # cross-owner read returns None
    assert store.get_bundle("bob@x", b["id"]) is None
    # delete is owner-scoped
    assert store.delete_bundle("bob@x", b["id"]) is False
    assert store.delete_bundle("alice@x", b["id"]) is True


def test_audit_append_and_list(store: PortalStore):
    store.append_audit("alice@x", "launch", process_id="proc-1", detail={"cmd": "ls"})
    store.append_audit("alice@x", "cancel", process_id="proc-1")
    store.append_audit("bob@x", "launch", process_id="proc-2")
    alice = store.list_audit("alice@x")
    bob = store.list_audit("bob@x")
    assert [e["event"] for e in alice] == ["cancel", "launch"]  # newest first
    assert [e["event"] for e in bob] == ["launch"]
    assert alice[1]["detail"] == {"cmd": "ls"}
    assert alice[0]["executor"] == "local"


# ---------------------------------------------------------------------------
# HTTP-level tests — FastAPI TestClient against the agent-portal router.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    from atlas.core import log_sanitizer as log_san
    from atlas.routes import agent_portal_routes as ap_routes

    # Build a fresh PortalStore against a temp DuckDB file and pin it as
    # the singleton so the routes pick it up.
    db_url = f"duckdb:///{tmp_path / 'portal.db'}"
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ps_mod._singleton = PortalStore(factory)

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


def test_http_layout_round_trip(api_client: TestClient):
    res = api_client.get("/api/agent-portal/state/layout")
    assert res.status_code == 200
    assert res.json() == {"layout": {}}

    res2 = api_client.put(
        "/api/agent-portal/state/layout",
        json={"layout": {"mode": "2x2", "slots": ["pid-1", None, None, None]}},
    )
    assert res2.status_code == 200
    assert res2.json()["layout"]["mode"] == "2x2"

    res3 = api_client.get("/api/agent-portal/state/layout")
    assert res3.json()["layout"]["mode"] == "2x2"


def test_http_launch_history_upsert_and_list(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/state/launch-history",
        json={"entry": {"command": "ls", "argsString": "-l", "cwd": "/tmp"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["entry"]["command"] == "ls"
    assert len(body["entries"]) == 1

    # Replace with a fresh list
    res2 = api_client.put(
        "/api/agent-portal/state/launch-history",
        json={"entries": [{"command": "a"}, {"command": "b"}]},
    )
    assert res2.status_code == 200
    assert [e["command"] for e in res2.json()["entries"]] == ["a", "b"]


def test_http_launch_configs_replace(api_client: TestClient):
    res = api_client.put(
        "/api/agent-portal/state/launch-configs",
        json={"configs": [{"name": "c1", "command": "ls"}, {"name": "c2", "command": "pwd"}]},
    )
    assert res.status_code == 200
    assert {c["name"] for c in res.json()["configs"]} == {"c1", "c2"}

    res2 = api_client.get("/api/agent-portal/state/launch-configs")
    assert res2.status_code == 200
    assert {c["name"] for c in res2.json()["configs"]} == {"c1", "c2"}


def test_http_groups_crud(api_client: TestClient):
    create = api_client.post(
        "/api/agent-portal/groups",
        json={"name": "team-a", "max_panes": 4},
    )
    assert create.status_code == 201
    g = create.json()
    assert g["owner"] == "alice@example.com"
    assert g["max_panes"] == 4

    listing = api_client.get("/api/agent-portal/groups")
    assert listing.status_code == 200
    assert [x["id"] for x in listing.json()["groups"]] == [g["id"]]

    patch = api_client.patch(
        f"/api/agent-portal/groups/{g['id']}", json={"max_panes": 6}
    )
    assert patch.status_code == 200
    assert patch.json()["max_panes"] == 6

    delete = api_client.delete(f"/api/agent-portal/groups/{g['id']}")
    assert delete.status_code == 204

    missing = api_client.get(f"/api/agent-portal/groups/{g['id']}")
    assert missing.status_code == 404


def test_http_bundles_crud(api_client: TestClient):
    create = api_client.post(
        "/api/agent-portal/bundles",
        json={
            "name": "trio",
            "group_template": {"max_panes": 3},
            "members": [{"preset_id": "pst_1"}],
        },
    )
    assert create.status_code == 201
    b = create.json()
    assert b["name"] == "trio"
    assert b["members"] == [{"preset_id": "pst_1"}]

    delete = api_client.delete(f"/api/agent-portal/bundles/{b['id']}")
    assert delete.status_code == 204
    assert api_client.get(f"/api/agent-portal/bundles/{b['id']}").status_code == 404


def test_http_audit_list(api_client: TestClient):
    # Empty by default
    res = api_client.get("/api/agent-portal/audit")
    assert res.status_code == 200
    assert res.json()["events"] == []
