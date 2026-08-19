"""Tests for the per-user workspace switcher.

Covers repository CRUD, config normalization, user isolation (including email
case normalization), and the REST layer's feature gate. Uses a temporary DuckDB
database for fast, isolated tests.
"""

import pytest

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.workspace_repository import (
    MAX_SELECTION_ITEMS,
    normalize_config,
)


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import (
        WorkspaceRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'test_workspaces.db'}")
    return WorkspaceRepository(get_session_factory())


def _config(**overrides):
    base = {
        "active_prompt_key": "userprompt:abc",
        "selected_tools": ["files_read"],
        "selected_prompts": [],
        "selected_data_sources": ["corpus-a"],
        "rag_enabled": True,
    }
    base.update(overrides)
    return base


# --- config normalization -------------------------------------------------


def test_normalize_config_fills_canonical_shape():
    assert normalize_config(None) == {
        "active_prompt_key": None,
        "selected_tools": [],
        "selected_prompts": [],
        "selected_data_sources": [],
        "rag_enabled": False,
    }


def test_normalize_config_drops_unknown_keys_and_bad_types():
    result = normalize_config(
        {
            "active_prompt_key": 42,
            "selected_tools": ["a", 7, None, "a", "  ", " b "],
            "rag_enabled": "yes",
            "sneaky": {"nested": "payload"},
        }
    )
    assert "sneaky" not in result
    assert result["active_prompt_key"] is None  # non-string rejected
    assert result["selected_tools"] == ["a", "b"]  # deduped, trimmed, blanks gone
    assert result["rag_enabled"] is True


def test_normalize_config_caps_selection_count():
    result = normalize_config({"selected_tools": [f"t{i}" for i in range(MAX_SELECTION_ITEMS + 50)]})
    assert len(result["selected_tools"]) == MAX_SELECTION_ITEMS


def test_normalize_config_rejects_overlong_keys():
    result = normalize_config({"selected_data_sources": ["ok", "x" * 5000]})
    assert result["selected_data_sources"] == ["ok"]


# --- repository CRUD ------------------------------------------------------


def test_create_and_list(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    assert created["id"]
    assert created["name"] == "Work"
    assert created["config"]["selected_data_sources"] == ["corpus-a"]
    assert created["config"]["rag_enabled"] is True

    workspaces = repo.list_workspaces("alice@test.com")
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == created["id"]


def test_create_without_config_stores_empty_selections(repo):
    created = repo.create_workspace("alice@test.com", "Blank")
    assert created["config"]["selected_tools"] == []
    assert created["config"]["active_prompt_key"] is None


def test_name_is_trimmed(repo):
    created = repo.create_workspace("alice@test.com", "  Home  ", _config())
    assert created["name"] == "Home"


def test_get_respects_owner(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    assert repo.get_workspace(created["id"], "alice@test.com") is not None
    assert repo.get_workspace(created["id"], "bob@test.com") is None


def test_update_replaces_config_wholesale(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    updated = repo.update_workspace(
        created["id"],
        "alice@test.com",
        name="Work v2",
        config=_config(selected_data_sources=[], rag_enabled=False),
    )
    assert updated["name"] == "Work v2"
    # Clearing a list must actually clear it -- a merge would keep "corpus-a".
    assert updated["config"]["selected_data_sources"] == []
    assert updated["config"]["rag_enabled"] is False


def test_update_partial_keeps_other_fields(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    updated = repo.update_workspace(created["id"], "alice@test.com", name="Renamed")
    assert updated["name"] == "Renamed"
    assert updated["config"]["selected_tools"] == ["files_read"]


def test_update_wrong_owner_returns_none(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    assert repo.update_workspace(created["id"], "bob@test.com", name="Stolen") is None


def test_delete(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    assert repo.delete_workspace(created["id"], "alice@test.com") is True
    assert repo.list_workspaces("alice@test.com") == []


def test_delete_wrong_owner(repo):
    created = repo.create_workspace("alice@test.com", "Work", _config())
    assert repo.delete_workspace(created["id"], "bob@test.com") is False
    assert len(repo.list_workspaces("alice@test.com")) == 1


def test_user_isolation_with_case_normalization(repo):
    repo.create_workspace("Alice@Test.com", "Work", _config())
    repo.create_workspace("bob@test.com", "Bob's", _config())

    alice = repo.list_workspaces("alice@test.com")
    assert len(alice) == 1
    assert alice[0]["name"] == "Work"
    assert len(repo.list_workspaces("bob@test.com")) == 1


def test_unreadable_config_json_does_not_break_listing(repo):
    """A corrupt row degrades to empty selections instead of failing the list."""
    from atlas.modules.chat_history import get_session_factory
    from atlas.modules.chat_history.models import UserWorkspaceRecord

    created = repo.create_workspace("alice@test.com", "Work", _config())
    session = get_session_factory()()
    record = session.query(UserWorkspaceRecord).filter_by(id=created["id"]).first()
    record.config_json = "{not json"
    session.commit()
    session.close()

    workspaces = repo.list_workspaces("alice@test.com")
    assert len(workspaces) == 1
    assert workspaces[0]["config"] == normalize_config(None)


# --- REST layer -----------------------------------------------------------


def _client(monkeypatch, *, enabled=True, repo=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from atlas.core.log_sanitizer import get_current_user
    from atlas.routes import workspace_routes

    monkeypatch.setattr(workspace_routes, "_workspaces_enabled", lambda: enabled)
    monkeypatch.setattr(workspace_routes, "_get_repo", lambda: repo)

    app = FastAPI()
    app.include_router(workspace_routes.router)
    app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
    return TestClient(app)


class _FakeRepo:
    def __init__(self):
        self.created = None

    def list_workspaces(self, user_email):
        return []

    def create_workspace(self, user_email, name, config=None, description=None):
        self.created = {"name": name, "config": config, "description": description}
        return {"id": "1", "name": name, "config": config}

    def update_workspace(self, workspace_id, user_email, name=None, config=None, description=None):
        return {"id": workspace_id, "name": name or "n", "config": config}

    def delete_workspace(self, workspace_id, user_email):
        return workspace_id == "1"


def test_routes_404_when_feature_disabled(monkeypatch):
    """The workspace API is hidden when the feature flag is off."""
    client = _client(monkeypatch, enabled=False, repo=_FakeRepo())
    # Requests are issued outside the assert so `python -O` cannot strip them.
    listed = client.get("/api/workspaces")
    created = client.post("/api/workspaces", json={"name": "W"})
    deleted = client.delete("/api/workspaces/1")
    assert listed.status_code == 404
    assert created.status_code == 404
    assert deleted.status_code == 404


def test_routes_reject_blank_name(monkeypatch):
    client = _client(monkeypatch, repo=_FakeRepo())
    created = client.post("/api/workspaces", json={"name": "   "})
    updated = client.put("/api/workspaces/1", json={"name": "  "})
    assert created.status_code == 400
    assert updated.status_code == 400


def test_routes_reject_unknown_config_keys(monkeypatch):
    """Extra config keys are a client bug, not something to silently persist."""
    client = _client(monkeypatch, repo=_FakeRepo())
    resp = client.post(
        "/api/workspaces",
        json={"name": "W", "config": {"selected_tools": [], "bogus": 1}},
    )
    assert resp.status_code == 422


def test_routes_create_passes_full_config(monkeypatch):
    fake = _FakeRepo()
    client = _client(monkeypatch, repo=fake)
    resp = client.post(
        "/api/workspaces",
        json={"name": "W", "config": {"selected_tools": ["a_b"], "rag_enabled": True}},
    )
    assert resp.status_code == 200
    assert fake.created["config"]["selected_tools"] == ["a_b"]
    assert fake.created["config"]["rag_enabled"] is True
    # Unspecified fields still arrive in the canonical shape.
    assert fake.created["config"]["selected_data_sources"] == []


def test_routes_503_without_repository(monkeypatch):
    """Writes fail loudly when chat history (the persistence layer) is absent."""
    client = _client(monkeypatch, repo=None)
    created = client.post("/api/workspaces", json={"name": "W"})
    assert created.status_code == 503
    # Listing degrades gracefully instead so the UI can render an empty state.
    resp = client.get("/api/workspaces")
    assert resp.status_code == 200
    assert resp.json()["workspaces"] == []


def test_routes_delete_missing_returns_404(monkeypatch):
    client = _client(monkeypatch, repo=_FakeRepo())
    deleted = client.delete("/api/workspaces/1")
    missing = client.delete("/api/workspaces/nope")
    assert deleted.status_code == 200
    assert missing.status_code == 404


# --- settings gate --------------------------------------------------------


@pytest.mark.parametrize(
    "workspaces,chat_history,expected",
    [
        ("true", "false", False),  # nowhere to persist workspaces
        ("false", "true", False),
        ("true", "true", True),
    ],
)
def test_workspaces_effective_requires_chat_history(
    monkeypatch, workspaces, chat_history, expected
):
    """Workspaces live in the chat-history DB, so the flag alone is not enough."""
    from atlas.modules.config.settings import AppSettings

    monkeypatch.setenv("FEATURE_WORKSPACES_ENABLED", workspaces)
    monkeypatch.setenv("FEATURE_CHAT_HISTORY_ENABLED", chat_history)
    # _env_file=None so a developer's local .env cannot flip the result.
    settings = AppSettings(_env_file=None)
    assert settings.workspaces_effective is expected
