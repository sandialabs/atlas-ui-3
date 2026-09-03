"""Tests for the /api/personas routes (issue #880)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.core.log_sanitizer import get_current_user
from atlas.modules.prompts.persona_library import PersonaLibrary
from atlas.routes import persona_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "open.md").write_text(
        "---\nname: Open\ndescription: For everyone\n---\nOpen prompt", encoding="utf-8"
    )
    (tmp_path / "gated.md").write_text(
        "---\nname: Gated\naccess_group: secret-team\n---\nGated prompt", encoding="utf-8"
    )

    library = PersonaLibrary([tmp_path])
    monkeypatch.setattr(persona_routes, "get_persona_library", lambda: library)

    async def not_in_any_group(user, group):
        return False

    monkeypatch.setattr(persona_routes, "is_user_in_group", not_in_any_group)

    app = FastAPI()
    app.include_router(persona_routes.router)
    app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
    return TestClient(app)


def test_list_returns_only_ungated_personas(client):
    body = client.get("/api/personas").json()

    assert [p["name"] for p in body["personas"]] == ["Open"]
    persona = body["personas"][0]
    assert persona["id"] == "open"
    assert persona["description"] == "For everyone"
    # The list endpoint ships a short preview, not the full prompt body.
    assert "content" not in persona
    assert persona["preview"] == "Open prompt"


def test_list_preview_is_truncated(tmp_path, monkeypatch):
    (tmp_path / "long.md").write_text("x" * 5000, encoding="utf-8")
    library = PersonaLibrary([tmp_path])
    monkeypatch.setattr(persona_routes, "get_persona_library", lambda: library)

    app = FastAPI()
    app.include_router(persona_routes.router)
    app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
    client = TestClient(app)

    preview = client.get("/api/personas").json()["personas"][0]["preview"]
    assert len(preview) == 163  # 160 chars + ellipsis
    assert preview.endswith("...")


def test_get_single_persona(client):
    assert client.get("/api/personas/open").json()["persona"]["content"] == "Open prompt"


def test_gated_persona_is_404_for_non_members(client):
    assert client.get("/api/personas/gated").status_code == 404


def test_unknown_persona_is_404(client):
    assert client.get("/api/personas/nope").status_code == 404


def test_member_sees_the_gated_persona(tmp_path, monkeypatch):
    (tmp_path / "gated.md").write_text(
        "---\nname: Gated\naccess_group: secret-team\n---\nGated prompt", encoding="utf-8"
    )
    library = PersonaLibrary([tmp_path])
    monkeypatch.setattr(persona_routes, "get_persona_library", lambda: library)

    async def in_secret_team(user, group):
        return group == "secret-team"

    monkeypatch.setattr(persona_routes, "is_user_in_group", in_secret_team)

    app = FastAPI()
    app.include_router(persona_routes.router)
    app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
    client = TestClient(app)

    assert [p["id"] for p in client.get("/api/personas").json()["personas"]] == ["gated"]
    assert client.get("/api/personas/gated").status_code == 200
