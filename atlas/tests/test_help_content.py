"""Tests for help content (markdown) endpoints introduced in PR #495.

Covers:
- /api/config returns help_content as a string
- GET /admin/help-config returns content from help.md (or legacy fallback)
- PUT /admin/help-config writes content, enforces size limit
- Auth enforcement on admin endpoints
"""

import json
from pathlib import Path
from unittest.mock import patch

from main import app
from starlette.testclient import TestClient


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

def test_admin_put_help_config_writes_content(tmp_path):
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


def test_admin_put_help_config_requires_admin():
    """Non-admin users should be denied."""
    client = TestClient(app)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "user@example.com", "Content-Type": "application/json"},
        json={"content": "# Nope"},
    )
    assert resp.status_code in (302, 403)


def test_admin_put_help_config_rejects_oversized_content():
    """Content exceeding 1 MB should be rejected with 413."""
    client = TestClient(app)
    oversized = "x" * (1_048_576 + 1)
    resp = client.put(
        "/admin/help-config",
        headers={"X-User-Email": "admin@example.com", "Content-Type": "application/json"},
        json={"content": oversized},
    )
    assert resp.status_code == 413
