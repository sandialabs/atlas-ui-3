"""Regression tests for multi-segment S3 file keys in GET/DELETE routes.

Prior to the `{file_key:path}` converter change, keys containing `/` (e.g.
`users/alice@example.com/generated/foo.txt`) returned a 404 from FastAPI's
router because the default path parameter only matches a single path segment.
These tests ensure such keys now reach the handler.
"""

import base64

from main import app
from starlette.testclient import TestClient

MULTI_SEGMENT_KEY = "users/alice@example.com/generated/subdir/report.txt"


def test_get_file_accepts_multisegment_key(monkeypatch):
    """GET /api/files/{file_key:path} must route multi-segment keys to the handler."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()

    captured = {}

    async def fake_get_file(user, key):
        captured["user"] = user
        captured["key"] = key
        return {
            "key": key,
            "filename": "report.txt",
            "content_base64": base64.b64encode(b"data").decode(),
            "content_type": "text/plain",
            "size": 4,
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }

    monkeypatch.setattr(s3, "get_file", fake_get_file)

    resp = client.get(
        f"/api/files/{MULTI_SEGMENT_KEY}",
        headers={"X-User-Email": "alice@example.com"},
    )

    assert resp.status_code == 200, resp.text
    # Critical: the full multi-segment key must reach the handler, not be truncated.
    assert captured["key"] == MULTI_SEGMENT_KEY
    assert resp.json()["key"] == MULTI_SEGMENT_KEY


def test_delete_file_accepts_multisegment_key(monkeypatch):
    """DELETE /api/files/{file_key:path} must route multi-segment keys to the handler."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()

    captured = {}

    async def fake_delete_file(user, key):
        captured["user"] = user
        captured["key"] = key
        return True

    monkeypatch.setattr(s3, "delete_file", fake_delete_file)

    resp = client.delete(
        f"/api/files/{MULTI_SEGMENT_KEY}",
        headers={"X-User-Email": "alice@example.com"},
    )

    assert resp.status_code == 200, resp.text
    assert captured["key"] == MULTI_SEGMENT_KEY
    assert resp.json()["key"] == MULTI_SEGMENT_KEY


def test_specific_files_routes_not_shadowed_by_path_catchall():
    """The greedy {file_key:path} route must not swallow /files/healthz, /files, etc.

    Route declaration order matters: specific routes (healthz, list, download, stats)
    are declared before the path catch-all in files_routes.py.
    """
    client = TestClient(app)

    # healthz must return the health payload, not be treated as a file key
    resp = client.get("/api/files/healthz", headers={"X-User-Email": "alice@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("service") == "files-api"
    assert "s3_config" in data
