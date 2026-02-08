"""Unit tests for MCP authentication routes.

Tests the API endpoints for per-user token management:
- GET /api/mcp/auth/status - Get auth status for all servers
- POST /api/mcp/auth/{server}/token - Upload token for server
- DELETE /api/mcp/auth/{server}/token - Remove token for server

Updated: 2025-01-21
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.core.log_sanitizer import get_current_user
from atlas.routes.mcp_auth_routes import TokenUpload, router


# Create a test app with the auth routes
def create_test_app(user_override: str = "test@example.com"):
    """Create a FastAPI test app with auth routes."""
    app = FastAPI()
    app.include_router(router)

    # Override the get_current_user dependency
    async def override_get_current_user():
        return user_override

    app.dependency_overrides[get_current_user] = override_get_current_user
    return app


class TestGetAuthStatus:
    """Test GET /api/mcp/auth/status endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_dependencies(self):
        """Mock the dependencies for auth routes."""
        with patch("atlas.routes.mcp_auth_routes.app_factory") as mock_factory, \
             patch("atlas.routes.mcp_auth_routes.get_token_storage") as mock_storage:

            # Mock MCP manager
            mock_mcp_manager = AsyncMock()
            mock_mcp_manager.get_authorized_servers = AsyncMock(return_value=["server1", "server2"])
            mock_mcp_manager.servers_config = {
                "server1": {"auth_type": "api_key", "description": "API Key Server"},
                "server2": {"auth_type": "jwt", "description": "JWT Server"},
            }
            mock_factory.get_mcp_manager.return_value = mock_mcp_manager

            # Mock token storage
            mock_token_storage = MagicMock()
            mock_token_storage.get_user_auth_status.return_value = {
                "server1": {
                    "token_type": "api_key",
                    "is_expired": False,
                    "expires_at": None,
                    "time_until_expiry": None,
                    "has_refresh_token": False,
                    "scopes": None,
                }
            }
            mock_storage.return_value = mock_token_storage

            yield {
                "factory": mock_factory,
                "storage": mock_storage,
                "mcp_manager": mock_mcp_manager,
                "token_storage": mock_token_storage,
            }

    def test_get_auth_status_success(self, client, mock_dependencies):
        """Should return auth status for all servers."""
        response = client.get("/api/mcp/auth/status")

        assert response.status_code == 200
        data = response.json()

        assert "servers" in data
        assert "user" in data
        assert data["user"] == "test@example.com"
        assert len(data["servers"]) == 2

    def test_get_auth_status_shows_authenticated_servers(self, client, mock_dependencies):
        """Should indicate which servers user is authenticated with."""
        response = client.get("/api/mcp/auth/status")

        data = response.json()
        servers = {s["server_name"]: s for s in data["servers"]}

        # server1 has token stored
        assert servers["server1"]["authenticated"] is True
        assert servers["server1"]["auth_type"] == "api_key"

        # server2 has no token
        assert servers["server2"]["authenticated"] is False
        assert servers["server2"]["auth_type"] == "jwt"

    def test_get_auth_status_includes_token_details(self, client, mock_dependencies):
        """Should include token details for authenticated servers."""
        response = client.get("/api/mcp/auth/status")

        data = response.json()
        server1 = next(s for s in data["servers"] if s["server_name"] == "server1")

        assert server1["token_type"] == "api_key"
        assert server1["is_expired"] is False


class TestUploadToken:
    """Test POST /api/mcp/auth/{server_name}/token endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_dependencies(self):
        """Mock the dependencies for auth routes."""
        with patch("atlas.routes.mcp_auth_routes.app_factory") as mock_factory, \
             patch("atlas.routes.mcp_auth_routes.get_token_storage") as mock_storage:

            mock_mcp_manager = AsyncMock()
            mock_mcp_manager.get_authorized_servers = AsyncMock(return_value=["test-server"])
            mock_mcp_manager.servers_config = {
                "test-server": {"auth_type": "api_key", "description": "Test Server"},
            }
            mock_factory.get_mcp_manager.return_value = mock_mcp_manager

            mock_token_storage = MagicMock()
            mock_stored_token = MagicMock()
            mock_stored_token.token_type = "api_key"
            mock_stored_token.expires_at = None
            mock_stored_token.scopes = None
            mock_token_storage.store_token.return_value = mock_stored_token
            mock_storage.return_value = mock_token_storage

            yield {
                "factory": mock_factory,
                "storage": mock_storage,
                "mcp_manager": mock_mcp_manager,
                "token_storage": mock_token_storage,
            }

    def test_upload_token_success(self, client, mock_dependencies):
        """Should store token successfully."""
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "my-api-key-123"}
        )

        assert response.status_code == 200
        data = response.json()

        assert data["message"] == "Token stored for server 'test-server'"
        assert data["server_name"] == "test-server"
        assert data["token_type"] == "api_key"

    def test_upload_token_with_expiry(self, client, mock_dependencies):
        """Should store token with expiration time."""
        expiry = time.time() + 3600
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "my-api-key", "expires_at": expiry}
        )

        assert response.status_code == 200
        mock_dependencies["token_storage"].store_token.assert_called_once()
        call_args = mock_dependencies["token_storage"].store_token.call_args
        assert call_args.kwargs["expires_at"] == expiry

    def test_upload_token_with_scopes(self, client, mock_dependencies):
        """Should store token with scopes."""
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "my-api-key", "scopes": "read write"}
        )

        assert response.status_code == 200
        mock_dependencies["token_storage"].store_token.assert_called_once()
        call_args = mock_dependencies["token_storage"].store_token.call_args
        assert call_args.kwargs["scopes"] == "read write"

    def test_upload_token_empty_rejected(self, client, mock_dependencies):
        """Should reject empty token."""
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": ""}
        )

        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_upload_token_whitespace_only_rejected(self, client, mock_dependencies):
        """Should reject whitespace-only token."""
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "   "}
        )

        assert response.status_code == 400

    def test_upload_token_unauthorized_server(self, client, mock_dependencies):
        """Should reject token for unauthorized server."""
        response = client.post(
            "/api/mcp/auth/unauthorized-server/token",
            json={"token": "my-api-key"}
        )

        assert response.status_code == 403
        assert "Not authorized" in response.json()["detail"]

    def test_upload_token_wrong_auth_type(self, client, mock_dependencies):
        """Should reject token for server with auth_type=none."""
        mock_dependencies["mcp_manager"].servers_config["test-server"]["auth_type"] = "none"

        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "my-api-key"}
        )

        assert response.status_code == 400
        assert "does not accept token authentication" in response.json()["detail"]

    def test_upload_token_strips_whitespace(self, client, mock_dependencies):
        """Should strip whitespace from token."""
        response = client.post(
            "/api/mcp/auth/test-server/token",
            json={"token": "  my-api-key  "}
        )

        assert response.status_code == 200
        call_args = mock_dependencies["token_storage"].store_token.call_args
        assert call_args.kwargs["token_value"] == "my-api-key"


class TestRemoveToken:
    """Test DELETE /api/mcp/auth/{server_name}/token endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_test_app()
        return TestClient(app)

    @pytest.fixture
    def mock_dependencies(self):
        """Mock the dependencies for auth routes."""
        with patch("atlas.routes.mcp_auth_routes.get_token_storage") as mock_storage, \
             patch("atlas.routes.mcp_auth_routes.app_factory") as mock_factory:

            mock_token_storage = MagicMock()
            mock_token_storage.remove_token.return_value = True
            mock_storage.return_value = mock_token_storage

            # Mock tool manager for cache invalidation
            mock_tool_manager = AsyncMock()
            mock_tool_manager._invalidate_user_client = AsyncMock()
            mock_factory.get_mcp_manager.return_value = mock_tool_manager

            yield {
                "storage": mock_storage,
                "token_storage": mock_token_storage,
                "factory": mock_factory,
                "tool_manager": mock_tool_manager,
            }

    def test_remove_token_success(self, client, mock_dependencies):
        """Should remove token successfully."""
        response = client.delete("/api/mcp/auth/test-server/token")

        assert response.status_code == 200
        data = response.json()

        assert data["message"] == "Token removed for server 'test-server'"
        assert data["server_name"] == "test-server"

    def test_remove_token_invalidates_cache(self, client, mock_dependencies):
        """Should invalidate cached client when token is removed."""
        response = client.delete("/api/mcp/auth/test-server/token")

        assert response.status_code == 200
        # Verify cache invalidation was called
        mock_dependencies["tool_manager"]._invalidate_user_client.assert_called_once_with(
            "test@example.com", "test-server"
        )

    def test_remove_token_not_found(self, client, mock_dependencies):
        """Should return 404 when token doesn't exist."""
        mock_dependencies["token_storage"].remove_token.return_value = False

        response = client.delete("/api/mcp/auth/nonexistent-server/token")

        assert response.status_code == 404
        assert "No token found" in response.json()["detail"]


class TestTokenUploadModel:
    """Test TokenUpload Pydantic model."""

    def test_token_required(self):
        """Token field should be required."""
        with pytest.raises(Exception):
            TokenUpload()

    def test_token_accepts_string(self):
        """Token field should accept string."""
        model = TokenUpload(token="my-api-key")
        assert model.token == "my-api-key"

    def test_expires_at_optional(self):
        """expires_at should be optional."""
        model = TokenUpload(token="my-api-key")
        assert model.expires_at is None

    def test_expires_at_accepts_float(self):
        """expires_at should accept float timestamp."""
        expiry = time.time() + 3600
        model = TokenUpload(token="my-api-key", expires_at=expiry)
        assert model.expires_at == expiry

    def test_scopes_optional(self):
        """scopes should be optional."""
        model = TokenUpload(token="my-api-key")
        assert model.scopes is None

    def test_scopes_accepts_string(self):
        """scopes should accept space-separated string."""
        model = TokenUpload(token="my-api-key", scopes="read write admin")
        assert model.scopes == "read write admin"
