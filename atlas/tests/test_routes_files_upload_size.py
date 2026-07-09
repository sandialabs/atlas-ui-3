import base64

from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory


def test_upload_file_rejects_configured_oversized_file():
    """POST /api/files should reject files larger than the configured limit."""
    config_manager = app_factory.get_config_manager()
    original_limit = config_manager.app_settings.max_file_upload_size_mb

    try:
        config_manager.app_settings.max_file_upload_size_mb = 1
        content = base64.b64encode(b"x" * ((1024 * 1024) + 1)).decode()

        client = TestClient(app)
        resp = client.post(
            "/api/files",
            headers={"X-User-Email": "test@test.com"},
            json={
                "filename": "large.txt",
                "content_base64": content,
                "content_type": "text/plain",
                "tags": {"source": "user"},
            },
        )

        assert resp.status_code == 413
        assert resp.json()["detail"] == "File too large. Maximum size is 1MB"
    finally:
        config_manager.app_settings.max_file_upload_size_mb = original_limit
