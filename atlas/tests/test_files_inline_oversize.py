"""Unit tests for the WebSocket inline-file oversize helper.

`find_oversized_inline_file` gates chat-attachment payloads sent over the
WebSocket before they reach the chat handler. It must accept both the
string-shaped (`{filename: base64}`) and dict-shaped
(`{filename: {"content": base64}}`) payloads, reject malformed entries with a
400, and honor the configured size limit at the boundary.
"""

import base64

import pytest
from fastapi import HTTPException

from atlas.infrastructure.app_factory import app_factory
from atlas.routes.files_routes import BYTES_PER_MIB, find_oversized_inline_file


def _b64(size_bytes: int) -> str:
    """Base64 for a payload that decodes to exactly ``size_bytes`` bytes."""
    return base64.b64encode(b"x" * size_bytes).decode()


@pytest.fixture
def limit_1mb():
    """Pin the upload limit to 1 MiB for the duration of a test."""
    config_manager = app_factory.get_config_manager()
    original = config_manager.app_settings.max_file_upload_size_mb
    config_manager.app_settings.max_file_upload_size_mb = 1
    try:
        yield BYTES_PER_MIB
    finally:
        config_manager.app_settings.max_file_upload_size_mb = original


def test_returns_none_when_files_not_a_dict(limit_1mb):
    assert find_oversized_inline_file(None) is None
    assert find_oversized_inline_file([]) is None
    assert find_oversized_inline_file("not-a-dict") is None


def test_string_shaped_payload_within_limit(limit_1mb):
    files = {"small.txt": _b64(1024)}
    assert find_oversized_inline_file(files) is None


def test_string_shaped_payload_oversized(limit_1mb):
    files = {"big.txt": _b64(limit_1mb + 1)}
    result = find_oversized_inline_file(files)
    assert result is not None
    filename, size = result
    assert filename == "big.txt"
    assert size > limit_1mb


def test_dict_shaped_payload_oversized(limit_1mb):
    files = {"big.bin": {"content": _b64(limit_1mb + 1)}}
    result = find_oversized_inline_file(files)
    assert result is not None
    assert result[0] == "big.bin"


def test_dict_shaped_payload_within_limit(limit_1mb):
    files = {"ok.bin": {"content": _b64(2048)}}
    assert find_oversized_inline_file(files) is None


def test_boundary_exactly_at_limit_is_allowed(limit_1mb):
    """A file decoding to exactly the limit is not oversized (strict >)."""
    files = {"exact.bin": _b64(limit_1mb)}
    assert find_oversized_inline_file(files) is None


def test_malformed_payload_raises_400(limit_1mb):
    files = {"weird.bin": 12345}  # neither str nor dict
    with pytest.raises(HTTPException) as exc:
        find_oversized_inline_file(files)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid base64 content"


def test_missing_content_key_treated_as_empty(limit_1mb):
    """A dict payload without a 'content' key decodes to 0 bytes, not oversized."""
    files = {"empty.bin": {"not_content": "abc"}}
    assert find_oversized_inline_file(files) is None


def test_first_oversized_file_is_reported(limit_1mb):
    files = {
        "small.txt": _b64(512),
        "big.bin": _b64(limit_1mb + 1),
    }
    result = find_oversized_inline_file(files)
    assert result is not None
    assert result[0] == "big.bin"
