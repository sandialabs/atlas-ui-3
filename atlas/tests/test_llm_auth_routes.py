"""Unit tests for LLM authentication routes.

Tests the API endpoints for per-user LLM API key management:
- GET /api/llm/auth/status - Get auth status for user-key models
- POST /api/llm/auth/{model}/token - Upload API key for model
- DELETE /api/llm/auth/{model}/token - Remove API key for model

Updated: 2026-02-08
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.core.log_sanitizer import get_current_user
from atlas.routes.llm_auth_routes import LLMTokenUpload, router


def create_test_app(user_override: str = "test@example.com"):
    """Create a FastAPI test app with LLM auth routes."""
    app = FastAPI()
    app.include_router(router)

    async def override_get_current_user():
        return user_override

    app.dependency_overrides[get_current_user] = override_get_current_user
    return app


def _mock_llm_config(models_dict):
    """Build a mock LLMConfig from a dict of model_name -> ModelConfig-like objects."""
    mock_config = MagicMock()
    mock_models = {}
    for name, attrs in models_dict.items():
        m = MagicMock()
        m.description = attrs.get("description", "")
        m.api_key_source = attrs.get("api_key_source", "system")
        mock_models[name] = m
    mock_config.models = mock_models
    return mock_config


class TestGetLLMAuthStatus:
    """Test GET /api/llm/auth/status endpoint."""

    @pytest.fixture
    def client(self):
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_deps(self):
        with patch("atlas.routes.llm_auth_routes.app_factory") as mock_factory, \
             patch("atlas.routes.llm_auth_routes.get_token_storage") as mock_storage:

            llm_config = _mock_llm_config({
                "system-model": {"api_key_source": "system"},
                "user-model": {"api_key_source": "user", "description": "Bring your own key"},
            })
            mock_cm = MagicMock()
            mock_cm.llm_config = llm_config
            mock_factory.get_config_manager.return_value = mock_cm

            mock_ts = MagicMock()
            mock_ts.get_token.return_value = None
            mock_storage.return_value = mock_ts

            yield {
                "factory": mock_factory,
                "token_storage": mock_ts,
                "config_manager": mock_cm,
            }

    def test_returns_only_user_key_models(self, client, mock_deps):
        """Should only return models with api_key_source=user."""
        response = client.get("/api/llm/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert len(data["models"]) == 1
        assert data["models"][0]["model_name"] == "user-model"

    def test_shows_unauthenticated_when_no_token(self, client, mock_deps):
        """Should show authenticated=False when user has no key."""
        response = client.get("/api/llm/auth/status")
        data = response.json()
        assert data["models"][0]["authenticated"] is False

    def test_shows_authenticated_when_token_exists(self, client, mock_deps):
        """Should show authenticated=True when user has a stored key."""
        mock_token = MagicMock()
        mock_token.is_expired.return_value = False
        mock_token.expires_at = None
        mock_deps["token_storage"].get_token.return_value = mock_token

        response = client.get("/api/llm/auth/status")
        data = response.json()
        assert data["models"][0]["authenticated"] is True
        assert data["models"][0]["is_expired"] is False


class TestUploadLLMToken:
    """Test POST /api/llm/auth/{model}/token endpoint."""

    @pytest.fixture
    def client(self):
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_deps(self):
        with patch("atlas.routes.llm_auth_routes.app_factory") as mock_factory, \
             patch("atlas.routes.llm_auth_routes.get_token_storage") as mock_storage:

            llm_config = _mock_llm_config({
                "user-model": {"api_key_source": "user"},
                "system-model": {"api_key_source": "system"},
            })
            mock_cm = MagicMock()
            mock_cm.llm_config = llm_config
            mock_factory.get_config_manager.return_value = mock_cm

            mock_ts = MagicMock()
            mock_stored = MagicMock()
            mock_stored.expires_at = None
            mock_ts.store_token.return_value = mock_stored
            mock_storage.return_value = mock_ts

            yield {
                "factory": mock_factory,
                "token_storage": mock_ts,
            }

    def test_upload_success(self, client, mock_deps):
        """Should store API key successfully."""
        response = client.post(
            "/api/llm/auth/user-model/token",
            json={"token": "sk-abc123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model_name"] == "user-model"
        mock_deps["token_storage"].store_token.assert_called_once()

    def test_upload_stores_with_llm_prefix(self, client, mock_deps):
        """Should use llm: prefix in storage key."""
        client.post(
            "/api/llm/auth/user-model/token",
            json={"token": "sk-abc123"}
        )
        call_kwargs = mock_deps["token_storage"].store_token.call_args.kwargs
        assert call_kwargs["server_name"] == "llm:user-model"
        assert call_kwargs["token_value"] == "sk-abc123"
        assert call_kwargs["token_type"] == "api_key"

    def test_upload_with_expiry(self, client, mock_deps):
        """Should store token with expiration."""
        expiry = time.time() + 3600
        response = client.post(
            "/api/llm/auth/user-model/token",
            json={"token": "sk-abc123", "expires_at": expiry}
        )
        assert response.status_code == 200
        call_kwargs = mock_deps["token_storage"].store_token.call_args.kwargs
        assert call_kwargs["expires_at"] == expiry

    def test_upload_empty_rejected(self, client, mock_deps):
        """Should reject empty API key."""
        response = client.post(
            "/api/llm/auth/user-model/token",
            json={"token": ""}
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_upload_whitespace_only_rejected(self, client, mock_deps):
        """Should reject whitespace-only key."""
        response = client.post(
            "/api/llm/auth/user-model/token",
            json={"token": "   "}
        )
        assert response.status_code == 400

    def test_upload_model_not_found(self, client, mock_deps):
        """Should return 404 for unknown model."""
        response = client.post(
            "/api/llm/auth/nonexistent/token",
            json={"token": "sk-abc123"}
        )
        assert response.status_code == 404

    def test_upload_rejected_for_system_model(self, client, mock_deps):
        """Should reject upload for system-key model."""
        response = client.post(
            "/api/llm/auth/system-model/token",
            json={"token": "sk-abc123"}
        )
        assert response.status_code == 400
        assert "does not accept per-user" in response.json()["detail"]

    def test_upload_strips_whitespace(self, client, mock_deps):
        """Should strip whitespace from token."""
        client.post(
            "/api/llm/auth/user-model/token",
            json={"token": "  sk-abc123  "}
        )
        call_kwargs = mock_deps["token_storage"].store_token.call_args.kwargs
        assert call_kwargs["token_value"] == "sk-abc123"


class TestRemoveLLMToken:
    """Test DELETE /api/llm/auth/{model}/token endpoint."""

    @pytest.fixture
    def client(self):
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_deps(self):
        with patch("atlas.routes.llm_auth_routes.get_token_storage") as mock_storage:
            mock_ts = MagicMock()
            mock_ts.remove_token.return_value = True
            mock_storage.return_value = mock_ts

            yield {"token_storage": mock_ts}

    def test_remove_success(self, client, mock_deps):
        """Should remove token successfully."""
        response = client.delete("/api/llm/auth/user-model/token")
        assert response.status_code == 200
        data = response.json()
        assert data["model_name"] == "user-model"

    def test_remove_uses_llm_prefix(self, client, mock_deps):
        """Should use llm: prefix when removing."""
        client.delete("/api/llm/auth/user-model/token")
        mock_deps["token_storage"].remove_token.assert_called_once_with(
            "test@example.com", "llm:user-model"
        )

    def test_remove_not_found(self, client, mock_deps):
        """Should return 404 when no key exists."""
        mock_deps["token_storage"].remove_token.return_value = False
        response = client.delete("/api/llm/auth/nonexistent/token")
        assert response.status_code == 404


class TestLLMTokenUploadModel:
    """Test LLMTokenUpload Pydantic model."""

    def test_token_required(self):
        with pytest.raises(Exception):
            LLMTokenUpload()

    def test_token_accepts_string(self):
        model = LLMTokenUpload(token="sk-abc123")
        assert model.token == "sk-abc123"

    def test_expires_at_optional(self):
        model = LLMTokenUpload(token="sk-abc123")
        assert model.expires_at is None

    def test_expires_at_accepts_float(self):
        expiry = time.time() + 3600
        model = LLMTokenUpload(token="sk-abc123", expires_at=expiry)
        assert model.expires_at == expiry
