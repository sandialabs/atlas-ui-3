import base64
from starlette.testclient import TestClient

from main import app
from core.capabilities import generate_file_token


def test_files_download_with_token(monkeypatch):
    client = TestClient(app)

    # Prepare fake file in mock S3 by monkeypatching S3 client get_file
    from infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()

    async def fake_get_file(user, key):
        return {
            "key": key,
            "filename": "hello.txt",
            "content_base64": base64.b64encode(b"hello").decode(),
            "content_type": "text/plain",
            "size": 5,
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }

    monkeypatch.setattr(s3, "get_file", fake_get_file)

    token = generate_file_token(user_email="test@test.com", file_key="k1", ttl_seconds=60)

    resp = client.get(
        "/api/files/download/k1",
        params={"token": token},
        headers={"X-User-Email": "ignored@example.com"},  # token overrides
    )
    assert resp.status_code == 200
    assert resp.content == b"hello"
    ct = resp.headers.get("content-type", "")
    assert ct.startswith("text/plain")
