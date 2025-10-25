import time
import base64
from starlette.testclient import TestClient

from main import app
from core.capabilities import generate_file_token, verify_file_token


def test_capability_token_roundtrip_and_tamper(monkeypatch):
    # Basic generate/verify
    token = generate_file_token("alice@example.com", "file123", ttl_seconds=60)
    claims = verify_file_token(token)
    assert claims and claims["u"] == "alice@example.com" and claims["k"] == "file123"

    # Tamper body should fail
    body, sig = token.split(".", 1)
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B")
    bad = f"{tampered}.{sig}"
    assert verify_file_token(bad) is None


def test_capability_token_expiry(monkeypatch):
    # Create a token that is already expired
    token = generate_file_token("bob@example.com", "file999", ttl_seconds=-1)
    assert verify_file_token(token) is None


def test_download_rejects_invalid_or_expired_token(monkeypatch):
    client = TestClient(app)

    from infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()

    async def fake_get_file(user, key):
        return {
            "key": key,
            "filename": "hello.txt",
            "content_base64": base64.b64encode(b"secret").decode(),
            "content_type": "text/plain",
            "size": 6,
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }

    # Always return a file for these tests
    monkeypatch.setattr(s3, "get_file", fake_get_file)

    # Invalid token
    resp = client.get(
        "/api/files/download/k2",
        params={"token": "not.a.valid.token"},
        headers={"X-User-Email": "ignored@example.com"},
    )
    assert resp.status_code == 403

    # Expired token
    expired = generate_file_token("alice@example.com", "k2", ttl_seconds=-5)
    resp2 = client.get(
        "/api/files/download/k2",
        params={"token": expired},
        headers={"X-User-Email": "ignored@example.com"},
    )
    assert resp2.status_code == 403
