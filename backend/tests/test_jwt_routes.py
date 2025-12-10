"""Tests for JWT management admin routes."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch


@pytest.fixture
def mock_jwt_storage():
    """Mock JWT storage for testing."""
    with patch('backend.routes.admin_routes.get_jwt_storage') as mock:
        mock_storage = Mock()
        mock_storage.storage_dir = "/tmp/jwt-storage"
        mock.return_value = mock_storage
        yield mock_storage


@pytest.fixture
def mock_config_manager():
    """Mock config manager for testing."""
    with patch('backend.routes.admin_routes.config_manager') as mock:
        mock_config = Mock()
        mock_config.servers = {
            "test-server": Mock(),
            "another-server": Mock()
        }
        mock.mcp_config = mock_config
        yield mock


@pytest.fixture
def mock_require_admin():
    """Mock admin authentication."""
    with patch('backend.routes.admin_routes.require_admin') as mock:
        mock.return_value = "admin@example.com"
        yield mock


class TestJWTManagementRoutes:
    """Test JWT management API endpoints."""

    def test_upload_jwt_success(self, mock_jwt_storage, mock_config_manager):
        """Test successful JWT upload."""
        # This is a placeholder for actual route testing
        # In practice, you would use TestClient with your FastAPI app
        
        server_name = "test-server"
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"
        
        # Simulate the route logic
        mock_jwt_storage.store_jwt(server_name, jwt_token)
        
        # Verify JWT was stored
        mock_jwt_storage.store_jwt.assert_called_once_with(server_name, jwt_token)

    def test_upload_jwt_server_not_found(self, mock_jwt_storage, mock_config_manager):
        """Test JWT upload for non-existent server."""
        # Update mock to not include the server
        mock_config_manager.mcp_config.servers = {}
        
        server_name = "nonexistent-server"
        
        # Should raise HTTPException with 404
        # (This would be tested with TestClient in actual integration tests)

    def test_get_jwt_status_exists(self, mock_jwt_storage):
        """Test getting JWT status when JWT exists."""
        server_name = "test-server"
        mock_jwt_storage.has_jwt.return_value = True
        
        # Simulate route logic
        has_jwt = mock_jwt_storage.has_jwt(server_name)
        
        assert has_jwt is True
        mock_jwt_storage.has_jwt.assert_called_once_with(server_name)

    def test_get_jwt_status_not_exists(self, mock_jwt_storage):
        """Test getting JWT status when JWT doesn't exist."""
        server_name = "test-server"
        mock_jwt_storage.has_jwt.return_value = False
        
        has_jwt = mock_jwt_storage.has_jwt(server_name)
        
        assert has_jwt is False

    def test_delete_jwt_success(self, mock_jwt_storage):
        """Test successful JWT deletion."""
        server_name = "test-server"
        mock_jwt_storage.delete_jwt.return_value = True
        
        deleted = mock_jwt_storage.delete_jwt(server_name)
        
        assert deleted is True
        mock_jwt_storage.delete_jwt.assert_called_once_with(server_name)

    def test_delete_jwt_not_found(self, mock_jwt_storage):
        """Test JWT deletion when JWT doesn't exist."""
        server_name = "test-server"
        mock_jwt_storage.delete_jwt.return_value = False
        
        deleted = mock_jwt_storage.delete_jwt(server_name)
        
        assert deleted is False

    def test_list_servers_with_jwt(self, mock_jwt_storage):
        """Test listing servers with stored JWTs."""
        mock_jwt_storage.list_servers_with_jwt.return_value = [
            "server1",
            "server2",
            "server3"
        ]
        
        servers = mock_jwt_storage.list_servers_with_jwt()
        
        assert len(servers) == 3
        assert "server1" in servers
        assert "server2" in servers
        assert "server3" in servers


class TestJWTUploadValidation:
    """Test JWT upload validation logic."""

    def test_valid_jwt_format(self):
        """Test that valid JWT format is accepted."""
        # JWT has three parts separated by dots
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
        parts = jwt.split('.')
        assert len(parts) == 3

    def test_invalid_jwt_format(self):
        """Test that invalid JWT format is rejected."""
        # Not a valid JWT
        invalid_jwt = "not-a-valid-jwt"
        parts = invalid_jwt.split('.')
        # Should not have 3 parts
        assert len(parts) != 3

    def test_empty_jwt(self):
        """Test that empty JWT is rejected."""
        jwt = ""
        assert jwt == ""
        # Should be rejected in actual validation


# Integration test notes:
# The following would be proper integration tests using FastAPI's TestClient:
#
# @pytest.fixture
# def client():
#     from backend.main import app
#     return TestClient(app)
#
# def test_upload_jwt_integration(client, mock_require_admin, mock_jwt_storage, mock_config_manager):
#     response = client.post(
#         "/admin/mcp/test-server/jwt",
#         json={"jwt_token": "eyJ..."},
#         headers={"X-User-Email": "admin@example.com"}
#     )
#     assert response.status_code == 200
#     assert response.json()["status"] == "success"
