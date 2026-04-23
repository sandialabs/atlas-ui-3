"""Tests for OpenTelemetry span emission in file-storage clients.

Covers the ``file.upload``, ``file.download``, ``storage.list``, and
``storage.delete`` span contract emitted by ``S3StorageClient`` and
``MockS3StorageClient``. The four spans carry pseudonymized identifiers
(HMAC hashes) and sanitized labels — never raw keys, filenames (beyond the
sanitized label), bucket names, or user emails.
"""

from __future__ import annotations

import base64
from typing import List, Optional
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

# ---------------------------------------------------------------------------
# Tracer fixtures (mirror test_telemetry_spans.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _module_provider():
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    trace.set_tracer_provider(provider)
    return trace.get_tracer_provider()


@pytest.fixture
def span_exporter(_module_provider):
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    _module_provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
        exporter.clear()


def _by_name(spans: List[ReadableSpan], name: str) -> ReadableSpan:
    matching = [s for s in spans if s.name == name]
    assert matching, f"No span named {name!r} in {[s.name for s in spans]}"
    return matching[-1]


# ---------------------------------------------------------------------------
# Helpers: stable HMAC so hash comparisons are deterministic
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_hmac(monkeypatch):
    monkeypatch.setenv("ATLAS_TELEMETRY_HMAC_SECRET", "storage-spans-test-secret")


def _assert_no_sensitive_values(span: ReadableSpan, forbidden: List[str]) -> None:
    """Fail if any span attribute value contains a forbidden substring.

    The ``filename`` attribute is allowed to contain the sanitized filename
    label, but the raw untrimmed version / path must not appear anywhere.
    """
    for key, value in span.attributes.items():
        if isinstance(value, str):
            for forbidden_val in forbidden:
                assert forbidden_val not in value, (
                    f"Forbidden value {forbidden_val!r} found in attr {key}={value!r}"
                )


# ---------------------------------------------------------------------------
# MockS3StorageClient: full CRUD lifecycle produces the documented spans
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

    return MockS3StorageClient(s3_bucket_name="atlas-test-spans")


@pytest.mark.asyncio
async def test_mock_upload_emits_file_upload_span(mock_client, span_exporter):
    user_email = "alice@example.com"
    filename = "report.txt"
    content = b"hello world"
    b64 = base64.b64encode(content).decode()

    result = await mock_client.upload_file(
        user_email=user_email,
        filename=filename,
        content_base64=b64,
        content_type="text/plain",
        source_type="user",
    )
    assert result["size"] == len(content)

    span = _by_name(span_exporter.get_finished_spans(), "file.upload")
    attrs = span.attributes
    assert attrs["storage_backend"] == "mock"
    assert attrs["success"] is True
    assert attrs["file_size"] == len(content)
    assert attrs["content_type"] == "text/plain"
    assert attrs["source_type"] == "user"
    assert attrs["category"] == "uploads"
    assert attrs["filename"] == filename  # safe_label passes short ASCII through
    # Hashes present and 16 hex chars
    assert len(attrs["user_hash"]) == 16
    assert len(attrs["key_hash"]) == 16
    assert "duration_ms" in attrs
    # Negative control: raw user email and raw generated key must not leak.
    raw_key = result["key"]
    _assert_no_sensitive_values(span, [user_email, raw_key])


@pytest.mark.asyncio
async def test_mock_download_emits_file_download_span(mock_client, span_exporter):
    user_email = "bob@example.com"
    uploaded = await mock_client.upload_file(
        user_email=user_email,
        filename="doc.txt",
        content_base64=base64.b64encode(b"payload").decode(),
        content_type="text/plain",
        source_type="user",
    )
    span_exporter.clear()

    result = await mock_client.get_file(user_email, uploaded["key"])
    assert result is not None

    span = _by_name(span_exporter.get_finished_spans(), "file.download")
    attrs = span.attributes
    assert attrs["storage_backend"] == "mock"
    assert attrs["success"] is True
    assert attrs["file_size"] == len(b"payload")
    assert attrs["category"] == "uploads"
    assert attrs["filename"] == "doc.txt"
    assert len(attrs["user_hash"]) == 16
    assert len(attrs["key_hash"]) == 16
    _assert_no_sensitive_values(span, [user_email, uploaded["key"]])


@pytest.mark.asyncio
async def test_mock_list_emits_storage_list_span(mock_client, span_exporter):
    user_email = "carol@example.com"
    for i in range(2):
        await mock_client.upload_file(
            user_email=user_email,
            filename=f"f{i}.txt",
            content_base64=base64.b64encode(b"x").decode(),
            content_type="text/plain",
            source_type="user",
        )
    span_exporter.clear()

    files = await mock_client.list_files(user_email, file_type="user", limit=10)
    assert len(files) >= 2

    span = _by_name(span_exporter.get_finished_spans(), "storage.list")
    attrs = span.attributes
    assert attrs["storage_backend"] == "mock"
    assert attrs["success"] is True
    assert attrs["file_type"] == "user"
    assert attrs["limit"] == 10
    assert attrs["num_results"] == len(files)
    assert attrs["total_bytes"] >= 0
    assert len(attrs["user_hash"]) == 16
    _assert_no_sensitive_values(span, [user_email])


@pytest.mark.asyncio
async def test_mock_delete_emits_storage_delete_span(mock_client, span_exporter):
    user_email = "dan@example.com"
    uploaded = await mock_client.upload_file(
        user_email=user_email,
        filename="del.txt",
        content_base64=base64.b64encode(b"bye").decode(),
        content_type="text/plain",
        source_type="user",
    )
    span_exporter.clear()

    ok = await mock_client.delete_file(user_email, uploaded["key"])
    assert ok is True

    span = _by_name(span_exporter.get_finished_spans(), "storage.delete")
    attrs = span.attributes
    assert attrs["storage_backend"] == "mock"
    assert attrs["success"] is True
    assert attrs["category"] == "uploads"
    assert len(attrs["user_hash"]) == 16
    assert len(attrs["key_hash"]) == 16
    _assert_no_sensitive_values(span, [user_email, uploaded["key"]])


@pytest.mark.asyncio
async def test_mock_download_access_denied_persists_flag(mock_client, span_exporter):
    """Cross-user key attempts must set access_denied BEFORE the raise."""
    with pytest.raises(Exception, match="Access denied"):
        await mock_client.get_file(
            user_email="eve@example.com",
            file_key="users/victim@example.com/uploads/1_x_secret.txt",
        )

    span = _by_name(span_exporter.get_finished_spans(), "file.download")
    attrs = span.attributes
    assert attrs["access_denied"] is True
    assert attrs["success"] is False
    assert attrs["storage_backend"] == "mock"
    # Raw victim key / attacker email must not appear anywhere
    _assert_no_sensitive_values(
        span,
        ["eve@example.com", "victim@example.com", "1_x_secret.txt"],
    )


@pytest.mark.asyncio
async def test_mock_delete_access_denied_persists_flag(mock_client, span_exporter):
    with pytest.raises(Exception, match="Access denied"):
        await mock_client.delete_file(
            user_email="eve@example.com",
            file_key="users/victim@example.com/uploads/1_x_target.txt",
        )

    span = _by_name(span_exporter.get_finished_spans(), "storage.delete")
    attrs = span.attributes
    assert attrs["access_denied"] is True
    assert attrs["success"] is False
    _assert_no_sensitive_values(
        span,
        ["eve@example.com", "victim@example.com", "1_x_target.txt"],
    )


@pytest.mark.asyncio
async def test_mock_download_not_found_sets_flag(mock_client, span_exporter):
    result = await mock_client.get_file(
        user_email="frank@example.com",
        file_key="users/frank@example.com/uploads/missing.txt",
    )
    assert result is None
    span = _by_name(span_exporter.get_finished_spans(), "file.download")
    assert span.attributes["not_found"] is True
    assert span.attributes["success"] is False
    # error_type is set on the non-raising not-found branch so failure-mode
    # aggregation can group it alongside raised errors.
    assert span.attributes["error_type"] == "NotFound"


@pytest.mark.asyncio
async def test_mock_delete_not_found_sets_flag(mock_client, span_exporter):
    ok = await mock_client.delete_file(
        user_email="grace@example.com",
        file_key="users/grace@example.com/uploads/missing.txt",
    )
    assert ok is False
    span = _by_name(span_exporter.get_finished_spans(), "storage.delete")
    assert span.attributes["not_found"] is True
    assert span.attributes["success"] is False
    assert span.attributes["error_type"] == "NotFound"


@pytest.mark.asyncio
async def test_mock_list_file_type_none_uses_sentinel(mock_client, span_exporter):
    """file_type=None must surface as the string sentinel ``"null"`` so the
    ``storage.list`` span always has the attribute present for analysis."""
    await mock_client.list_files(user_email="h@example.com", file_type=None, limit=5)
    span = _by_name(span_exporter.get_finished_spans(), "storage.list")
    assert span.attributes["file_type"] == "null"


@pytest.mark.asyncio
async def test_mock_upload_filename_label_sanitized(mock_client, span_exporter):
    """Control chars in a filename must be stripped from the span label."""
    from atlas.core.telemetry import LABEL_MAX_CHARS

    bad = "evil\nname\rWithCRLF.txt"
    await mock_client.upload_file(
        user_email="u@x.com",
        filename=bad,
        content_base64=base64.b64encode(b"x").decode(),
        content_type="text/plain",
        source_type="user",
    )
    span = _by_name(span_exporter.get_finished_spans(), "file.upload")
    label = span.attributes["filename"]
    assert "\n" not in label and "\r" not in label
    assert len(label) <= LABEL_MAX_CHARS


# ---------------------------------------------------------------------------
# S3StorageClient (real boto3 client) — mock boto3 to avoid network
# ---------------------------------------------------------------------------


def _make_s3_client_with_stubbed_boto(monkeypatch):
    """Build an S3StorageClient whose underlying boto3 client is a MagicMock.

    Skips the bucket-creation call and config_manager lookup by supplying
    every constructor argument.
    """
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
async def test_s3_upload_span_attributes(monkeypatch, span_exporter):
    from datetime import datetime

    client, boto = _make_s3_client_with_stubbed_boto(monkeypatch)
    boto.put_object.return_value = {}
    boto.head_object.return_value = {
        "LastModified": datetime(2026, 1, 1),
        "ETag": '"abc"',
    }

    result = await client.upload_file(
        user_email="alice@example.com",
        filename="report.txt",
        content_base64=base64.b64encode(b"hello").decode(),
        content_type="text/plain",
        source_type="user",
    )
    assert result["size"] == 5

    span = _by_name(span_exporter.get_finished_spans(), "file.upload")
    attrs = span.attributes
    assert attrs["storage_backend"] == "s3"
    assert attrs["success"] is True
    assert attrs["file_size"] == 5
    assert attrs["category"] == "uploads"
    assert attrs["source_type"] == "user"
    # Parity with mock: contract attribute shape is identical.
    for key in ("user_hash", "key_hash", "filename", "content_type", "duration_ms"):
        assert key in attrs, f"Missing {key!r} on s3 file.upload span"


@pytest.mark.asyncio
async def test_s3_upload_failure_span(monkeypatch, span_exporter):
    from botocore.exceptions import ClientError

    client, boto = _make_s3_client_with_stubbed_boto(monkeypatch)

    boto.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "boom with SECRET token"}},
        "PutObject",
    )

    raised: Optional[BaseException] = None
    try:
        await client.upload_file(
            user_email="a@b.com",
            filename="f.txt",
            content_base64=base64.b64encode(b"x").decode(),
            content_type="text/plain",
        )
    except Exception as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None

    # The raised exception message must NOT echo the raw boto Error.Message —
    # that string can contain tokens, caller args, or other sensitive content.
    # The sanitized preview goes on the span; the raised exception is generic.
    assert "SECRET" not in str(raised)
    assert "boom" not in str(raised)
    assert str(raised) == "S3 upload failed"

    span = _by_name(span_exporter.get_finished_spans(), "file.upload")
    attrs = span.attributes
    assert attrs["success"] is False
    # Inner except sets error_type="ClientError"; start_span then overrides
    # with the wrapping Exception's class name when the re-raised exception
    # propagates. Either is acceptable — both are non-sensitive class names.
    assert attrs["error_type"] in ("ClientError", "Exception")
    # error_message is the sanitized preview, not the raw exception repr
    assert "boom" in attrs["error_message"]
    # And it's capped well under PREVIEW/ERROR_MESSAGE_MAX_CHARS budget.
    assert len(attrs["error_message"]) <= 400
    # Known-at-failure-time values (key_hash, file_size, category) are populated
    # so upload_volume_by_user / category breakdowns include failed attempts.
    assert "file_size" in attrs
    assert attrs["file_size"] == 1  # b"x" is one byte after base64 decode
    assert "category" in attrs
    assert "key_hash" in attrs


@pytest.mark.asyncio
async def test_s3_get_not_found_sets_error_type(monkeypatch, span_exporter):
    """NoSuchKey returns None (no raise) but must populate error_type."""
    from botocore.exceptions import ClientError

    client, boto = _make_s3_client_with_stubbed_boto(monkeypatch)
    boto.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist"}},
        "GetObject",
    )

    result = await client.get_file(
        user_email="u@x.com",
        file_key="users/u@x.com/uploads/missing.txt",
    )
    assert result is None

    span = _by_name(span_exporter.get_finished_spans(), "file.download")
    assert span.attributes["not_found"] is True
    assert span.attributes["success"] is False
    assert span.attributes["error_type"] == "NoSuchKey"


@pytest.mark.asyncio
async def test_mock_and_s3_backend_attribute_parity(mock_client, monkeypatch, span_exporter):
    """Both backends must populate the same contract keys on a successful upload."""
    from datetime import datetime

    # Real-backend upload
    s3_client, boto = _make_s3_client_with_stubbed_boto(monkeypatch)
    boto.put_object.return_value = {}
    boto.head_object.return_value = {"LastModified": datetime(2026, 1, 1), "ETag": '"e"'}

    await s3_client.upload_file(
        user_email="u@x.com",
        filename="a.txt",
        content_base64=base64.b64encode(b"x").decode(),
        content_type="text/plain",
        source_type="user",
    )
    # Mock-backend upload
    await mock_client.upload_file(
        user_email="u@x.com",
        filename="a.txt",
        content_base64=base64.b64encode(b"x").decode(),
        content_type="text/plain",
        source_type="user",
    )

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "file.upload"]
    assert len(spans) >= 2
    s3_span = next(s for s in spans if s.attributes.get("storage_backend") == "s3")
    mock_span = next(s for s in spans if s.attributes.get("storage_backend") == "mock")
    # Contract keys identical (value of storage_backend aside)
    shared = {"user_hash", "key_hash", "filename", "content_type", "file_size",
              "source_type", "category", "storage_backend", "success", "duration_ms"}
    assert shared.issubset(set(s3_span.attributes.keys()))
    assert shared.issubset(set(mock_span.attributes.keys()))
