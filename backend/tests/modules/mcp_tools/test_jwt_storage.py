"""Tests for JWT storage functionality."""

import os
import pytest
from pathlib import Path
from cryptography.fernet import Fernet

from backend.modules.mcp_tools.jwt_storage import JWTStorage


class TestJWTStorage:
    """Test JWT storage with encryption."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        storage_dir = tmp_path / "jwt-storage"
        return str(storage_dir)

    @pytest.fixture
    def encryption_key(self):
        """Generate a test encryption key."""
        return Fernet.generate_key().decode()

    @pytest.fixture
    def jwt_storage(self, temp_storage_dir, encryption_key):
        """Create a JWT storage instance for testing."""
        return JWTStorage(storage_dir=temp_storage_dir, encryption_key=encryption_key)

    def test_store_and_retrieve_jwt(self, jwt_storage):
        """Test storing and retrieving a JWT."""
        server_name = "test-server"
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"

        # Store JWT
        jwt_storage.store_jwt(server_name, jwt_token)

        # Retrieve JWT
        retrieved_token = jwt_storage.get_jwt(server_name)
        assert retrieved_token == jwt_token

    def test_get_nonexistent_jwt(self, jwt_storage):
        """Test retrieving a JWT that doesn't exist."""
        result = jwt_storage.get_jwt("nonexistent-server")
        assert result is None

    def test_has_jwt(self, jwt_storage):
        """Test checking if JWT exists."""
        server_name = "test-server"
        jwt_token = "test-jwt-token"

        # Should not exist initially
        assert not jwt_storage.has_jwt(server_name)

        # Store JWT
        jwt_storage.store_jwt(server_name, jwt_token)

        # Should exist now
        assert jwt_storage.has_jwt(server_name)

    def test_delete_jwt(self, jwt_storage):
        """Test deleting a JWT."""
        server_name = "test-server"
        jwt_token = "test-jwt-token"

        # Store JWT
        jwt_storage.store_jwt(server_name, jwt_token)
        assert jwt_storage.has_jwt(server_name)

        # Delete JWT
        deleted = jwt_storage.delete_jwt(server_name)
        assert deleted is True
        assert not jwt_storage.has_jwt(server_name)

    def test_delete_nonexistent_jwt(self, jwt_storage):
        """Test deleting a JWT that doesn't exist."""
        deleted = jwt_storage.delete_jwt("nonexistent-server")
        assert deleted is False

    def test_list_servers_with_jwt(self, jwt_storage):
        """Test listing servers with stored JWTs."""
        # Initially empty
        servers = jwt_storage.list_servers_with_jwt()
        assert len(servers) == 0

        # Store JWTs for multiple servers
        jwt_storage.store_jwt("server1", "jwt1")
        jwt_storage.store_jwt("server2", "jwt2")
        jwt_storage.store_jwt("server3", "jwt3")

        # List should contain all three servers
        servers = jwt_storage.list_servers_with_jwt()
        assert len(servers) == 3
        assert "server1" in servers
        assert "server2" in servers
        assert "server3" in servers

    def test_encryption_integrity(self, temp_storage_dir, encryption_key):
        """Test that JWT files are actually encrypted."""
        jwt_storage = JWTStorage(storage_dir=temp_storage_dir, encryption_key=encryption_key)
        server_name = "test-server"
        jwt_token = "my-secret-jwt-token"

        # Store JWT
        jwt_storage.store_jwt(server_name, jwt_token)

        # Read raw file content
        jwt_path = jwt_storage._get_jwt_path(server_name)
        encrypted_content = jwt_path.read_bytes()

        # Encrypted content should not contain the original token
        assert jwt_token.encode() not in encrypted_content

        # Should be able to decrypt with the same key
        fernet = Fernet(encryption_key.encode())
        decrypted = fernet.decrypt(encrypted_content).decode()
        assert decrypted == jwt_token

    def test_wrong_encryption_key_fails(self, temp_storage_dir):
        """Test that using wrong encryption key fails to decrypt."""
        # Store with one key
        key1 = Fernet.generate_key().decode()
        storage1 = JWTStorage(storage_dir=temp_storage_dir, encryption_key=key1)
        storage1.store_jwt("test-server", "secret-token")

        # Try to retrieve with different key
        key2 = Fernet.generate_key().decode()
        storage2 = JWTStorage(storage_dir=temp_storage_dir, encryption_key=key2)
        retrieved = storage2.get_jwt("test-server")
        
        # Should fail to decrypt and return None
        assert retrieved is None

    def test_file_permissions(self, jwt_storage):
        """Test that JWT files have secure permissions."""
        server_name = "test-server"
        jwt_token = "test-jwt-token"

        # Store JWT
        jwt_storage.store_jwt(server_name, jwt_token)

        # Check file permissions
        jwt_path = jwt_storage._get_jwt_path(server_name)
        stat_info = jwt_path.stat()
        
        # On Unix systems, check that only owner has read/write
        # 0o600 = owner read/write only
        if os.name != 'nt':  # Skip on Windows
            assert oct(stat_info.st_mode)[-3:] == '600'

    def test_auto_generate_encryption_key(self, temp_storage_dir):
        """Test that encryption key is auto-generated if not provided."""
        # Don't provide encryption key
        storage = JWTStorage(storage_dir=temp_storage_dir)

        # Should have created a key file
        key_file = Path(temp_storage_dir) / ".encryption_key"
        assert key_file.exists()

        # Key should be valid Fernet key
        key_content = key_file.read_text().strip()
        fernet = Fernet(key_content.encode())  # Should not raise

        # Should be able to use the storage
        storage.store_jwt("test", "token")
        assert storage.get_jwt("test") == "token"

    def test_sanitize_server_name(self, jwt_storage):
        """Test that server names are sanitized for filesystem."""
        # Server name with special characters
        server_name = "my-server/with:special*chars"
        jwt_token = "test-token"

        # Should store without error
        jwt_storage.store_jwt(server_name, jwt_token)

        # Should retrieve correctly
        assert jwt_storage.get_jwt(server_name) == jwt_token

        # File path should have sanitized name
        jwt_path = jwt_storage._get_jwt_path(server_name)
        # Path should not contain special characters
        assert "/" not in jwt_path.name
        assert ":" not in jwt_path.name
        assert "*" not in jwt_path.name

    def test_environment_variable_encryption_key(self, temp_storage_dir, monkeypatch):
        """Test using encryption key from environment variable."""
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("JWT_STORAGE_ENCRYPTION_KEY", key)

        # Create storage without explicit key
        storage = JWTStorage(storage_dir=temp_storage_dir)

        # Should use the env var key
        storage.store_jwt("test", "token")
        assert storage.get_jwt("test") == "token"

        # Create another instance, should use same key
        storage2 = JWTStorage(storage_dir=temp_storage_dir)
        assert storage2.get_jwt("test") == "token"
