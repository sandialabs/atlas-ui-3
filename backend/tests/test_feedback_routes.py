"""Tests for feedback routes to prevent regression of issue #200.

Verifies that:
- POST /api/feedback is accessible to authenticated users
- GET /api/feedback requires admin group membership
- GET /api/feedback/stats requires admin group membership
- DELETE /api/feedback/{id} requires admin group membership
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from main import app


AUTH_HEADERS = {"X-User-Email": "test@test.com"}
ADMIN_HEADERS = {"X-User-Email": "admin@test.com"}


@pytest.fixture
def temp_feedback_dir():
    """Create a temporary directory for feedback files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_feedback_dir(temp_feedback_dir, monkeypatch):
    """Mock the feedback directory to use temp directory."""
    def mock_get_feedback_directory():
        return temp_feedback_dir
    
    monkeypatch.setattr(
        "routes.feedback_routes.get_feedback_directory",
        mock_get_feedback_directory
    )
    return temp_feedback_dir


@pytest.fixture
def mock_admin_check():
    """Mock admin group check to allow admin@test.com."""
    async def mock_is_user_in_group(user: str, group: str) -> bool:
        return user == "admin@test.com"
    
    with patch("routes.feedback_routes.is_user_in_group", mock_is_user_in_group):
        yield


class TestFeedbackRouteRegistration:
    """Test that feedback routes are properly registered (issue #200)."""

    def test_post_feedback_route_exists(self):
        """POST /api/feedback should not return 404."""
        client = TestClient(app)
        resp = client.post(
            "/api/feedback",
            json={"rating": 1, "comment": "test", "session": {}},
            headers=AUTH_HEADERS
        )
        assert resp.status_code != 404, "Feedback route not registered (issue #200)"

    def test_get_feedback_route_exists(self):
        """GET /api/feedback should not return 404."""
        client = TestClient(app)
        resp = client.get("/api/feedback", headers=AUTH_HEADERS)
        assert resp.status_code != 404, "Feedback route not registered (issue #200)"

    def test_get_feedback_stats_route_exists(self):
        """GET /api/feedback/stats should not return 404."""
        client = TestClient(app)
        resp = client.get("/api/feedback/stats", headers=AUTH_HEADERS)
        assert resp.status_code != 404, "Feedback stats route not registered (issue #200)"


class TestFeedbackSubmission:
    """Test feedback submission by regular users."""

    def test_submit_feedback_success(self, mock_feedback_dir, mock_admin_check):
        """Regular users can submit feedback."""
        client = TestClient(app)
        resp = client.post(
            "/api/feedback",
            json={"rating": 1, "comment": "Great service!", "session": {"model": "gpt-4"}},
            headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Feedback submitted successfully"
        assert "feedback_id" in data
        assert "timestamp" in data

    def test_submit_feedback_validates_rating(self, mock_feedback_dir, mock_admin_check):
        """Feedback submission validates rating values."""
        client = TestClient(app)
        resp = client.post(
            "/api/feedback",
            json={"rating": 5, "comment": "Invalid rating"},
            headers=AUTH_HEADERS
        )
        assert resp.status_code == 400
        assert "Rating must be -1, 0, or 1" in resp.json()["detail"]

    def test_submit_feedback_creates_file(self, mock_feedback_dir, mock_admin_check):
        """Feedback submission creates a JSON file."""
        client = TestClient(app)
        resp = client.post(
            "/api/feedback",
            json={"rating": 0, "comment": "Neutral feedback"},
            headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        
        feedback_files = list(mock_feedback_dir.glob("feedback_*.json"))
        assert len(feedback_files) == 1
        
        with open(feedback_files[0]) as f:
            saved_data = json.load(f)
        assert saved_data["rating"] == 0
        assert saved_data["comment"] == "Neutral feedback"
        assert saved_data["user"] == "test@test.com"


class TestFeedbackAdminAccess:
    """Test that viewing feedback requires admin access."""

    def test_get_feedback_requires_admin(self, mock_feedback_dir, mock_admin_check):
        """GET /api/feedback returns 403 for non-admin users."""
        client = TestClient(app)
        resp = client.get("/api/feedback", headers=AUTH_HEADERS)
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_get_feedback_stats_requires_admin(self, mock_feedback_dir, mock_admin_check):
        """GET /api/feedback/stats returns 403 for non-admin users."""
        client = TestClient(app)
        resp = client.get("/api/feedback/stats", headers=AUTH_HEADERS)
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_admin_can_view_feedback(self, mock_feedback_dir, mock_admin_check):
        """Admin users can view feedback list."""
        client = TestClient(app)
        
        client.post(
            "/api/feedback",
            json={"rating": 1, "comment": "Test"},
            headers=AUTH_HEADERS
        )
        
        resp = client.get("/api/feedback", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "feedback" in data
        assert "pagination" in data
        assert "statistics" in data

    def test_admin_can_view_stats(self, mock_feedback_dir, mock_admin_check):
        """Admin users can view feedback statistics."""
        client = TestClient(app)
        resp = client.get("/api/feedback/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_feedback" in data
        assert "rating_distribution" in data


class TestFeedbackDeletion:
    """Test feedback deletion by admin."""

    def test_delete_feedback_requires_admin(self, mock_feedback_dir, mock_admin_check):
        """DELETE /api/feedback/{id} returns 403 for non-admin users."""
        client = TestClient(app)
        resp = client.delete("/api/feedback/fake-id", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_admin_can_delete_feedback(self, mock_feedback_dir, mock_admin_check):
        """Admin users can delete feedback."""
        client = TestClient(app)
        
        resp = client.post(
            "/api/feedback",
            json={"rating": -1, "comment": "To be deleted"},
            headers=AUTH_HEADERS
        )
        feedback_id = resp.json()["feedback_id"]
        
        resp = client.delete(f"/api/feedback/{feedback_id}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Feedback deleted successfully"

    def test_delete_nonexistent_feedback_returns_404(self, mock_feedback_dir, mock_admin_check):
        """Deleting non-existent feedback returns 404."""
        client = TestClient(app)
        resp = client.delete("/api/feedback/nonexistent", headers=ADMIN_HEADERS)
        assert resp.status_code == 404
