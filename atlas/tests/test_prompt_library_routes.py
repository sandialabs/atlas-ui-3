"""REST tests for /api/prompts (issue #1169).

Uses the real router over a real library (temporary DuckDB). Identity comes
only from the authenticated-user dependency, switched per request with a
header the test app maps into it -- the same contract the auth middleware
gives production routes.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from atlas.core.log_sanitizer import get_current_user
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService
from atlas.routes import prompt_library_routes, user_prompt_routes

OWNER = "owner@example.com"
MEMBER = "member@example.com"
OUTSIDER = "outsider@example.com"


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'routes.db'}")
    members = {"eng": {OWNER, MEMBER}}

    async def check(user, group):
        return user in members.get(group, set())

    service = PromptLibraryService(
        PromptLibraryRepository(get_session_factory()), group_check=check,
        group_candidates=lambda: ["eng", "hr"],
    )
    monkeypatch.setattr(prompt_library_routes, "_get_service", lambda: service)
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: True)

    app = FastAPI()
    app.include_router(prompt_library_routes.router)

    async def user_from_test_header(request: Request) -> str:
        return request.headers["x-test-user"]

    app.dependency_overrides[get_current_user] = user_from_test_header
    test_client = TestClient(app)
    test_client.members = members
    return test_client


def as_user(user):
    return {"x-test-user": user}


def _create_shared(client, content="BODY v1"):
    r = client.post("/api/prompts", json={"title": "Reviewer", "content": content, "description": "d"},
                    headers=as_user(OWNER))
    assert r.status_code == 200
    pid = r.json()["prompt"]["id"]
    assert client.post(f"/api/prompts/{pid}/publish", json={"change_description": "c"},
                       headers=as_user(OWNER)).status_code == 200
    assert client.put(f"/api/prompts/{pid}/sharing", json={"groups": ["eng"], "users": []},
                      headers=as_user(OWNER)).status_code == 200
    return pid


def test_full_owner_flow(client):
    r = client.post("/api/prompts", json={"title": "T", "content": "draft"}, headers=as_user(OWNER))
    prompt = r.json()["prompt"]
    assert prompt["permission"] == "owner" and prompt["shared"] is False
    pid = prompt["id"]
    r = client.put(f"/api/prompts/{pid}", json={"content": "draft 2"}, headers=as_user(OWNER))
    assert r.json()["prompt"]["draft_content"] == "draft 2"
    v = client.post(f"/api/prompts/{pid}/publish", json={}, headers=as_user(OWNER)).json()["version"]
    assert v["version_number"] == 1 and v["content"] == "draft 2"
    assert client.get(f"/api/prompts/{pid}/versions/{v['id']}", headers=as_user(OWNER)).json()["version"]["content"] == "draft 2"
    resolved = client.get(f"/api/prompts/{pid}/resolve", headers=as_user(OWNER)).json()
    assert resolved == {"prompt_id": pid, "version_id": v["id"], "version_number": 1, "title": "T"}
    export = client.get(f"/api/prompts/{pid}/export", headers=as_user(OWNER)).json()
    assert export["export_format"] == "atlas-prompt-library/1" and len(export["versions"]) == 1
    audit = client.get(f"/api/prompts/{pid}/audit", headers=as_user(OWNER)).json()["events"]
    assert {e["event_type"] for e in audit} == {"create", "update_draft", "publish"}
    assert client.get("/api/prompts/groups", headers=as_user(OWNER)).json() == {"groups": ["eng"]}
    assert client.delete(f"/api/prompts/{pid}", headers=as_user(OWNER)).json() == {"success": True}
    assert client.get(f"/api/prompts/{pid}", headers=as_user(OWNER)).status_code == 404


def test_member_sees_published_entry_without_draft_or_sharing(client):
    pid = _create_shared(client)
    client.put(f"/api/prompts/{pid}", json={"content": "UNPUBLISHED DRAFT"}, headers=as_user(OWNER))
    listed = client.get("/api/prompts?scope=team", headers=as_user(MEMBER)).json()["prompts"]
    assert [p["id"] for p in listed] == [pid]
    detail = client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).json()["prompt"]
    assert detail["latest_content"] == "BODY v1"
    assert "UNPUBLISHED DRAFT" not in str(detail) and "sharing" not in detail
    export = client.get(f"/api/prompts/{pid}/export", headers=as_user(MEMBER)).json()
    assert "UNPUBLISHED DRAFT" not in str(export) and "sharing" not in export
    # Use-level callers get 403 (they can see it) for privileged actions.
    assert client.put(f"/api/prompts/{pid}", json={"content": "x"}, headers=as_user(MEMBER)).status_code == 403
    assert client.post(f"/api/prompts/{pid}/publish", json={}, headers=as_user(MEMBER)).status_code == 403
    assert client.get(f"/api/prompts/{pid}/sharing", headers=as_user(MEMBER)).status_code == 403
    assert client.get(f"/api/prompts/{pid}/audit", headers=as_user(MEMBER)).status_code == 403
    assert client.delete(f"/api/prompts/{pid}", headers=as_user(MEMBER)).status_code == 403
    fork = client.post(f"/api/prompts/{pid}/fork", json={}, headers=as_user(MEMBER)).json()["prompt"]
    assert fork["is_owner"] is True and fork["draft_content"] == "BODY v1"


def test_outsider_gets_identical_404s(client):
    pid = _create_shared(client)
    vid = client.get(f"/api/prompts/{pid}/versions", headers=as_user(OWNER)).json()["versions"][0]["id"]
    paths = [
        ("get", f"/api/prompts/{pid}", None),
        ("get", f"/api/prompts/{pid}/versions", None),
        ("get", f"/api/prompts/{pid}/versions/{vid}", None),
        ("get", f"/api/prompts/{pid}/sharing", None),
        ("get", f"/api/prompts/{pid}/export", None),
        ("get", f"/api/prompts/{pid}/audit", None),
        ("get", f"/api/prompts/{pid}/resolve", None),
        ("put", f"/api/prompts/{pid}", {"content": "x"}),
        ("put", f"/api/prompts/{pid}/sharing", {"groups": [], "users": []}),
        ("post", f"/api/prompts/{pid}/publish", {}),
        ("post", f"/api/prompts/{pid}/fork", {}),
        ("delete", f"/api/prompts/{pid}", None),
    ]
    for method, path, body in paths:
        real = getattr(client, method)(path, headers=as_user(OUTSIDER), **({"json": body} if body is not None else {}))
        ghost = getattr(client, method)(path.replace(pid, "00000000-0000-0000-0000-000000000000"),
                                        headers=as_user(OUTSIDER), **({"json": body} if body is not None else {}))
        assert real.status_code == ghost.status_code == 404, path
        assert real.json() == ghost.json(), path
    assert client.get("/api/prompts", headers=as_user(OUTSIDER)).json() == {"prompts": []}
    assert client.get("/api/prompts?q=Reviewer", headers=as_user(OUTSIDER)).json() == {"prompts": []}


def test_identity_cannot_be_supplied_in_the_body(client):
    # Extra fields naming another owner are ignored; the caller owns the entry.
    r = client.post("/api/prompts", json={"title": "T", "content": "c", "owner_email": OWNER, "user_email": OWNER},
                    headers=as_user(OUTSIDER))
    assert r.json()["prompt"]["owner_email"] == OUTSIDER


def test_membership_revocation_is_immediate(client):
    pid = _create_shared(client)
    assert client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).status_code == 200
    client.members["eng"].discard(MEMBER)
    assert client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).status_code == 404
    assert client.get("/api/prompts", headers=as_user(MEMBER)).json() == {"prompts": []}


def test_validation_and_sharing_errors(client):
    pid = _create_shared(client)
    assert client.post("/api/prompts", json={"title": " ", "content": "c"}, headers=as_user(OWNER)).status_code == 400
    assert client.put(f"/api/prompts/{pid}", json={"content": "  "}, headers=as_user(OWNER)).status_code == 400
    assert client.get("/api/prompts?scope=bogus", headers=as_user(OWNER)).status_code == 400
    assert client.put(f"/api/prompts/{pid}/sharing", json={"groups": ["hr"]}, headers=as_user(OWNER)).status_code == 403
    assert client.put(f"/api/prompts/{pid}/sharing", json={"groups": ["users"]}, headers=as_user(OWNER)).status_code == 400
    bad = {"groups": [], "users": [{"email": MEMBER, "permission": "admin"}]}
    assert client.put(f"/api/prompts/{pid}/sharing", json=bad, headers=as_user(OWNER)).status_code == 400


def test_feature_gate_hides_routes(client, monkeypatch):
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: False)
    assert client.get("/api/prompts", headers=as_user(OWNER)).status_code == 404


def test_legacy_user_prompt_routes_are_unchanged():
    """/api/user-prompts still serves private prompts under its own prefix."""
    paths = {r.path for r in user_prompt_routes.router.routes}
    assert paths == {"/api/user-prompts", "/api/user-prompts/{prompt_id}"}
