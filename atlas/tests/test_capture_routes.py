"""Tests for fine-tune capture HTTP routes (issue #622).

Verifies consent get/set, the system-disabled guard, admin gating on the
stats/export endpoints, and user self-delete.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.application.chat.capture.capture_service import CaptureService
from atlas.application.chat.capture.capture_store import CaptureStore

USER_HEADERS = {"X-User-Email": "test@test.com"}
ADMIN_HEADERS = {"X-User-Email": "admin@test.com"}


def _config(system_enabled=True):
    return SimpleNamespace(
        app_settings=SimpleNamespace(
            feature_finetune_capture_enabled=system_enabled,
            runtime_capture_dir=None,
            capture_user_salt="routes-test-salt",
            admin_group="admin",
        )
    )


@pytest.fixture
def patched_service():
    """Patch the route factory to use a temp-backed service. Yields the service."""
    with tempfile.TemporaryDirectory() as tmp:
        store = CaptureStore(Path(tmp), user_salt="routes-test-salt")
        holder = {"service": CaptureService(_config(True), store=store)}

        def _factory():
            return holder["service"]

        with patch("atlas.routes.capture_routes._get_capture_service", _factory):
            yield holder


@pytest.fixture
def mock_admin():
    async def _is_user_in_group(user: str, group: str) -> bool:
        return user == "admin@test.com"

    with patch("atlas.routes.capture_routes.is_user_in_group", _is_user_in_group):
        yield


class TestConsent:
    def test_get_consent_defaults_off(self, patched_service):
        client = TestClient(app)
        resp = client.get("/api/capture/consent", headers=USER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_enabled"] is False
        assert body["system_enabled"] is True

    def test_opt_in_then_out(self, patched_service):
        client = TestClient(app)
        resp = client.post(
            "/api/capture/consent", json={"enabled": True}, headers=USER_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["user_enabled"] is True

        resp = client.post(
            "/api/capture/consent", json={"enabled": False}, headers=USER_HEADERS
        )
        assert resp.json()["user_enabled"] is False

    def test_opt_in_rejected_when_system_disabled(self, patched_service):
        store = patched_service["service"].store
        patched_service["service"] = CaptureService(_config(False), store=store)
        client = TestClient(app)
        resp = client.post(
            "/api/capture/consent", json={"enabled": True}, headers=USER_HEADERS
        )
        assert resp.status_code == 409


class TestSelfDelete:
    def test_delete_my_data(self, patched_service):
        service = patched_service["service"]
        service.set_consent("test@test.com", True)
        client = TestClient(app)
        resp = client.delete("/api/capture/me", headers=USER_HEADERS)
        assert resp.status_code == 200
        assert "deleted_records" in resp.json()


class TestAdminGating:
    def test_stats_requires_admin(self, patched_service, mock_admin):
        client = TestClient(app)
        assert client.get("/api/admin/capture/stats", headers=USER_HEADERS).status_code == 403
        ok = client.get("/api/admin/capture/stats", headers=ADMIN_HEADERS)
        assert ok.status_code == 200
        assert "total_records" in ok.json()

    def test_export_requires_admin(self, patched_service, mock_admin):
        client = TestClient(app)
        assert client.get("/api/admin/capture/export", headers=USER_HEADERS).status_code == 403
        ok = client.get("/api/admin/capture/export", headers=ADMIN_HEADERS)
        assert ok.status_code == 200
