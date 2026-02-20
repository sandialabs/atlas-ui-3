"""Unit tests for health check and heartbeat endpoints."""

from datetime import datetime

from main import app
from starlette.testclient import TestClient


def test_heartbeat_endpoint_returns_200():
    """Test that heartbeat endpoint returns 200 status."""
    client = TestClient(app)
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200


def test_heartbeat_endpoint_no_auth_required():
    """Test that heartbeat endpoint works without authentication."""
    client = TestClient(app)
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200


def test_heartbeat_endpoint_response_structure():
    """Test that heartbeat endpoint returns minimal response."""
    client = TestClient(app)
    resp = client.get("/api/heartbeat")
    assert resp.status_code == 200

    data = resp.json()
    assert data == {"status": "ok"}


def test_health_endpoint_returns_200():
    """Test that health endpoint returns 200 status."""
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_endpoint_no_auth_required():
    """Test that health endpoint works without authentication."""
    client = TestClient(app)
    # No X-User-Email header provided
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_endpoint_response_structure():
    """Test that health endpoint returns correct response structure."""
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200

    data = resp.json()

    # Verify all required fields are present
    assert "status" in data
    assert "service" in data
    assert "version" in data
    assert "timestamp" in data

    # Verify field values
    assert data["status"] == "healthy"
    assert data["service"] == "atlas-ui-3-backend"

    # This version number can change, so just check it's a non-empty string
    assert isinstance(data["version"], str) and len(data["version"]) > 0

    # Verify timestamp is valid ISO-8601 format
    try:
        datetime.fromisoformat(data["timestamp"])
    except ValueError:
        assert False, f"Invalid timestamp format: {data['timestamp']}"
