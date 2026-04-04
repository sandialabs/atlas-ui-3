"""Tests for help content (markdown) endpoints introduced in PR #495.

Covers:
- /api/config returns help_content as a string
- GET /admin/help-config returns content from help.md (or legacy fallback)
- PUT /admin/help-config writes content, enforces size limit
- Auth enforcement on admin endpoints
- help-images static serving (including path-traversal rejection)
"""

import pytest
from main import app
from starlette.testclient import TestClient


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point admin-config file writes at a tmp dir so tests don't pollute the repo.

    Disables the default-seeding step too, so the tmp dir truly starts empty.
    """
    from atlas.routes import admin_routes

    monkeypatch.setattr(
        admin_routes.config_manager.app_settings, "app_config_dir", str(tmp_path)
    )
    monkeypatch.setattr(
        admin_routes.config_manager.app_settings, "help_config_file", "help.md"
    )
    monkeypatch.setattr(admin_routes, "setup_config_dir", lambda: None)
    return tmp_path


# ---------------------------------------------------------------------------
# /api/config — help_content field
# ---------------------------------------------------------------------------

def test_config_returns_help_content_as_string():
    """help_content must be present and be a string (not a dict/list)."""
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert "help_content" in data, "Response missing help_content field"
    assert isinstance(data["help_content"], str), "help_content should be a string"


def test_config_help_content_not_empty_with_default_file():
    """With the default help.md shipped in atlas/config/, help_content should not be empty."""
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    # The shipped help.md has real content
    assert len(data["help_content"]) > 0, "help_content is empty — default help.md may not be found"


# ---------------------------------------------------------------------------
# GET /admin/help-config
# ---------------------------------------------------------------------------

def test_admin_get_help_config_returns_content():
    """Admin GET should return content and file_path."""
    client = TestClient(app)
    resp = client.get("/admin/help-config", headers={"X-User-Email": "admin@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert isinstance(data["content"], str)
    assert "file_path" in data


def test_admin_get_help_config_requires_admin():
    """Non-admin users should be denied."""
    client = TestClient(app)
    resp = client.get("/admin/help-config", headers={"X-User-Email": "user@example.com"})
    assert resp.status_code in (302, 403)


# ---------------------------------------------------------------------------
# PUT /admin/help-config
# ---------------------------------------------------------------------------

def test_admin_put_help_config_writes_content(isolated_config_dir):
    """PUT should write content and return success."""
    client = TestClient(app)

    new_content = "# Updated Help\n\nThis is test content."
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={"content": new_content},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert "file_path" in data

    # Verify the content was written by reading it back
    resp2 = client.get("/admin/help-config", headers={"X-User-Email": "admin@example.com"})
    assert resp2.status_code == 200
    assert resp2.json()["content"] == new_content
    # And the file actually lives in the isolated tmp dir
    assert (isolated_config_dir / "help.md").read_text(encoding="utf-8") == new_content


def test_admin_put_help_config_requires_admin():
    """Non-admin users should be denied."""
    client = TestClient(app)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "user@example.com", "Content-Type": "application/json"},
        json={"content": "# Nope"},
    )
    assert resp.status_code in (302, 403)


def test_admin_put_help_config_rejects_oversized_content(isolated_config_dir):
    """Content exceeding 1 MB should be rejected with 413."""
    client = TestClient(app)
    oversized = "x" * (1_048_576 + 1)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={"content": oversized},
    )
    assert resp.status_code == 413


def test_admin_put_help_config_accepts_content_at_size_limit(isolated_config_dir):
    """Content exactly at the 1 MB limit should be accepted."""
    client = TestClient(app)
    at_limit = "x" * 1_048_576
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={"content": at_limit},
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("bad_content", [123, None, ["a", "b"], {"k": "v"}, True])
def test_admin_put_help_config_rejects_non_string_content(bad_content, isolated_config_dir):
    """Non-string 'content' field should be rejected with 400, not 500."""
    client = TestClient(app)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={"content": bad_content},
    )
    assert resp.status_code == 400
    assert "content" in resp.json()["detail"].lower()


def test_admin_put_help_config_missing_content_field_defaults_to_empty(isolated_config_dir):
    """Omitting 'content' should be treated as empty string (writes empty file)."""
    client = TestClient(app)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={},
    )
    assert resp.status_code == 200
    assert (isolated_config_dir / "help.md").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Legacy help-config.json fallback
# ---------------------------------------------------------------------------

def test_admin_get_help_config_legacy_fallback(isolated_config_dir):
    """When help.md is absent but legacy help-config.json exists, GET returns the legacy content.

    Regression test for a bug where get_admin_config_path() mapped both
    "help.md" and "help-config.json" to the same configured filename, so the
    fallback branch never pointed at a different file.
    """
    # Write only the legacy JSON, not help.md
    legacy_path = isolated_config_dir / "help-config.json"
    legacy_content = '{"title": "Legacy Help", "sections": []}'
    legacy_path.write_text(legacy_content, encoding="utf-8")

    client = TestClient(app)
    resp = client.get("/admin/help-config", headers={"X-User-Email": "admin@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == legacy_content
    assert data["file_path"].endswith("help-config.json")


def test_admin_get_help_config_returns_empty_when_nothing_exists(isolated_config_dir):
    """When neither help.md nor legacy help-config.json exist, GET returns empty content."""
    client = TestClient(app)
    resp = client.get("/admin/help-config", headers={"X-User-Email": "admin@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == ""
    assert data["file_path"].endswith("help.md")


# ---------------------------------------------------------------------------
# /help-images/* static serving
# ---------------------------------------------------------------------------

def test_help_image_serves_shipped_default():
    """The shipped chat-interface.png under atlas/config/help-images/ should be servable."""
    client = TestClient(app)
    resp = client.get(
        "/help-images/chat-interface.png",
        headers={"X-User-Email": "test@test.com"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("image/")


def test_help_image_missing_returns_404():
    """Requests for a non-existent image return 404."""
    client = TestClient(app)
    resp = client.get(
        "/help-images/definitely-not-here-xyz.png",
        headers={"X-User-Email": "test@test.com"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "traversal_path",
    [
        "../help.md",
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
    ],
)
def test_help_image_rejects_path_traversal(traversal_path):
    """Path traversal attempts must not escape the help-images directories."""
    client = TestClient(app)
    resp = client.get(
        f"/help-images/{traversal_path}",
        headers={"X-User-Email": "test@test.com"},
    )
    # Either 404 (traversal blocked by resolve()+relative_to()) or 400 from FastAPI —
    # must NOT be 200, which would mean we served a file outside the roots.
    assert resp.status_code != 200
