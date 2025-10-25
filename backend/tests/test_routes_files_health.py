import pytest
from starlette.testclient import TestClient

from main import app


def test_files_health_endpoint(monkeypatch):
    # Stub out S3 client call to avoid external dependency sensitivity
    from infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()
    # No network call is made by files/health; but ensure attributes exist
    assert hasattr(s3, "base_url")

    client = TestClient(app)
    resp = client.get("/api/files/healthz", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "files-api"
    assert "s3_config" in data
