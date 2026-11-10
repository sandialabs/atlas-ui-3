"""REST tests for /api/prompts (issue #1169).

Uses the real router over a real library (temporary DuckDB). Identity comes
only from the authenticated-user dependency, switched per request with a
header the test app maps into it -- the same contract the auth middleware
gives production routes. Requests with side effects are made in their own
statements, never inside an ``assert``.
"""

import logging

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
EMPTY_PAGE = {"prompts": [], "total": 0, "limit": 50, "offset": 0}


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
    test_client.service = service
    return test_client


def as_user(user):
    return {"x-test-user": user}


def _draft_revision(client, pid, user=OWNER):
    return client.get(f"/api/prompts/{pid}", headers=as_user(user)).json()["prompt"]["draft_revision"]


def _sharing_revision(client, pid, user=OWNER):
    return client.get(f"/api/prompts/{pid}/sharing", headers=as_user(user)).json()["sharing"]["revision"]


def _create_shared(client, content="BODY v1"):
    created = client.post("/api/prompts", json={"title": "Reviewer", "content": content, "description": "d"},
                          headers=as_user(OWNER))
    assert created.status_code == 200
    pid = created.json()["prompt"]["id"]
    published = client.post(f"/api/prompts/{pid}/publish",
                            json={"change_description": "c", "expected_draft_revision": 1}, headers=as_user(OWNER))
    assert published.status_code == 200
    shared = client.put(f"/api/prompts/{pid}/sharing", json={"groups": ["eng"], "users": [], "expected_revision": 0},
                        headers=as_user(OWNER))
    assert shared.status_code == 200
    return pid


def test_full_owner_flow(client):
    created = client.post("/api/prompts", json={"title": "T", "content": "draft"}, headers=as_user(OWNER))
    prompt = created.json()["prompt"]
    assert prompt["permission"] == "owner" and prompt["shared"] is False and prompt["draft_revision"] == 1
    pid = prompt["id"]
    updated = client.put(f"/api/prompts/{pid}", json={"content": "draft 2", "expected_draft_revision": 1},
                         headers=as_user(OWNER))
    assert updated.json()["prompt"]["draft_content"] == "draft 2"
    assert updated.json()["prompt"]["draft_revision"] == 2
    published = client.post(f"/api/prompts/{pid}/publish", json={"expected_draft_revision": 2}, headers=as_user(OWNER))
    v = published.json()["version"]
    assert v["version_number"] == 1 and v["content"] == "draft 2"
    assert client.get(f"/api/prompts/{pid}/versions/{v['id']}", headers=as_user(OWNER)).json()["version"]["content"] == "draft 2"
    resolved = client.get(f"/api/prompts/{pid}/resolve", headers=as_user(OWNER)).json()
    assert resolved == {"prompt_id": pid, "version_id": v["id"], "version_number": 1, "title": "T"}
    export = client.get(f"/api/prompts/{pid}/export", headers=as_user(OWNER)).json()
    assert export["export_format"] == "atlas-prompt-library/1" and len(export["versions"]) == 1
    audit = client.get(f"/api/prompts/{pid}/audit", headers=as_user(OWNER)).json()["events"]
    assert {e["event_type"] for e in audit} == {"create", "update_draft", "publish"}
    assert client.get("/api/prompts/groups", headers=as_user(OWNER)).json() == {"groups": ["eng"]}
    archived = client.delete(f"/api/prompts/{pid}", headers=as_user(OWNER))
    assert archived.json() == {"success": True}
    assert client.get(f"/api/prompts/{pid}", headers=as_user(OWNER)).status_code == 404


def test_revisions_are_required(client):
    pid = _create_shared(client)
    for method, path, body in (
        ("put", f"/api/prompts/{pid}", {"content": "x"}),
        ("post", f"/api/prompts/{pid}/publish", {}),
        ("put", f"/api/prompts/{pid}/sharing", {"groups": ["eng"], "users": []}),
    ):
        response = getattr(client, method)(path, json=body, headers=as_user(OWNER))
        assert response.status_code == 422, path


def test_stale_draft_and_sharing_revisions_answer_409(client):
    pid = _create_shared(client)
    first = client.put(f"/api/prompts/{pid}", json={"content": "editor A", "expected_draft_revision": 1},
                       headers=as_user(OWNER))
    assert first.status_code == 200
    stale_edit = client.put(f"/api/prompts/{pid}", json={"content": "editor B", "expected_draft_revision": 1},
                            headers=as_user(OWNER))
    assert stale_edit.status_code == 409 and "changed by someone else" in stale_edit.json()["detail"]
    stale_publish = client.post(f"/api/prompts/{pid}/publish", json={"expected_draft_revision": 1},
                                headers=as_user(OWNER))
    assert stale_publish.status_code == 409
    stale_share = client.put(f"/api/prompts/{pid}/sharing",
                             json={"groups": ["eng"], "users": [], "expected_revision": 0}, headers=as_user(OWNER))
    assert stale_share.status_code == 409
    assert _sharing_revision(client, pid) == 1


def test_member_sees_published_entry_without_draft_or_sharing(client):
    pid = _create_shared(client)
    edit = client.put(f"/api/prompts/{pid}", json={"content": "UNPUBLISHED DRAFT", "expected_draft_revision": 1},
                      headers=as_user(OWNER))
    assert edit.status_code == 200
    listed = client.get("/api/prompts?scope=team", headers=as_user(MEMBER)).json()["prompts"]
    assert [p["id"] for p in listed] == [pid]
    detail = client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).json()["prompt"]
    assert detail["latest_content"] == "BODY v1"
    assert "UNPUBLISHED DRAFT" not in str(detail) and "sharing" not in detail and "draft_revision" not in detail
    export = client.get(f"/api/prompts/{pid}/export", headers=as_user(MEMBER)).json()
    assert "UNPUBLISHED DRAFT" not in str(export) and "sharing" not in export
    # Use-level callers get 403 (they can see it) for privileged actions.
    refused = [
        client.put(f"/api/prompts/{pid}", json={"content": "x", "expected_draft_revision": 2}, headers=as_user(MEMBER)),
        client.post(f"/api/prompts/{pid}/publish", json={"expected_draft_revision": 2}, headers=as_user(MEMBER)),
        client.get(f"/api/prompts/{pid}/sharing", headers=as_user(MEMBER)),
        client.get(f"/api/prompts/{pid}/audit", headers=as_user(MEMBER)),
        client.delete(f"/api/prompts/{pid}", headers=as_user(MEMBER)),
    ]
    assert [r.status_code for r in refused] == [403] * 5
    forked = client.post(f"/api/prompts/{pid}/fork", json={}, headers=as_user(MEMBER))
    fork = forked.json()["prompt"]
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
        ("put", f"/api/prompts/{pid}", {"content": "x", "expected_draft_revision": 1}),
        ("put", f"/api/prompts/{pid}/sharing", {"groups": [], "users": [], "expected_revision": 1}),
        ("post", f"/api/prompts/{pid}/publish", {"expected_draft_revision": 1}),
        ("post", f"/api/prompts/{pid}/fork", {}),
        ("delete", f"/api/prompts/{pid}", None),
    ]
    for method, path, body in paths:
        kwargs = {"json": body} if body is not None else {}
        real = getattr(client, method)(path, headers=as_user(OUTSIDER), **kwargs)
        ghost = getattr(client, method)(path.replace(pid, "00000000-0000-0000-0000-000000000000"),
                                        headers=as_user(OUTSIDER), **kwargs)
        assert real.status_code == ghost.status_code == 404, path
        assert real.json() == ghost.json(), path
    assert client.get("/api/prompts", headers=as_user(OUTSIDER)).json() == EMPTY_PAGE
    assert client.get("/api/prompts?q=Reviewer", headers=as_user(OUTSIDER)).json() == EMPTY_PAGE


def test_resolve_is_read_only(client):
    """An advisory /resolve that is refused writes no audit event."""
    pid = _create_shared(client)
    before = client.service.repository.list_audit_events(pid)
    refused = client.get(f"/api/prompts/{pid}/resolve", headers=as_user(OUTSIDER))
    assert refused.status_code == 404
    client.members["eng"].discard(MEMBER)
    revoked = client.get(f"/api/prompts/{pid}/resolve", headers=as_user(MEMBER))
    assert revoked.status_code == 404
    assert client.service.repository.list_audit_events(pid) == before


def test_listing_is_paginated(client):
    ids = []
    for i in range(5):
        created = client.post("/api/prompts", json={"title": f"Item {i}", "content": "c"}, headers=as_user(OWNER))
        ids.append(created.json()["prompt"]["id"])
    first = client.get("/api/prompts?limit=2&offset=0", headers=as_user(OWNER)).json()
    second = client.get("/api/prompts?limit=2&offset=2", headers=as_user(OWNER)).json()
    last = client.get("/api/prompts?limit=2&offset=4", headers=as_user(OWNER)).json()
    assert first["total"] == second["total"] == last["total"] == 5
    seen = [p["id"] for page in (first, second, last) for p in page["prompts"]]
    assert sorted(seen) == sorted(ids) and len(seen) == 5
    assert client.get("/api/prompts?limit=0", headers=as_user(OWNER)).status_code == 422
    assert client.get("/api/prompts?limit=500", headers=as_user(OWNER)).status_code == 422


def test_search_wildcards_match_literally(client):
    for title in ("100% coverage", "under_score", "plain"):
        created = client.post("/api/prompts", json={"title": title, "content": "c"}, headers=as_user(OWNER))
        assert created.status_code == 200

    def titles(q):
        page = client.get("/api/prompts", params={"q": q}, headers=as_user(OWNER)).json()
        return sorted(p["title"] for p in page["prompts"])

    assert titles("%") == ["100% coverage"]
    assert titles("_") == ["under_score"]
    assert titles("0%") == ["100% coverage"]
    assert titles("nde_") == []  # "_" as a wildcard would match "nder"


def test_identity_cannot_be_supplied_in_the_body(client):
    # Extra fields naming another owner are ignored; the caller owns the entry.
    created = client.post("/api/prompts", json={"title": "T", "content": "c", "owner_email": OWNER,
                                                "user_email": OWNER}, headers=as_user(OUTSIDER))
    assert created.json()["prompt"]["owner_email"] == OUTSIDER


def test_membership_revocation_is_immediate(client):
    pid = _create_shared(client)
    assert client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).status_code == 200
    client.members["eng"].discard(MEMBER)
    assert client.get(f"/api/prompts/{pid}", headers=as_user(MEMBER)).status_code == 404
    assert client.get("/api/prompts", headers=as_user(MEMBER)).json() == EMPTY_PAGE


def test_validation_and_sharing_errors(client):
    pid = _create_shared(client)
    revision = _sharing_revision(client, pid)
    responses = {
        "blank title": client.post("/api/prompts", json={"title": " ", "content": "c"}, headers=as_user(OWNER)),
        "blank content": client.put(f"/api/prompts/{pid}", json={"content": "  ", "expected_draft_revision": 1},
                                    headers=as_user(OWNER)),
        "bad scope": client.get("/api/prompts?scope=bogus", headers=as_user(OWNER)),
        "not a member": client.put(f"/api/prompts/{pid}/sharing", json={"groups": ["hr"], "expected_revision": revision},
                                   headers=as_user(OWNER)),
        "everyone": client.put(f"/api/prompts/{pid}/sharing", json={"groups": [" Users "], "expected_revision": revision},
                               headers=as_user(OWNER)),
        "bad permission": client.put(
            f"/api/prompts/{pid}/sharing",
            json={"groups": [], "users": [{"email": MEMBER, "permission": "admin"}], "expected_revision": revision},
            headers=as_user(OWNER),
        ),
    }
    assert {k: r.status_code for k, r in responses.items()} == {
        "blank title": 400, "blank content": 400, "bad scope": 400,
        "not a member": 403, "everyone": 400, "bad permission": 400,
    }


def test_storage_failures_answer_503_without_leaking(client, monkeypatch, caplog):
    """A database error carrying prompt text and SQL never reaches the response or the log."""
    from sqlalchemy.exc import OperationalError

    secret = "TOP-SECRET-PROMPT-BODY"

    def boom(*args, **kwargs):
        raise OperationalError(f"INSERT INTO prompt_library_prompts VALUES ('{secret}')",
                               {"draft_content": secret, "owner_email": OWNER}, Exception(secret))

    monkeypatch.setattr(client.service.repository, "_session", boom)
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/prompts", json={"title": secret, "content": secret}, headers=as_user(OWNER))
        listing = client.get("/api/prompts", headers=as_user(OWNER))
    assert response.status_code == 503 and listing.status_code == 503
    assert secret not in response.text and OWNER not in response.text
    assert secret not in caplog.text and OWNER not in caplog.text
    assert "OperationalError" in caplog.text


def test_feature_gate_hides_routes(client, monkeypatch):
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: False)
    assert client.get("/api/prompts", headers=as_user(OWNER)).status_code == 404


def test_legacy_user_prompt_routes_are_unchanged():
    """/api/user-prompts still serves private prompts under its own prefix."""
    paths = {r.path for r in user_prompt_routes.router.routes}
    assert paths == {"/api/user-prompts", "/api/user-prompts/{prompt_id}"}
