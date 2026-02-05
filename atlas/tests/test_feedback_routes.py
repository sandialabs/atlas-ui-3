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
from main import app
from starlette.testclient import TestClient

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
        "atlas.routes.feedback_routes.get_feedback_directory",
        mock_get_feedback_directory
    )
    return temp_feedback_dir


@pytest.fixture
def mock_admin_check():
    """Mock admin group check to allow admin@test.com."""
    async def mock_is_user_in_group(user: str, group: str) -> bool:
        return user == "admin@test.com"

    with patch("atlas.routes.feedback_routes.is_user_in_group", mock_is_user_in_group):
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


class TestFeedbackDownload:
    """Test feedback download functionality."""

    def test_download_feedback_requires_admin(self, mock_feedback_dir, mock_admin_check):
        """GET /api/feedback/download returns 403 for non-admin users."""
        client = TestClient(app)
        resp = client.get("/api/feedback/download", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_download_feedback_csv_format(self, mock_feedback_dir, mock_admin_check):
        """Admin users can download feedback as CSV."""
        client = TestClient(app)

        # Create some test feedback
        client.post(
            "/api/feedback",
            json={"rating": 1, "comment": "Great service!", "session": {"model": "gpt-4"}},
            headers=AUTH_HEADERS
        )
        client.post(
            "/api/feedback",
            json={"rating": -1, "comment": "Poor experience", "session": {"model": "gpt-3"}},
            headers=AUTH_HEADERS
        )

        # Download as CSV
        resp = client.get("/api/feedback/download?format=csv", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment; filename=" in resp.headers["content-disposition"]
        assert "feedback_export_" in resp.headers["content-disposition"]

        # Verify CSV content
        csv_content = resp.text
        lines = [line.strip() for line in csv_content.strip().split('\n') if line.strip()]
        assert len(lines) >= 3  # Header + 2 data rows

        # Check header
        assert lines[0] == "id,timestamp,user,rating,comment"

        # Check that feedback appears in CSV (order may vary)
        found_positive = any("1" in line and "Great service!" in line for line in lines[1:])
        found_negative = any("-1" in line and "Poor experience" in line for line in lines[1:])
        assert found_positive, "Positive feedback not found in CSV"
        assert found_negative, "Negative feedback not found in CSV"

    def test_download_feedback_json_format(self, mock_feedback_dir, mock_admin_check):
        """Admin users can download feedback as JSON."""
        client = TestClient(app)

        # Create test feedback
        resp1 = client.post(
            "/api/feedback",
            json={"rating": 1, "comment": "JSON test", "session": {"model": "gpt-4"}},
            headers=AUTH_HEADERS
        )
        feedback_id = resp1.json()["feedback_id"]

        # Download as JSON
        resp = client.get("/api/feedback/download?format=json", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert "attachment; filename=" in resp.headers["content-disposition"]
        assert "feedback_export_" in resp.headers["content-disposition"]

        # Verify JSON content
        json_data = resp.json()
        assert isinstance(json_data, list)
        assert len(json_data) == 1

        feedback = json_data[0]
        assert feedback["id"] == feedback_id
        assert feedback["rating"] == 1
        assert feedback["comment"] == "JSON test"
        assert feedback["user"] == "test@test.com"
        assert "timestamp" in feedback
        assert "session_info" in feedback
        assert "server_context" in feedback

    def test_download_feedback_empty_csv(self, mock_feedback_dir, mock_admin_check):
        """Downloading empty feedback as CSV returns header-only file."""
        client = TestClient(app)

        resp = client.get("/api/feedback/download?format=csv", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

        csv_content = resp.text
        lines = csv_content.strip().split('\n')
        assert len(lines) == 1  # Only header row
        assert lines[0] == "id,timestamp,user,rating,comment"

    def test_download_feedback_empty_json(self, mock_feedback_dir, mock_admin_check):
        """Downloading empty feedback as JSON returns empty array."""
        client = TestClient(app)

        resp = client.get("/api/feedback/download?format=json", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

        json_data = resp.json()
        assert json_data == []

    def test_download_feedback_csv_sanitizes_fields(self, mock_feedback_dir, mock_admin_check):
        """CSV download properly handles missing fields with defaults."""
        client = TestClient(app)

        # Clear any existing feedback files
        for f in mock_feedback_dir.glob("feedback_*.json"):
            f.unlink()

        # Manually create feedback file with missing fields (using correct naming pattern)
        import json
        feedback_file = mock_feedback_dir / "feedback_manual_test123.json"
        manual_feedback = {
            "id": "test123",
            "timestamp": "2026-01-10T12:00:00",
            "user": "test@example.com"
            # Missing rating, comment, session_info, server_context
        }
        with open(feedback_file, 'w') as f:
            json.dump(manual_feedback, f)

        resp = client.get("/api/feedback/download?format=csv", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

        csv_content = resp.text
        lines = csv_content.strip().split('\n')
        assert len(lines) == 2  # Header + 1 data row

        data_line = lines[1]
        assert "test123" in data_line
        assert "test@example.com" in data_line
        # Missing fields should be empty strings
