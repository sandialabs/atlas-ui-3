"""
Tests for the /mcp/files/download/ endpoint.

This endpoint is used by MCP servers and other non-browser clients to download
files using HMAC capability tokens. In production, nginx skips auth_request
for /mcp/ paths, so the backend handles all authentication via tokens.
"""

import base64

from main import app
from starlette.testclient import TestClient

from atlas.core.capabilities import generate_file_token


def _fake_s3_get_file(content=b"hello", filename="hello.txt", content_type="text/plain"):
    """Create a fake S3 get_file coroutine."""
    async def fake_get_file(user, key):
        return {
            "key": key,
            "filename": filename,
            "content_base64": base64.b64encode(content).decode(),
            "content_type": content_type,
            "size": len(content),
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }
    return fake_get_file


def test_mcp_download_with_valid_token(monkeypatch):
    """MCP download with a valid capability token should succeed."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    token = generate_file_token(user_email="test@test.com", file_key="k1", ttl_seconds=60)

    resp = client.get(
        "/mcp/files/download/k1",
        params={"token": token},
    )
    assert resp.status_code == 200
    assert resp.content == b"hello"
    ct = resp.headers.get("content-type", "")
    assert ct.startswith("text/plain")


def test_mcp_download_without_token_returns_401(monkeypatch):
    """MCP download without a token should return 401."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    resp = client.get("/mcp/files/download/k1")
    assert resp.status_code == 401


def test_mcp_download_with_invalid_token_returns_401(monkeypatch):
    """MCP download with an invalid token should return 401."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    resp = client.get(
        "/mcp/files/download/k1",
        params={"token": "not.a.valid.token"},
    )
    assert resp.status_code == 401


def test_mcp_download_with_expired_token_returns_401(monkeypatch):
    """MCP download with an expired token should return 401."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    expired_token = generate_file_token("alice@example.com", "k1", ttl_seconds=-5)
    resp = client.get(
        "/mcp/files/download/k1",
        params={"token": expired_token},
    )
    assert resp.status_code == 401


def test_mcp_download_token_wrong_key_returns_403(monkeypatch):
    """MCP download with a token for a different file key should return 403."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    # Token for "other-key" but requesting "k1"
    token = generate_file_token("test@test.com", "other-key", ttl_seconds=60)
    resp = client.get(
        "/mcp/files/download/k1",
        params={"token": token},
    )
    assert resp.status_code == 403


def test_api_download_still_works_with_token(monkeypatch):
    """The /api/files/download/ path should still accept tokens for backward compat."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    token = generate_file_token(user_email="test@test.com", file_key="k1", ttl_seconds=60)

    resp = client.get(
        "/api/files/download/k1",
        params={"token": token},
        headers={"X-User-Email": "ignored@example.com"},
    )
    assert resp.status_code == 200
    assert resp.content == b"hello"


def test_api_download_works_with_auth_header(monkeypatch):
    """The /api/files/download/ path should work with X-User-Email header (browser path)."""
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "get_file", _fake_s3_get_file())

    resp = client.get(
        "/api/files/download/k1",
        headers={"X-User-Email": "browser-user@example.com"},
    )
    assert resp.status_code == 200
    assert resp.content == b"hello"
