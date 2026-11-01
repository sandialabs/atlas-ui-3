"""File library explorer: page selection, and the paged list route.

The library used to hydrate every object up to the requested limit. Paging
filters and orders the cheap key listing first, then reads tags and metadata
only for the page that is returned.
"""

import base64
import os
import time
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


def test_select_file_page_query_folds_spaces_and_ignores_the_path():
    entries = [
        _entry(
            "users/me@example.com/uploads/1700000200_aaaaaaaa_my_report.pdf",
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
    ]

    matched, total = select_file_page(entries, query=" my report ")
    assert total == 1
    assert matched[0]["key"].endswith("my_report.pdf")

    for query in ("uploads", "me@example.com", "generated"):
        _page, empty = select_file_page(entries, query=query)
        assert empty == 0

    # The timestamp and UUID inside the key are not part of the name the
    # user sees, and the browser filter never matches them either.
    for query in ("1700000200", "aaaaaaaa", "1700000200_aaaaaaaa"):
        _page, empty = select_file_page(entries, query=query)
        assert empty == 0


def test_select_file_page_query_folds_unicode_separators():
    entries = [
        _entry(
            "users/me@example.com/uploads/1700000200_aaaaaaaa_日本_報告.txt",
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
    ]

    matched, total = select_file_page(entries, query="日本 報告")
    assert total == 1
    assert matched[0]["key"].endswith("日本_報告.txt")


def test_select_file_page_caps_the_page_size():
    entries = [
        _entry(f"users/a/uploads/{1700000000 + i}_aaaaaaaa_f{i}.txt", datetime(2026, 6, 1, tzinfo=timezone.utc))
        for i in range(250)
    ]
    page, total = select_file_page(entries, limit=500)
    assert total == 250
    assert len(page) == 200


def test_list_route_rejects_a_page_above_the_cap(test_user_headers):
    from main import app

    client = TestClient(app)
    response = client.get("/api/files", params={"limit": 201}, headers=test_user_headers)
    assert response.status_code == 422


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


def _fake_s3_client(monkeypatch):
    """Build an S3StorageClient whose boto3 client is a MagicMock."""
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
    return client, boto


def _listed(key, size, when):
    return {
        "Key": key,
        "Size": size,
        "ETag": '"e"',
        "LastModified": when,
    }


async def test_s3_page_hydration_runs_concurrently_and_keeps_page_order(monkeypatch):
    """The page's per-row reads overlap, and the hydrated rows keep page order."""
    client, boto = _fake_s3_client(monkeypatch)

    stamps = [
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    ]
    names = ["one.txt", "two.txt", "three.txt"]
    keys = [
        f"users/a@example.com/uploads/{1700000300 - 1000 * i}_aaaaaaaa_{names[i]}"
        for i in range(3)
    ]
    filename_by_key = dict(zip(keys, names))
    boto.list_objects_v2.return_value = {
        "IsTruncated": False,
        "Contents": [
            _listed(key, 1, stamps[i])
            for i, key in enumerate(keys)
        ],
    }

    in_flight = 0
    max_in_flight = 0

    def head_object(**kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)
        in_flight -= 1
        return {
            "Metadata": {"original_filename": filename_by_key[kwargs["Key"]]},
            "ContentType": "text/plain",
        }

    boto.head_object.side_effect = head_object
    boto.get_object_tagging.return_value = {"TagSet": []}

    page = await client.list_file_page(user_email="a@example.com", limit=3, offset=0)

    assert [f["filename"] for f in page["files"]] == names
    assert boto.head_object.call_count == 3
    assert boto.get_object_tagging.call_count == 3
    assert max_in_flight >= 2


async def test_s3_user_stats_count_the_raw_listing_without_hydrating(monkeypatch):
    """Stats come from the key listing: no per-object reads, no 1000-file cap."""
    client, boto = _fake_s3_client(monkeypatch)

    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    uploads = [
        _listed(f"users/a@example.com/uploads/{1700000000 + i}_aaaaaaaa_f{i}.txt", i, when)
        for i in range(1000)
    ]
    generated = [
        _listed(f"users/a@example.com/generated/{1700000000 + i}_aaaaaaaa_g{i}.txt", i, when)
        for i in range(200)
    ]

    def list_objects(**kwargs):
        if kwargs.get("ContinuationToken") == "next":
            return {"IsTruncated": False, "Contents": generated}
        return {
            "IsTruncated": True,
            "NextContinuationToken": "next",
            "Contents": uploads,
        }

    boto.list_objects_v2.side_effect = list_objects

    stats = await client.get_user_stats("a@example.com")

    assert stats["total_files"] == 1200
    assert stats["upload_count"] == 1000
    assert stats["generated_count"] == 200
    assert stats["total_size"] == sum(range(1000)) + sum(range(200))
    assert boto.head_object.call_count == 0
    assert boto.get_object_tagging.call_count == 0


async def test_s3_listing_cap_fails_instead_of_reporting_a_partial_result(monkeypatch):
    """Past the traversal cap the client raises instead of truncating silently."""
    import pytest

    from atlas.modules.file_storage import s3_client as s3_mod
    from atlas.modules.file_storage.listing import ListingTooLargeError

    client, boto = _fake_s3_client(monkeypatch)
    monkeypatch.setattr(s3_mod, "_MAX_LIST_PAGES", 2)

    def list_objects(**kwargs):
        return {
            "IsTruncated": True,
            "NextContinuationToken": f"token-{kwargs.get('ContinuationToken')}",
            "Contents": [_listed("users/a@example.com/uploads/1_aaaaaaaa_x.txt", 1, datetime(2026, 6, 1, tzinfo=timezone.utc))],
        }

    boto.list_objects_v2.side_effect = list_objects

    with pytest.raises(ListingTooLargeError):
        await client.list_file_page(user_email="a@example.com", limit=10, offset=0)

    with pytest.raises(ListingTooLargeError):
        await client.get_user_stats("a@example.com")


async def test_mock_listing_follows_continuation_tokens(monkeypatch, tmp_path):
    """The mock pages through its whole prefix, one small request at a time."""
    from atlas.modules.file_storage import mock_s3_client as mock_mod
    from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

    monkeypatch.setenv("MOCK_S3_ROOT", str(tmp_path))
    monkeypatch.setattr(mock_mod, "_MOCK_LIST_MAX_KEYS", 2)
    client = MockS3StorageClient(s3_bucket_name="atlas-explorer")
    user = "ada@example.com"

    for i in range(5):
        await client.upload_file(
            user_email=user,
            filename=f"doc_{i}.txt",
            content_base64=base64.b64encode(b"x").decode(),
            content_type="application/octet-stream",
        )

    page = await client.list_file_page(user_email=user, limit=10, offset=0)
    assert page["total"] == 5
    assert sorted(f["filename"] for f in page["files"]) == [f"doc_{i}.txt" for i in range(5)]

    stats = await client.get_user_stats(user)
    assert stats["total_files"] == 5
    assert stats["upload_count"] == 5


async def test_s3_listing_source_tag_follows_the_key(monkeypatch):
    """A stale or non-standard source tag never contradicts the folder the file is counted in."""
    client, boto = _fake_s3_client(monkeypatch)

    boto.list_objects_v2.return_value = {
        "IsTruncated": False,
        "Contents": [
            _listed("users/a@example.com/uploads/1700000100_aaaaaaaa_x.txt", 1, datetime(2026, 6, 1, tzinfo=timezone.utc)),
            _listed("users/a@example.com/generated/1700000200_bbbbbbbb_y.txt", 1, datetime(2026, 6, 2, tzinfo=timezone.utc)),
        ],
    }
    # An old writer stored a source value that does not match the key path.
    boto.get_object_tagging.return_value = {"TagSet": [{"Key": "source", "Value": "chat"}]}
    boto.head_object.return_value = {
        "Metadata": {"original_filename": "x.txt"},
        "ContentType": "text/plain",
    }

    page = await client.list_file_page(user_email="a@example.com", limit=10, offset=0)

    sources = {f["key"].rsplit("/", 2)[-2]: f["tags"]["source"] for f in page["files"]}
    assert sources == {"uploads": "user", "generated": "tool"}


def test_list_route_maps_the_cap_to_a_fixed_message(monkeypatch, test_user_headers):
    from main import app

    from atlas.infrastructure.app_factory import app_factory
    from atlas.modules.file_storage.listing import ListingTooLargeError

    storage = app_factory.get_file_storage()

    async def too_large(**kwargs):
        raise ListingTooLargeError("User file listing stopped after 200 S3 pages (200000 objects)")

    monkeypatch.setattr(storage, "list_file_page", too_large)
    client = TestClient(app)
    response = client.get("/api/files", headers=test_user_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "The file library is too large to list"
    assert "200000" not in response.text


def test_list_route_returns_a_generic_message_for_other_failures(monkeypatch, test_user_headers):
    from main import app

    from atlas.infrastructure.app_factory import app_factory

    storage = app_factory.get_file_storage()

    async def broken(**kwargs):
        raise RuntimeError("internal storage endpoint http://10.0.0.1:9000 refused")

    monkeypatch.setattr(storage, "list_file_page", broken)
    client = TestClient(app)
    response = client.get("/api/files", headers=test_user_headers)

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to list files"
    assert "10.0.0.1" not in response.text


def test_stats_route_maps_the_cap_to_a_fixed_message(monkeypatch, test_user_headers, test_user):
    from main import app

    from atlas.infrastructure.app_factory import app_factory
    from atlas.modules.file_storage.listing import ListingTooLargeError

    storage = app_factory.get_file_storage()

    async def too_large(user_email):
        raise ListingTooLargeError("User file listing stopped after 200 S3 pages (200000 objects)")

    monkeypatch.setattr(storage, "get_user_stats", too_large)
    client = TestClient(app)
    response = client.get(f"/api/users/{test_user}/files/stats", headers=test_user_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "The file library is too large to list"


async def test_listing_walk_and_hydration_run_on_the_dedicated_executors(monkeypatch):
    """The blocking walk leaves the loop's default executor to other callers."""
    import threading

    client, boto = _fake_s3_client(monkeypatch)

    threads = {}

    def walk(user_email, prefix, generation):
        threads["walk"] = threading.current_thread().name
        return iter([
            {
                "Key": "users/a@example.com/uploads/1700000100_aaaaaaaa_x.txt",
                "Size": 1,
                "ETag": '"e"',
                "LastModified": datetime(2026, 6, 1, tzinfo=timezone.utc),
            }
        ])

    def head(**kwargs):
        threads["hydrate"] = threading.current_thread().name
        return {"Metadata": {}, "ContentType": "text/plain"}

    monkeypatch.setattr(client, "_iter_listed_objects", walk)
    boto.head_object.side_effect = head
    boto.get_object_tagging.return_value = {"TagSet": []}

    await client.list_file_page(user_email="a@example.com", limit=1, offset=0)

    assert threads["walk"].startswith("s3-io")
    assert threads["hydrate"].startswith("s3-hydrate")


def test_a_walk_stops_when_a_newer_listing_for_the_user_starts(monkeypatch):
    """The abandoned walk frees its worker at the next page boundary."""
    import pytest

    from atlas.modules.file_storage.listing import ListingSupersededError

    client, boto = _fake_s3_client(monkeypatch)

    calls = []

    def list_objects(**kwargs):
        calls.append(kwargs.get("ContinuationToken"))
        token = kwargs.get("ContinuationToken")
        return {
            "IsTruncated": True,
            "NextContinuationToken": f"after-{token}",
            "Contents": [],
        }

    boto.list_objects_v2.side_effect = list_objects

    first = client._begin_walk("a@example.com")
    assert client._walk_superseded("a@example.com", first) is False

    second = client._begin_walk("a@example.com")
    assert client._walk_superseded("a@example.com", first) is True
    assert client._walk_superseded("a@example.com", second) is False

    with pytest.raises(ListingSupersededError):
        list(client._iter_listed_objects("a@example.com", "users/a@example.com/", first))
    # The walk stops before its next S3 page: no call was ever made.
    assert len(calls) == 0
