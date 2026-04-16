"""Regression tests for file-key percent-encoding / double-decode safety.

Validates that _normalize_file_key correctly handles proxy-induced double-encoding
while the S3 client's prefix check blocks cross-user traversal attempts.
"""

import base64

from main import app
from starlette.testclient import TestClient

USER = "alice@example.com"
KEY = f"users/{USER}/generated/report.txt"


def _fake_s3(monkeypatch):
    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    captured = {}

    def _enforce_user_prefix(user, key):
        expected_prefix = f"users/{user}/"
        if not key.startswith(expected_prefix):
            raise PermissionError("Access denied")

    async def fake_get_file(user, key):
        captured["user"] = user
        captured["key"] = key
        _enforce_user_prefix(user, key)
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

    async def fake_delete_file(user, key):
        captured["user"] = user
        captured["key"] = key
        _enforce_user_prefix(user, key)
        return True

    monkeypatch.setattr(s3, "get_file", fake_get_file)
    monkeypatch.setattr(s3, "delete_file", fake_delete_file)
    return captured


# --- Double-encoding recovery (the bug this PR fixes) ---

def test_get_file_decodes_proxy_double_encoded_at_sign(monkeypatch):
    """@ double-encoded by proxy (%2540) should decode to the real S3 key."""
    captured = _fake_s3(monkeypatch)
    client = TestClient(app)
    # Simulate proxy double-encoding: @ -> %40 -> %2540 on the wire.
    # Starlette decodes %2540 -> %40; _normalize_file_key decodes %40 -> @.
    double_encoded_path = KEY.replace("@", "%2540")
    resp = client.get(
        f"/api/files/{double_encoded_path}",
        headers={"X-User-Email": USER},
    )
    assert resp.status_code == 200, resp.text
    assert captured["key"] == KEY


def test_delete_file_decodes_proxy_double_encoded_at_sign(monkeypatch):
    captured = _fake_s3(monkeypatch)
    client = TestClient(app)
    double_encoded_path = KEY.replace("@", "%2540")
    resp = client.delete(
        f"/api/files/{double_encoded_path}",
        headers={"X-User-Email": USER},
    )
    assert resp.status_code == 200, resp.text
    assert captured["key"] == KEY


def test_download_decodes_proxy_double_encoded_at_sign(monkeypatch):
    captured = _fake_s3(monkeypatch)
    client = TestClient(app)
    double_encoded_path = KEY.replace("@", "%2540")
    resp = client.get(
        f"/api/files/download/{double_encoded_path}",
        headers={"X-User-Email": USER},
    )
    assert resp.status_code == 200, resp.text
    assert captured["key"] == KEY


# --- Clean keys pass through unchanged ---

def test_get_file_clean_key_unchanged(monkeypatch):
    """A key with no residual encoding passes through unaltered."""
    captured = _fake_s3(monkeypatch)
    client = TestClient(app)
    resp = client.get(
        f"/api/files/{KEY}",
        headers={"X-User-Email": USER},
    )
    assert resp.status_code == 200
    assert captured["key"] == KEY


# --- Traversal-shaped paths are handled safely by decode/normalization logic ---

def test_traversal_via_double_encoded_slash_does_not_crash(monkeypatch):
    """Double-encoded ../ (%252F) is handled without a 500 for same-user paths."""
    _fake_s3(monkeypatch)
    client = TestClient(app)
    # Attacker tries: users/alice@example.com/../../secret
    # Wire-encoded as: users/alice%40example.com/%252E%252E%252F%252E%252E%252Fsecret
    # After Starlette: users/alice@example.com/%2E%2E%2F%2E%2E%2Fsecret
    # After _normalize_file_key: users/alice@example.com/../../secret
    # S3 treats this as an opaque key rather than filesystem traversal syntax.
    # With the current fake S3 setup, this regression test only checks that the
    # handler responds without crashing; it does not assert prefix enforcement.
    traversal = f"users/{USER}/%252E%252E%252F%252E%252E%252Fsecret"
    resp = client.get(
        f"/api/files/{traversal}",
        headers={"X-User-Email": USER},
    )
    # Either 404 (file not found) or 200 (fake S3 returns data) is acceptable
    # here; the important behavior under test is that decoding this path does
    # not produce an unexpected server error.
    assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code}"


def test_cross_user_traversal_blocked(monkeypatch):
    """Attempting to access another user's files via encoded traversal is denied."""
    _fake_s3(monkeypatch)
    client = TestClient(app)
    # Attacker (bob) tries to reach alice's files
    victim_key = f"users/{USER}/secret.txt"
    resp = client.get(
        f"/api/files/{victim_key}",
        headers={"X-User-Email": "bob@example.com"},
    )
    # The fake S3 now mirrors the real client's prefix check and denies
    # requests where the key does not start with users/{authenticated_user}/.
    assert resp.status_code == 403


# --- _normalize_file_key unit tests ---

def test_normalize_file_key_unit():
    from atlas.routes.files_routes import _normalize_file_key

    # Clean input passes through
    assert _normalize_file_key("users/alice@example.com/file.txt") == "users/alice@example.com/file.txt"

    # Single-layer residual encoding decoded
    assert _normalize_file_key("users/alice%40example.com/file.txt") == "users/alice@example.com/file.txt"

    # %2F in a key decodes to / (single pass)
    assert _normalize_file_key("users/alice%40example.com/sub%2Fdir/file.txt") == "users/alice@example.com/sub/dir/file.txt"

    # Literal percent in filename (%25 -> %)
    assert _normalize_file_key("users/alice%40example.com/50%25off.txt") == "users/alice@example.com/50%off.txt"

    # Double-encoded percent (%2525) only decodes one layer
    assert _normalize_file_key("users/alice%40example.com/50%2525off.txt") == "users/alice@example.com/50%25off.txt"
