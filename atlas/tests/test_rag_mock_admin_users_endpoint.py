"""Unit tests for the RAG API mock's POST /admin/users endpoint.

The mock ships a fixed user database (``mock_data.json``); ``/admin/users`` is
the test-only helper the integration fixture uses to register the configured
ATLAS identity. These tests pin that helper's contract -- ``clone_from``, the
either/or validation, and bearer auth -- so the fixture can rely on it.
"""

import importlib.util
from pathlib import Path

from starlette.testclient import TestClient

_MOCK_MAIN = Path(__file__).resolve().parents[2] / "mocks" / "atlas-rag-api-mock" / "main.py"
_spec = importlib.util.spec_from_file_location("atlas_rag_mock_main_for_tests", _MOCK_MAIN)
mock_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_main)


def _client():
    return TestClient(mock_main.app)


def _auth():
    return {"Authorization": f"Bearer {mock_main.shared_key}"}


def test_register_with_groups():
    client = _client()
    resp = client.post("/admin/users", headers=_auth(), json={"user": "a@example.com", "groups": ["employee"]})
    assert resp.status_code == 200
    assert resp.json() == {"user": "a@example.com", "groups": ["employee"]}


def test_register_with_clone_from_copies_source_groups():
    client = _client()
    resp = client.post(
        "/admin/users",
        headers=_auth(),
        json={"user": "b@example.com", "clone_from": "test@test.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "b@example.com"
    assert body["groups"] == list(mock_main.USERS_GROUPS_DB["test@test.com"])
    assert body["groups"], "cloned groups should not be empty"


def test_clone_from_unknown_user_not_found():
    client = _client()
    resp = client.post(
        "/admin/users",
        headers=_auth(),
        json={"user": "c@example.com", "clone_from": "nobody@example.com"},
    )
    assert resp.status_code == 404
    assert "nobody@example.com" in resp.json()["detail"]


def test_register_rejects_neither_groups_nor_clone_from():
    client = _client()
    resp = client.post("/admin/users", headers=_auth(), json={"user": "d@example.com"})
    assert resp.status_code == 422


def test_register_rejects_both_groups_and_clone_from():
    client = _client()
    resp = client.post(
        "/admin/users",
        headers=_auth(),
        json={"user": "e@example.com", "groups": ["employee"], "clone_from": "test@test.com"},
    )
    assert resp.status_code == 422


def test_register_requires_bearer_token():
    client = _client()
    resp = client.post("/admin/users", json={"user": "f@example.com", "groups": ["employee"]})
    assert resp.status_code == 401
