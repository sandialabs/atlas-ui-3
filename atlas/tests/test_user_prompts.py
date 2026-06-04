"""Tests for the per-user custom prompt library (issue #153).

Covers CRUD, ordering, and user isolation (including email case
normalization). Uses a temporary DuckDB database for fast, isolated tests.
"""

import pytest

from atlas.modules.chat_history.database import reset_engine


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import (
        UserPromptRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'test_user_prompts.db'}")
    return UserPromptRepository(get_session_factory())


def test_routes_reject_blank_content(monkeypatch):
    """The REST layer rejects empty/whitespace content on create and update."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from atlas.core.log_sanitizer import get_current_user
    from atlas.routes import user_prompt_routes

    class FakeRepo:
        def create_prompt(self, user_email, title, content):
            return {"id": "1", "title": title, "content": content}

        def update_prompt(self, prompt_id, user_email, title=None, content=None):
            return {"id": prompt_id, "title": title or "t", "content": content or "c"}

    monkeypatch.setattr(user_prompt_routes, "_get_repo", lambda: FakeRepo())

    app = FastAPI()
    app.include_router(user_prompt_routes.router)
    app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
    client = TestClient(app)

    # Blank content rejected on create...
    assert client.post("/api/user-prompts", json={"title": "T", "content": "   "}).status_code == 400
    # ...and on update.
    assert client.put("/api/user-prompts/1", json={"content": "   "}).status_code == 400
    # Valid content still works.
    assert client.post("/api/user-prompts", json={"title": "T", "content": "hi"}).status_code == 200


def test_create_and_list(repo):
    created = repo.create_prompt("alice@test.com", "Pirate", "You are a pirate.")
    assert created["id"]
    assert created["title"] == "Pirate"
    assert created["content"] == "You are a pirate."

    prompts = repo.list_prompts("alice@test.com")
    assert len(prompts) == 1
    assert prompts[0]["id"] == created["id"]


def test_title_is_trimmed(repo):
    created = repo.create_prompt("alice@test.com", "  Spaced  ", "body")
    assert created["title"] == "Spaced"


def test_get_respects_owner(repo):
    created = repo.create_prompt("alice@test.com", "Mine", "body")
    assert repo.get_prompt(created["id"], "alice@test.com") is not None
    assert repo.get_prompt(created["id"], "bob@test.com") is None


def test_update(repo):
    created = repo.create_prompt("alice@test.com", "Old", "old body")
    updated = repo.update_prompt(
        created["id"], "alice@test.com", title="New", content="new body"
    )
    assert updated["title"] == "New"
    assert updated["content"] == "new body"


def test_update_partial_keeps_other_field(repo):
    created = repo.create_prompt("alice@test.com", "Title", "body")
    updated = repo.update_prompt(created["id"], "alice@test.com", content="changed")
    assert updated["title"] == "Title"
    assert updated["content"] == "changed"


def test_update_wrong_owner_returns_none(repo):
    created = repo.create_prompt("alice@test.com", "Mine", "body")
    assert repo.update_prompt(created["id"], "bob@test.com", title="Hacked") is None
    # Unchanged for the real owner.
    assert repo.get_prompt(created["id"], "alice@test.com")["title"] == "Mine"


def test_delete(repo):
    created = repo.create_prompt("alice@test.com", "Temp", "body")
    assert repo.delete_prompt(created["id"], "alice@test.com") is True
    assert repo.list_prompts("alice@test.com") == []


def test_delete_wrong_owner(repo):
    created = repo.create_prompt("alice@test.com", "Mine", "body")
    assert repo.delete_prompt(created["id"], "bob@test.com") is False
    assert len(repo.list_prompts("alice@test.com")) == 1


def test_user_isolation_with_case_normalization(repo):
    repo.create_prompt("Alice@Test.com", "P1", "body")
    # Different-case lookup hits the same logical user.
    assert len(repo.list_prompts("alice@test.com")) == 1
    # A genuinely different user sees nothing.
    assert repo.list_prompts("bob@test.com") == []
