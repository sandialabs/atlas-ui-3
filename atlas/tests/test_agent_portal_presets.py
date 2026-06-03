"""Tests for the agent-portal preset library (store + HTTP CRUD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.modules.agent_portal.presets_store import (
    Preset,
    PresetNotFoundError,
    PresetStore,
)

# ---------------------------------------------------------------------------
# Store-level tests — pure JSON, no HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> PresetStore:
    return PresetStore(path=tmp_path / "presets.json")


def test_create_and_list(store: PresetStore):
    a = store.create({"name": "a", "command": "ls"}, user_email="alice@example.com")
    b = store.create({"name": "b", "command": "pwd"}, user_email="alice@example.com")

    listed = store.list_for_user("alice@example.com")
    assert {p.id for p in listed} == {a.id, b.id}
    assert all(isinstance(p, Preset) for p in listed)


def test_list_is_filtered_by_user(store: PresetStore):
    store.create({"name": "alice-1", "command": "ls"}, user_email="alice@example.com")
    store.create({"name": "bob-1", "command": "pwd"}, user_email="bob@example.com")

    alice = store.list_for_user("alice@example.com")
    bob = store.list_for_user("bob@example.com")

    assert [p.name for p in alice] == ["alice-1"]
    assert [p.name for p in bob] == ["bob-1"]


def test_get_cross_user_raises_not_found(store: PresetStore):
    p = store.create({"name": "x", "command": "ls"}, user_email="alice@example.com")
    # bob cannot read alice's preset by id — the store should treat it as absent.
    with pytest.raises(PresetNotFoundError):
        store.get(p.id, user_email="bob@example.com")


def test_update_applies_partial_fields(store: PresetStore):
    p = store.create(
        {"name": "orig", "command": "ls", "sandbox_mode": "off"},
        user_email="alice@example.com",
    )
    updated = store.update(
        p.id, {"name": "renamed", "sandbox_mode": "strict"}, user_email="alice@example.com"
    )
    assert updated.id == p.id
    assert updated.name == "renamed"
    assert updated.sandbox_mode == "strict"
    assert updated.command == "ls"  # unchanged
    assert updated.updated_at >= p.updated_at


def test_update_rejects_immutable_fields(store: PresetStore):
    p = store.create({"name": "a", "command": "ls"}, user_email="alice@example.com")
    original_owner = p.user_email
    original_id = p.id
    original_created = p.created_at

    updated = store.update(
        p.id,
        {"id": "pst_hijack", "user_email": "bob@example.com", "created_at": 0.0},
        user_email="alice@example.com",
    )
    assert updated.id == original_id
    assert updated.user_email == original_owner
    assert updated.created_at == original_created


def test_update_cross_user_raises_not_found(store: PresetStore):
    p = store.create({"name": "a", "command": "ls"}, user_email="alice@example.com")
    with pytest.raises(PresetNotFoundError):
        store.update(p.id, {"name": "hacked"}, user_email="bob@example.com")


def test_delete_and_missing(store: PresetStore):
    p = store.create({"name": "a", "command": "ls"}, user_email="alice@example.com")
    store.delete(p.id, user_email="alice@example.com")
    assert store.list_for_user("alice@example.com") == []
    with pytest.raises(PresetNotFoundError):
        store.delete(p.id, user_email="alice@example.com")


def test_delete_cross_user_raises_not_found(store: PresetStore):
    p = store.create({"name": "a", "command": "ls"}, user_email="alice@example.com")
    with pytest.raises(PresetNotFoundError):
        store.delete(p.id, user_email="bob@example.com")
    # Alice's preset still exists
    assert len(store.list_for_user("alice@example.com")) == 1


def test_persistence_across_instances(tmp_path: Path):
    path = tmp_path / "presets.json"
    s1 = PresetStore(path=path)
    s1.create({"name": "a", "command": "ls"}, user_email="alice@example.com")

    s2 = PresetStore(path=path)
    listed = s2.list_for_user("alice@example.com")
    assert [p.name for p in listed] == ["a"]


def test_file_shape_on_disk(tmp_path: Path):
    path = tmp_path / "presets.json"
    s = PresetStore(path=path)
    s.create({"name": "a", "command": "ls"}, user_email="alice@example.com")

    with open(path) as f:
        data = json.load(f)
    assert data["schema_version"] == 1
    assert isinstance(data["presets"], list)
    assert data["presets"][0]["name"] == "a"


def test_corrupt_file_recovers_as_empty(tmp_path: Path):
    path = tmp_path / "presets.json"
    path.write_text("not valid json {")
    s = PresetStore(path=path)
    # list_for_user should not raise; treat as empty.
    assert s.list_for_user("alice@example.com") == []


# ---------------------------------------------------------------------------
# HTTP-level tests — FastAPI TestClient against just the agent-portal router.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    from atlas.core import log_sanitizer as log_san
    from atlas.modules.agent_portal import presets_store as ps_mod
    from atlas.routes import agent_portal_routes as ap_routes

    # Point the store singleton at a temp file
    ps_mod._singleton = PresetStore(path=tmp_path / "presets.json")

    # Stub feature flag + current user
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

    monkeypatch.setattr(
        ap_routes.app_factory, "get_config_manager", lambda: _CM()
    )

    async def _fake_current_user():
        return "alice@example.com"

    # The get_current_user used by routes comes from log_sanitizer. Override
    # the FastAPI dependency on the router.
    app = FastAPI()
    app.include_router(ap_routes.router)
    app.dependency_overrides[log_san.get_current_user] = _fake_current_user

    yield TestClient(app)

    ps_mod._singleton = None


def test_http_create_and_list(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "cla", "command": "claude", "args": ["--help"]},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["name"] == "cla"
    assert created["user_email"] == "alice@example.com"

    res2 = api_client.get("/api/agent-portal/presets")
    assert res2.status_code == 200
    body = res2.json()
    assert [p["id"] for p in body["presets"]] == [created["id"]]


def test_http_create_rejects_invalid_sandbox_mode(api_client: TestClient):
    res = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "bad", "command": "ls", "sandbox_mode": "nonsense"},
    )
    assert res.status_code == 400


def test_http_update_partial(api_client: TestClient):
    c = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "a", "command": "ls"},
    ).json()
    res = api_client.patch(
        f"/api/agent-portal/presets/{c['id']}",
        json={"name": "renamed"},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["name"] == "renamed"
    assert updated["command"] == "ls"


def test_http_get_missing_returns_404(api_client: TestClient):
    res = api_client.get("/api/agent-portal/presets/pst_does_not_exist")
    assert res.status_code == 404


def test_http_delete(api_client: TestClient):
    c = api_client.post(
        "/api/agent-portal/presets",
        json={"name": "a", "command": "ls"},
    ).json()
    res = api_client.delete(f"/api/agent-portal/presets/{c['id']}")
    assert res.status_code == 204
    res2 = api_client.get(f"/api/agent-portal/presets/{c['id']}")
    assert res2.status_code == 404


# ---------------------------------------------------------------------------
# Built-in preset templates (static endpoint, no per-user state)
# ---------------------------------------------------------------------------


def test_http_preset_templates_shipped(api_client: TestClient):
    """The built-in templates endpoint ships the three common agent CLIs
    with sandbox defaults that actually work.

    Pin both the command names and the writable-paths so a future refactor
    that drops a tool or removes a critical path triggers a test failure
    instead of a silent regression to the original "nothing happens" UX.
    """
    res = api_client.get("/api/agent-portal/preset-templates")
    assert res.status_code == 200
    body = res.json()
    templates = body["templates"]

    by_cmd = {t["command"]: t for t in templates}
    assert {"claude", "cline", "codex"} <= by_cmd.keys(), (
        f"templates must include claude, cline, codex; got {list(by_cmd)}"
    )

    # Claude is the load-bearing case from the bug report: workspace-write
    # sandbox + ~/.claude (dir) + ~/.claude.json (file) must all be there.
    claude = by_cmd["claude"]
    assert claude["sandbox_mode"] == "workspace-write"
    assert claude["use_pty"] is True
    paths = claude["extra_writable_paths"]
    assert "~/.claude" in paths
    assert "~/.claude.json" in paths, (
        "the single-file rule for ~/.claude.json is required — without it "
        "claude hangs on its first write to the state file"
    )

    # Cline & codex must at least have their own data dirs writable.
    assert any("cline" in p for p in by_cmd["cline"]["extra_writable_paths"])
    assert any("codex" in p for p in by_cmd["codex"]["extra_writable_paths"])
