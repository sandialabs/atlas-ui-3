"""Unit tests for MCP token storage.

Tests the secure per-user token storage module including:
- Token encryption/decryption
- Token storage and retrieval
- Token expiration handling
- Per-user isolation
- Error handling

Updated: 2025-01-21
"""

import time
import tempfile
import pytest
from pathlib import Path

from backend.modules.mcp_tools.token_storage import (
    MCPTokenStorage,
    StoredToken,
    AuthenticationRequiredException,
    get_token_storage,
    _make_token_key,
    _parse_token_key,
)


class TestTokenKeyFunctions:
    """Test helper functions for token key management."""

    def test_make_token_key_basic(self):
        """Should create key from email and server name."""
        key = _make_token_key("user@example.com", "my-server")
        assert key == "user@example.com:my-server"

    def test_make_token_key_normalizes_case(self):
        """Should normalize email to lowercase."""
        key = _make_token_key("User@Example.COM", "My-Server")
        assert key == "user@example.com:My-Server"

    def test_parse_token_key_basic(self):
        """Should parse key into email and server name."""
        email, server = _parse_token_key("user@example.com:my-server")
        assert email == "user@example.com"
        assert server == "my-server"

    def test_parse_token_key_with_colons_in_server(self):
        """Should handle server names with colons (only splits on first colon)."""
        email, server = _parse_token_key("user@example.com:server:with:colons")
        assert email == "user@example.com"
        assert server == "server:with:colons"

    def test_parse_token_key_invalid_format(self):
        """Should raise ValueError for invalid key format."""
        with pytest.raises(ValueError, match="Invalid token key format"):
            _parse_token_key("no-colon-here")


class TestStoredToken:
    """Test StoredToken dataclass."""

    def test_stored_token_not_expired_no_expiry(self):
        """Token without expiry should never be expired."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time(),
            expires_at=None,
        )
        assert token.is_expired() is False

    def test_stored_token_not_expired_future_expiry(self):
        """Token with future expiry should not be expired."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time(),
            expires_at=time.time() + 3600,  # 1 hour in future
        )
        assert token.is_expired() is False

    def test_stored_token_expired_past_expiry(self):
        """Token with past expiry should be expired."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time() - 7200,
            expires_at=time.time() - 3600,  # 1 hour in past
        )
        assert token.is_expired() is True

    def test_stored_token_expired_within_buffer(self):
        """Token expiring within buffer should be considered expired."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time(),
            expires_at=time.time() + 30,  # 30 seconds in future
        )
        # With 60-second buffer, should be considered expired
        assert token.is_expired(buffer_seconds=60) is True
        # With 10-second buffer, should not be expired
        assert token.is_expired(buffer_seconds=10) is False

    def test_stored_token_time_until_expiry(self):
        """Should return correct time until expiry."""
        future_time = time.time() + 3600
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time(),
            expires_at=future_time,
        )
        # Should be approximately 3600 seconds (allow small margin)
        time_until = token.time_until_expiry()
        assert time_until is not None
        assert 3590 < time_until <= 3600

    def test_stored_token_time_until_expiry_none(self):
        """Should return None when no expiry set."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time(),
            expires_at=None,
        )
        assert token.time_until_expiry() is None

    def test_stored_token_time_until_expiry_past(self):
        """Should return 0 when already expired."""
        token = StoredToken(
            token_type="api_key",
            token_value="test-key",
            user_email="user@example.com",
            server_name="test-server",
            created_at=time.time() - 7200,
            expires_at=time.time() - 3600,
        )
        assert token.time_until_expiry() == 0


class TestAuthenticationRequiredException:
    """Test AuthenticationRequiredException."""

    def test_exception_basic(self):
        """Should create exception with required fields."""
        exc = AuthenticationRequiredException(
            server_name="my-server",
            auth_type="api_key",
        )
        assert exc.server_name == "my-server"
        assert exc.auth_type == "api_key"
        assert exc.message == "Authentication required"
        assert exc.oauth_start_url is None

    def test_exception_with_message(self):
        """Should accept custom message."""
        exc = AuthenticationRequiredException(
            server_name="my-server",
            auth_type="jwt",
            message="Custom auth message",
        )
        assert exc.message == "Custom auth message"
        assert str(exc) == "Custom auth message"

    def test_exception_with_oauth_url(self):
        """Should store OAuth start URL for OAuth auth type."""
        exc = AuthenticationRequiredException(
            server_name="oauth-server",
            auth_type="oauth",
            oauth_start_url="/api/mcp/auth/oauth-server/oauth/start",
        )
        assert exc.oauth_start_url == "/api/mcp/auth/oauth-server/oauth/start"

    def test_exception_to_dict(self):
        """Should convert to dict for frontend consumption."""
        exc = AuthenticationRequiredException(
            server_name="my-server",
            auth_type="api_key",
            message="Please provide API key",
            oauth_start_url=None,
        )
        result = exc.to_dict()
        assert result == {
            "server_name": "my-server",
            "auth_type": "api_key",
            "message": "Please provide API key",
            "oauth_start_url": None,
        }


class TestMCPTokenStorage:
    """Test MCPTokenStorage class."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for token storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def storage(self, temp_storage_dir):
        """Create a MCPTokenStorage instance with temp directory."""
        # Pass encryption key directly to avoid app_settings caching issues
        storage = MCPTokenStorage(
            storage_dir=temp_storage_dir,
            encryption_key="test-encryption-key-12345"
        )
        yield storage

    def test_store_and_retrieve_token(self, storage):
        """Should store and retrieve a token successfully."""
        # Store token
        stored = storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="my-api-key-123",
            token_type="api_key",
        )
        assert stored.token_value == "my-api-key-123"
        assert stored.token_type == "api_key"

        # Retrieve token
        retrieved = storage.get_token("user@example.com", "test-server")
        assert retrieved is not None
        assert retrieved.token_value == "my-api-key-123"
        assert retrieved.token_type == "api_key"
        assert retrieved.user_email == "user@example.com"
        assert retrieved.server_name == "test-server"

    def test_store_token_with_expiry(self, storage):
        """Should store token with expiration time."""
        expiry = time.time() + 3600
        stored = storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="expiring-token",
            token_type="jwt",
            expires_at=expiry,
        )
        assert stored.expires_at == expiry

        retrieved = storage.get_token("user@example.com", "test-server")
        assert retrieved is not None
        assert retrieved.expires_at == expiry
        assert retrieved.is_expired() is False

    def test_store_token_with_scopes(self, storage):
        """Should store token with scopes."""
        stored = storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="scoped-token",
            token_type="bearer",
            scopes="read write admin",
        )
        assert stored.scopes == "read write admin"

        retrieved = storage.get_token("user@example.com", "test-server")
        assert retrieved is not None
        assert retrieved.scopes == "read write admin"

    def test_get_nonexistent_token(self, storage):
        """Should return None for nonexistent token."""
        retrieved = storage.get_token("nobody@example.com", "no-server")
        assert retrieved is None

    def test_remove_token(self, storage):
        """Should remove token successfully."""
        # Store token
        storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="to-be-removed",
            token_type="api_key",
        )

        # Verify it exists
        assert storage.get_token("user@example.com", "test-server") is not None

        # Remove it
        removed = storage.remove_token("user@example.com", "test-server")
        assert removed is True

        # Verify it's gone
        assert storage.get_token("user@example.com", "test-server") is None

    def test_remove_nonexistent_token(self, storage):
        """Should return False when removing nonexistent token."""
        removed = storage.remove_token("nobody@example.com", "no-server")
        assert removed is False

    def test_user_isolation(self, storage):
        """Tokens should be isolated per user."""
        # Store token for user1
        storage.store_token(
            user_email="user1@example.com",
            server_name="shared-server",
            token_value="user1-token",
            token_type="api_key",
        )

        # Store token for user2
        storage.store_token(
            user_email="user2@example.com",
            server_name="shared-server",
            token_value="user2-token",
            token_type="api_key",
        )

        # Each user should only see their own token
        user1_token = storage.get_token("user1@example.com", "shared-server")
        user2_token = storage.get_token("user2@example.com", "shared-server")

        assert user1_token is not None
        assert user2_token is not None
        assert user1_token.token_value == "user1-token"
        assert user2_token.token_value == "user2-token"

    def test_overwrite_existing_token(self, storage):
        """Should overwrite existing token for same user/server."""
        # Store initial token
        storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="old-token",
            token_type="api_key",
        )

        # Store new token
        storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="new-token",
            token_type="jwt",
        )

        # Should get the new token
        retrieved = storage.get_token("user@example.com", "test-server")
        assert retrieved is not None
        assert retrieved.token_value == "new-token"
        assert retrieved.token_type == "jwt"

    def test_get_user_auth_status(self, storage):
        """Should return auth status for all user's tokens."""
        # Store tokens for user
        storage.store_token(
            user_email="user@example.com",
            server_name="server1",
            token_value="token1",
            token_type="api_key",
        )
        storage.store_token(
            user_email="user@example.com",
            server_name="server2",
            token_value="token2",
            token_type="jwt",
            expires_at=time.time() + 3600,
        )

        # Store token for different user (should not be included)
        storage.store_token(
            user_email="other@example.com",
            server_name="server3",
            token_value="other-token",
            token_type="bearer",
        )

        status = storage.get_user_auth_status("user@example.com")

        assert "server1" in status
        assert "server2" in status
        assert "server3" not in status  # Different user

        assert status["server1"]["token_type"] == "api_key"
        assert status["server2"]["token_type"] == "jwt"
        assert status["server2"]["is_expired"] is False

    def test_email_case_insensitive(self, storage):
        """Email lookups should be case-insensitive."""
        storage.store_token(
            user_email="User@Example.COM",
            server_name="test-server",
            token_value="test-token",
            token_type="api_key",
        )

        # Should find with different case
        retrieved = storage.get_token("user@example.com", "test-server")
        assert retrieved is not None
        assert retrieved.token_value == "test-token"


class TestMCPTokenStoragePersistence:
    """Test token storage persistence across instances."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for token storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_persistence_across_instances(self, temp_storage_dir):
        """Tokens should persist across storage instances."""
        # Pass encryption_key directly to avoid app_settings caching issues
        encryption_key = "test-encryption-key-12345"

        # Create first storage instance and store token
        storage1 = MCPTokenStorage(storage_dir=temp_storage_dir, encryption_key=encryption_key)
        storage1.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="persistent-token",
            token_type="api_key",
        )

        # Create second storage instance and retrieve token
        storage2 = MCPTokenStorage(storage_dir=temp_storage_dir, encryption_key=encryption_key)
        retrieved = storage2.get_token("user@example.com", "test-server")

        assert retrieved is not None
        assert retrieved.token_value == "persistent-token"


class TestMCPTokenStorageEncryption:
    """Test token encryption functionality."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for token storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_tokens_encrypted_at_rest(self, temp_storage_dir):
        """Token values should be encrypted in storage file."""
        # Pass encryption key directly to avoid app_settings caching issues
        storage = MCPTokenStorage(
            storage_dir=temp_storage_dir,
            encryption_key="test-encryption-key-12345"
        )
        storage.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="secret-api-key-xyz",
            token_type="api_key",
        )

        # Read raw storage file
        storage_file = temp_storage_dir / "mcp_tokens.enc"
        raw_content = storage_file.read_text()

        # Plain token should not appear in raw content
        assert "secret-api-key-xyz" not in raw_content

    def test_different_keys_cannot_decrypt(self, temp_storage_dir):
        """Tokens encrypted with different keys should not be readable."""
        # Store with one key
        storage1 = MCPTokenStorage(
            storage_dir=temp_storage_dir,
            encryption_key="first-encryption-key"
        )
        storage1.store_token(
            user_email="user@example.com",
            server_name="test-server",
            token_value="secret-token",
            token_type="api_key",
        )

        # Try to read with different key
        storage2 = MCPTokenStorage(
            storage_dir=temp_storage_dir,
            encryption_key="different-encryption-key"
        )

        # Should return None (decryption fails gracefully)
        retrieved = storage2.get_token("user@example.com", "test-server")
        assert retrieved is None


class TestGetMCPTokenStorageSingleton:
    """Test the get_token_storage singleton function."""

    def test_returns_token_storage_instance(self):
        """Should return a MCPTokenStorage instance."""
        storage = get_token_storage()
        assert isinstance(storage, MCPTokenStorage)

    def test_returns_same_instance(self):
        """Should return the same instance on repeated calls."""
        storage1 = get_token_storage()
        storage2 = get_token_storage()
        assert storage1 is storage2
