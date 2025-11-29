import base64
import os
import sys
from typing import Optional

import pytest
from fastapi.testclient import TestClient

# Ensure backend root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub LiteLLM before importing app to avoid external dependency in tests
import types

fake_litellm_caller = types.ModuleType("modules.llm.litellm_caller")

class _FakeLLM:
    def __init__(self, *args, **kwargs):
        pass

    async def call_plain(self, *args, **kwargs):
        return "ok"

fake_litellm_caller.LiteLLMCaller = _FakeLLM  # type: ignore
sys.modules["modules.llm.litellm_caller"] = fake_litellm_caller

from main import app  # type: ignore
from core.capabilities import generate_file_token, verify_file_token  # type: ignore


class FakeS3:
    def __init__(self):
        self._store = {}
        self.endpoint_url = "mock://s3"
        self.bucket_name = "test-bucket"

    async def upload_file(self, user_email: str, filename: str, content_base64: str, content_type: str = "application/octet-stream", tags=None, source_type: str = "user"):
        key = f"k_{len(self._store)+1}"
        meta = {
            "key": key,
            "filename": filename,
            "size": len(base64.b64decode(content_base64)),
            "content_type": content_type,
            "last_modified": "now",
            "etag": "test",
            "tags": tags or {"source": source_type},
            "user_email": user_email,
            "content_base64": content_base64,
        }
        self._store[key] = meta
        return meta

    async def get_file(self, user_email: str, file_key: str):
        return self._store.get(file_key)


@pytest.fixture()
def client(monkeypatch):
    # Inject fake S3 into app_factory for route handlers
    from infrastructure import app_factory as af  # type: ignore

    fake = FakeS3()

    original_get = af.get_file_storage
    af.get_file_storage = lambda: fake  # type: ignore

    try:
        yield TestClient(app)
    finally:
        # Restore
        af.get_file_storage = original_get  # type: ignore


def test_capability_token_roundtrip():
    token = generate_file_token("alice@example.com", "k123", ttl_seconds=60)
    claims = verify_file_token(token)
    assert claims is not None
    assert claims["u"] == "alice@example.com"
    assert claims["k"] == "k123"


def _extract_key(resp_json) -> Optional[str]:
    if isinstance(resp_json, dict):
        return resp_json.get("key")
    return None


def test_download_with_and_without_token(client):
    """Test file upload/download with proper authentication in production mode."""
    # Upload a small text file via API with authentication header
    content = base64.b64encode(b"hello world").decode("utf-8")
    upload_resp = client.post(
        "/api/files",
        json={
            "filename": "hello.txt",
            "content_base64": content,
            "content_type": "text/plain",
            "tags": {"source": "test"},
        },
        headers={"X-User-Email": "test@example.com"},
    )
    assert upload_resp.status_code == 200
    key = _extract_key(upload_resp.json())
    assert key

    # Download with same user authentication
    dl_resp = client.get(
        f"/api/files/download/{key}",
        headers={"X-User-Email": "test@example.com"}
    )
    assert dl_resp.status_code == 200
    assert dl_resp.content == b"hello world"

    # With capability token for a different user (token grants access)
    token = generate_file_token("alice@example.com", key, ttl_seconds=60)
    dl_resp2 = client.get(
        f"/api/files/download/{key}",
        params={"token": token},
        headers={"X-User-Email": "alice@example.com"}
    )
    assert dl_resp2.status_code == 200
    assert dl_resp2.content == b"hello world"


def test_download_without_auth_fails_in_production_mode(client):
    """Test that requests without authentication fail in production mode (debug_mode=False)."""
    content = base64.b64encode(b"test content").decode("utf-8")
    
    # Try to upload without authentication - should fail with 401
    upload_resp = client.post(
        "/api/files",
        json={
            "filename": "test.txt",
            "content_base64": content,
            "content_type": "text/plain",
            "tags": {"source": "test"},
        },
    )
    assert upload_resp.status_code == 401


def test_injection_produces_tokenized_urls(client, monkeypatch):
    """Verify that tool argument injection replaces filename with tokenized URL."""
    from application.chat.utilities.tool_utils import inject_context_into_args  # type: ignore

    # Create a fake session context with a file mapping
    session_context = {
        "user_email": "bob@example.com",
        "files": {
            "report.pdf": {"key": "abc123", "content_type": "application/pdf"}
        },
    }

    args = {"filename": "report.pdf"}
    injected = inject_context_into_args(args, session_context)

    assert injected["username"] == "bob@example.com"
    assert injected["original_filename"] == "report.pdf"
    # URL should include /api/files/download/abc123 and a token query param
    assert injected["filename"].startswith("/api/files/download/abc123")
    assert "?token=" in injected["filename"]

    # Multiple files
    args2 = {"file_names": ["report.pdf", "missing.txt"]}
    injected2 = inject_context_into_args(args2, session_context)
    assert injected2["original_file_names"] == ["report.pdf", "missing.txt"]
    assert injected2["file_names"][0].startswith("/api/files/download/abc123")
    assert "?token=" in injected2["file_names"][0]
    # Missing.txt doesn't resolve to key, kept as-is
    assert injected2["file_names"][1] == "missing.txt"
