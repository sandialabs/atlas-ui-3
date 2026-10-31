"""File library explorer: page selection, and the paged list route.

The library used to hydrate every object up to the requested limit. Paging
filters and orders the cheap key listing first, then reads tags and metadata
only for the page that is returned.
"""

import base64
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from atlas.modules.file_storage.listing import filename_from_key, select_file_page


def _entry(key, when):
    return {"key": key, "size": 1, "etag": "e", "last_modified": when}


def test_select_file_page_is_newest_first_and_puts_unknown_dates_last():
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, tzinfo=timezone.utc)
    page, total = select_file_page(
        [
            _entry("users/a/uploads/old.txt", older),
            _entry("users/a/uploads/undated.txt", None),
            _entry("users/a/uploads/new.txt", newer),
        ],
        limit=10,
    )

    assert total == 3
    assert [entry["key"] for entry in page] == [
        "users/a/uploads/new.txt",
        "users/a/uploads/old.txt",
        "users/a/uploads/undated.txt",
    ]


def test_select_file_page_filters_query_category_and_offset():
    entries = [
        _entry(
            "users/a/uploads/1700000200_aaaaaaaa_notes.txt",
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        _entry(
            "users/a/uploads/1700000300_bbbbbbbb_photo.png",
            datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
        _entry(
            "users/a/generated/1700000100_cccccccc_sheet.csv",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]

    images, image_total = select_file_page(entries, limit=10, category="image")
    assert image_total == 1
    assert images[0]["key"].endswith("photo.png")

    notes, note_total = select_file_page(entries, limit=10, query="NOTES")
    assert note_total == 1
    assert notes[0]["key"].endswith("notes.txt")

    skipped, skipped_total = select_file_page(entries, limit=1, offset=1)
    assert skipped_total == 3
    assert len(skipped) == 1
    assert skipped[0]["key"].endswith("notes.txt")

    assert filename_from_key(entries[1]["key"]) == "photo.png"


def test_list_route_rejects_an_unknown_category(test_user_headers):
    from main import app

    client = TestClient(app)
    response = client.get(
        "/api/files",
        params={"category": "nope"},
        headers=test_user_headers,
    )
    assert response.status_code == 400


def test_list_route_reports_total_and_forwards_the_page(monkeypatch, test_user_headers):
    from main import app

    from atlas.infrastructure.app_factory import app_factory

    storage = app_factory.get_file_storage()
    captured = {}

    async def fake_page(**kwargs):
        captured.update(kwargs)
        return {
            "files": [{
                "key": "users/a/uploads/1_aaaaaaaa_notes.txt",
                "filename": "notes.txt",
                "size": 4,
                "content_type": "text/plain",
                "last_modified": "2026-06-01T00:00:00+00:00",
                "etag": "abc",
                "tags": {"source": "user"},
                "user_email": kwargs["user_email"],
            }],
            "total": 5,
            "offset": kwargs["offset"],
            "limit": kwargs["limit"],
        }

    monkeypatch.setattr(storage, "list_file_page", fake_page)
    client = TestClient(app)
    response = client.get(
        "/api/files",
        params={
            "limit": 1,
            "offset": 2,
            "q": "notes",
            "category": "document",
            "file_type": "user",
        },
        headers=test_user_headers,
    )

    assert response.status_code == 200, response.text
    assert response.headers["x-total-count"] == "5"
    body = response.json()
    assert len(body) == 1
    assert body[0]["filename"] == "notes.txt"
    assert captured["limit"] == 1
    assert captured["offset"] == 2
    assert captured["query"] == "notes"
    assert captured["category"] == "document"
    assert captured["file_type"] == "user"


async def test_s3_list_file_page_follows_continuation_tokens(monkeypatch):
    """A truncated key listing still returns the newest file, and only that page is read."""
    from atlas.modules.file_storage import s3_client as s3_mod

    monkeypatch.setattr(s3_mod.S3StorageClient, "_ensure_bucket", lambda self: None)
    boto = MagicMock()
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *args, **kwargs: boto)
    client = s3_mod.S3StorageClient(
        s3_endpoint="http://fake",
        s3_bucket_name="b",
        s3_access_key="a",
        s3_secret_key="s",
        s3_region="us-east-1",
        s3_timeout=1,
        s3_use_ssl=False,
    )

    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def list_objects(**kwargs):
        if kwargs.get("ContinuationToken") == "next":
            return {
                "IsTruncated": False,
                "Contents": [{
                    "Key": "users/a@example.com/uploads/200_aaaaaaaa_new.txt",
                    "Size": 2,
                    "ETag": '"n"',
                    "LastModified": newer,
                }],
            }
        return {
            "IsTruncated": True,
            "NextContinuationToken": "next",
            "Contents": [{
                "Key": "users/a@example.com/uploads/100_bbbbbbbb_old.txt",
                "Size": 1,
                "ETag": '"o"',
                "LastModified": older,
            }],
        }

    boto.list_objects_v2.side_effect = list_objects
    boto.get_object_tagging.return_value = {"TagSet": []}
    boto.head_object.return_value = {
        "Metadata": {"original_filename": "new.txt"},
        "ContentType": "text/plain",
    }

    page = await client.list_file_page(user_email="a@example.com", limit=1, offset=0)

    assert page["total"] == 2
    assert len(page["files"]) == 1
    assert page["files"][0]["filename"] == "new.txt"
    assert page["files"][0]["tags"]["source"] == "user"
    assert boto.head_object.call_count == 1
    assert boto.get_object_tagging.call_count == 1
    assert boto.list_objects_v2.call_count == 2


async def test_mock_list_file_page_orders_and_filters(monkeypatch, tmp_path):
    from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

    monkeypatch.setenv("MOCK_S3_ROOT", str(tmp_path))
    client = MockS3StorageClient(s3_bucket_name="atlas-explorer")
    user = "ada@example.com"

    uploaded = []
    for name, source in (("notes.txt", "user"), ("photo.png", "user"), ("sheet.csv", "tool")):
        stored = await client.upload_file(
            user_email=user,
            filename=name,
            content_base64=base64.b64encode(b"x").decode(),
            content_type="application/octet-stream",
            source_type=source,
        )
        uploaded.append(stored)

    stamps = {
        "notes.txt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "photo.png": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "sheet.csv": datetime(2026, 2, 1, tzinfo=timezone.utc),
    }
    for stored in uploaded:
        path = Path(tmp_path) / "atlas-explorer" / stored["key"]
        when = stamps[stored["filename"]]
        os.utime(path, (when.timestamp(), when.timestamp()))

    first = await client.list_file_page(user_email=user, limit=1, offset=0)
    assert first["total"] == 3
    assert first["files"][0]["filename"] == "photo.png"

    second = await client.list_file_page(user_email=user, limit=1, offset=1)
    assert second["files"][0]["filename"] == "sheet.csv"
    assert second["files"][0]["tags"]["source"] == "tool"

    images = await client.list_file_page(user_email=user, limit=10, category="image")
    assert images["total"] == 1
    assert images["files"][0]["filename"] == "photo.png"

    found = await client.list_file_page(user_email=user, limit=10, query="notes")
    assert found["total"] == 1
    assert found["files"][0]["filename"] == "notes.txt"
