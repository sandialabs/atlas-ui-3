"""The ``last_modified`` contract of the file APIs (issue #1104).

``POST /api/files`` answered HTTP 500 *after* the object had been stored:
``S3StorageClient.upload_file`` returned boto3's ``LastModified`` datetime
while ``FileResponse.last_modified`` is a string, so building the response
failed Pydantic validation.

These tests pin the contract at both ends:

* the storage clients report an ISO-8601 string, and
* the file routes answer 200 with that string even when a storage client
  hands back a datetime (the shape boto3 uses).
"""

import base64
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from main import app
from starlette.testclient import TestClient

# The shape boto3 returns for LastModified on every S3-compatible service.
BOTO3_TIMESTAMP = datetime(2026, 10, 21, 12, 30, 45, tzinfo=timezone.utc)
BOTO3_TIMESTAMP_ISO = BOTO3_TIMESTAMP.isoformat()


class _FakeStorage:
    """A storage backend that reports timestamps the way boto3 does."""

    endpoint_url = "fake://s3"
    bucket_name = "fake-bucket"

    def __init__(self, last_modified: Any = BOTO3_TIMESTAMP):
        self.last_modified = last_modified
        self.store: Dict[str, Dict[str, Any]] = {}

    async def upload_file(
        self,
        user_email: str,
        filename: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
        tags: Optional[Dict[str, str]] = None,
        source_type: str = "user",
    ) -> Dict[str, Any]:
        key = f"users/{user_email}/uploads/{filename}"
        meta = {
            "key": key,
            "filename": filename,
            "size": len(base64.b64decode(content_base64)),
            "content_type": content_type,
            "last_modified": self.last_modified,
            "etag": "fake-etag",
            "tags": tags or {"source": source_type},
            "user_email": user_email,
            "content_base64": content_base64,
        }
        self.store[key] = meta
        return meta

    async def get_file(self, user_email: str, file_key: str) -> Optional[Dict[str, Any]]:
        return self.store.get(file_key)

    async def list_files(
        self, user_email: str, file_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        return [meta for meta in self.store.values() if meta["user_email"] == user_email]


@pytest.fixture
def fake_storage(monkeypatch) -> _FakeStorage:
    """Serve the file routes from a backend that reports boto3 timestamps."""
    from atlas.infrastructure.app_factory import app_factory

    storage = _FakeStorage()
    monkeypatch.setattr(app_factory, "get_file_storage", lambda: storage)
    return storage


def _upload(client: TestClient, headers: Dict[str, str], filename: str = "a.txt"):
    return client.post(
        "/api/files",
        json={
            "filename": filename,
            "content_base64": base64.b64encode(b"hi\n").decode(),
            "content_type": "text/plain",
        },
        headers=headers,
    )


# --- Route level: a datetime from the storage layer must not fail the request ---


def test_upload_route_returns_iso_last_modified(fake_storage, test_user_headers):
    """The upload route answers 200 and reports the timestamp as a string."""
    client = TestClient(app)

    resp = _upload(client, test_user_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_modified"] == BOTO3_TIMESTAMP_ISO
    assert isinstance(body["last_modified"], str)
    # The stored value is still a parsable timestamp, not a stringified object.
    assert datetime.fromisoformat(body["last_modified"]) == BOTO3_TIMESTAMP


def test_list_route_returns_iso_last_modified(fake_storage, test_user_headers):
    """The listing route reports ISO strings, not datetimes."""
    client = TestClient(app)
    _upload(client, test_user_headers)

    resp = client.get("/api/files", headers=test_user_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["last_modified"] == BOTO3_TIMESTAMP_ISO


def test_get_file_route_returns_iso_last_modified(fake_storage, test_user_headers):
    """The single-file route reports an ISO string."""
    client = TestClient(app)
    key = _upload(client, test_user_headers).json()["key"]

    resp = client.get(f"/api/files/{key}", headers=test_user_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["last_modified"] == BOTO3_TIMESTAMP_ISO


def test_upload_route_tolerates_backend_without_timestamp(monkeypatch, test_user_headers):
    """A backend that cannot report a timestamp answers 200, not 500."""
    from atlas.infrastructure.app_factory import app_factory

    storage = _FakeStorage(last_modified=None)
    monkeypatch.setattr(app_factory, "get_file_storage", lambda: storage)
    client = TestClient(app)

    resp = _upload(client, test_user_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["last_modified"] is None


def test_upload_route_survives_a_stored_object(fake_storage, test_user_headers):
    """The reported failure mode: the object is stored even though the
    request failed. The route must both store it and answer 200."""
    client = TestClient(app)

    resp = _upload(client, test_user_headers)

    assert resp.status_code == 200, resp.text
    assert len(fake_storage.store) == 1
    stored_key = next(iter(fake_storage.store))
    assert stored_key.startswith("users/")

    listed = client.get("/api/files", headers=test_user_headers)
    assert listed.status_code == 200
    assert [f["key"] for f in listed.json()] == [stored_key]


# --- Client level: the storage layer reports ISO strings ---


def _s3_client_with_stubbed_boto(monkeypatch):
    """Build an S3StorageClient whose boto3 client is a MagicMock."""
    from atlas.modules.file_storage import s3_client as s3_mod

    monkeypatch.setattr(s3_mod.S3StorageClient, "_ensure_bucket", lambda self: None)
    fake_boto_client = MagicMock()
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **kw: fake_boto_client)

    client = s3_mod.S3StorageClient(
        s3_endpoint="http://fake",
        s3_bucket_name="b",
        s3_access_key="a",
        s3_secret_key="s",
        s3_region="us-east-1",
        s3_timeout=1,
        s3_use_ssl=False,
    )
    return client, fake_boto_client


@pytest.mark.asyncio
async def test_s3_upload_reports_iso_last_modified(monkeypatch):
    client, boto = _s3_client_with_stubbed_boto(monkeypatch)
    boto.put_object.return_value = {}
    boto.head_object.return_value = {"LastModified": BOTO3_TIMESTAMP, "ETag": '"abc"'}

    result = await client.upload_file(
        user_email="alice@example.com",
        filename="report.txt",
        content_base64=base64.b64encode(b"hello").decode(),
        content_type="text/plain",
        source_type="user",
    )

    assert result["last_modified"] == BOTO3_TIMESTAMP_ISO
    assert isinstance(result["last_modified"], str)


@pytest.mark.asyncio
async def test_s3_get_file_reports_iso_last_modified(monkeypatch):
    client, boto = _s3_client_with_stubbed_boto(monkeypatch)
    boto.get_object.return_value = {
        "Body": BytesIO(b"hello"),
        "ContentType": "text/plain",
        "LastModified": BOTO3_TIMESTAMP,
        "ETag": '"abc"',
        "Metadata": {"original_filename": "report.txt"},
    }
    boto.get_object_tagging.return_value = {"TagSet": []}

    result = await client.get_file(
        user_email="alice@example.com",
        file_key="users/alice@example.com/uploads/report.txt",
    )

    assert result["last_modified"] == BOTO3_TIMESTAMP_ISO
    assert isinstance(result["last_modified"], str)


@pytest.mark.asyncio
async def test_s3_list_files_reports_iso_last_modified_newest_first(monkeypatch):
    client, boto = _s3_client_with_stubbed_boto(monkeypatch)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 1, tzinfo=timezone.utc)
    boto.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "users/a@example.com/uploads/old.txt", "Size": 1, "ETag": '"o"', "LastModified": older},
            {"Key": "users/a@example.com/uploads/new.txt", "Size": 2, "ETag": '"n"', "LastModified": newer},
        ]
    }
    boto.get_object_tagging.return_value = {"TagSet": []}
    boto.head_object.return_value = {"Metadata": {}, "ContentType": "text/plain"}

    files = await client.list_files(user_email="a@example.com")

    assert [f["last_modified"] for f in files] == [newer.isoformat(), older.isoformat()]
    assert all(isinstance(f["last_modified"], str) for f in files)


@pytest.mark.asyncio
async def test_mock_upload_reports_iso_last_modified(monkeypatch, tmp_path):
    """The in-process mock backend reports a timestamp too, not ``None``."""
    from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

    monkeypatch.setenv("MOCK_S3_ROOT", str(tmp_path))
    client = MockS3StorageClient(s3_bucket_name="atlas-test-last-modified")

    result = await client.upload_file(
        user_email="alice@example.com",
        filename="report.txt",
        content_base64=base64.b64encode(b"hello").decode(),
        content_type="text/plain",
        source_type="user",
    )

    assert isinstance(result["last_modified"], str)
    assert datetime.fromisoformat(result["last_modified"]) is not None


@pytest.mark.asyncio
async def test_mock_list_reports_iso_last_modified(monkeypatch, tmp_path):
    from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

    monkeypatch.setenv("MOCK_S3_ROOT", str(tmp_path))
    client = MockS3StorageClient(s3_bucket_name="atlas-test-last-modified")
    await client.upload_file(
        user_email="alice@example.com",
        filename="report.txt",
        content_base64=base64.b64encode(b"hello").decode(),
        content_type="text/plain",
        source_type="user",
    )

    files = await client.list_files(user_email="alice@example.com")

    assert len(files) == 1
    assert isinstance(files[0]["last_modified"], str)
    assert datetime.fromisoformat(files[0]["last_modified"]) is not None


# --- The file manager keeps working with either shape ---


def test_organize_files_metadata_accepts_string_and_datetime():
    """``organize_files_metadata`` used to call ``.isoformat()`` on the value,
    which only works for a datetime. It now accepts both shapes."""
    from atlas.modules.file_storage.manager import FileManager

    manager = FileManager.__new__(FileManager)  # no S3 client needed

    from_strings = manager.organize_files_metadata(
        {"a.txt": {"key": "k1", "size": 1, "last_modified": BOTO3_TIMESTAMP_ISO}}
    )
    from_datetimes = manager.organize_files_metadata(
        {"a.txt": {"key": "k1", "size": 1, "last_modified": BOTO3_TIMESTAMP}}
    )
    without = manager.organize_files_metadata({"a.txt": {"key": "k1", "size": 1}})

    assert from_strings["files"][0]["last_modified"] == BOTO3_TIMESTAMP_ISO
    assert from_datetimes["files"][0]["last_modified"] == BOTO3_TIMESTAMP_ISO
    assert without["files"][0]["last_modified"] is None
