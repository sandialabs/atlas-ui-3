"""Secure per-user token storage for MCP server authentication.

This module provides encrypted storage for authentication tokens associated
with MCP servers on a per-user basis. Supports multiple token types:
- API keys
- JWT tokens
- Bearer tokens
- OAuth access tokens

Each user's tokens are isolated and encrypted using Fernet (AES-128-CBC).

Key format: "{user_email}:{server_name}"

Security considerations:
- Tokens are encrypted at rest using a key derived from environment variable
- Each user's tokens are stored separately (isolation by key)
- Token expiration is tracked and validated
- No plaintext tokens are logged

Updated: 2025-01-21
"""

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class AuthenticationRequiredException(Exception):
    """Exception raised when a user needs to authenticate with an MCP server.

    This exception carries information needed to initiate the OAuth flow
    so the frontend can automatically redirect the user to authenticate.
    """

    def __init__(
        self,
        server_name: str,
        auth_type: str,
        message: str = "Authentication required",
        oauth_start_url: Optional[str] = None,
    ):
        super().__init__(message)
        self.server_name = server_name
        self.auth_type = auth_type  # "oauth", "jwt", "bearer", or "api_key"
        self.oauth_start_url = oauth_start_url  # URL to start OAuth flow (if oauth)
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception info to a dict for frontend consumption."""
        return {
            "server_name": self.server_name,
            "auth_type": self.auth_type,
            "message": self.message,
            "oauth_start_url": self.oauth_start_url,
        }


def _make_token_key(user_email: str, server_name: str) -> str:
    """Create a storage key from user email and server name.

    Args:
        user_email: User's email address
        server_name: Name of the MCP server

    Returns:
        Combined key in format "user_email:server_name"
    """
    # Normalize to lowercase for consistent lookups
    return f"{user_email.lower()}:{server_name}"


def _parse_token_key(key: str) -> Tuple[str, str]:
    """Parse a storage key into user email and server name.

    Args:
        key: Combined key in format "user_email:server_name"

    Returns:
        Tuple of (user_email, server_name)

    Raises:
        ValueError: If key format is invalid
    """
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid token key format: {key}")
    return parts[0], parts[1]


@dataclass
class StoredToken:
    """Represents a stored authentication token."""

    token_type: str  # "api_key", "bearer", "jwt", "oauth_access", "oauth_refresh"
    token_value: str  # The actual token (will be encrypted at rest)
    user_email: str  # User who owns this token
    server_name: str  # MCP server this token is associated with
    created_at: float  # Unix timestamp when token was stored
    expires_at: Optional[float] = None  # Unix timestamp when token expires (if known)
    scopes: Optional[str] = None  # OAuth scopes (space-separated)
    refresh_token: Optional[str] = None  # OAuth refresh token (if available)
    metadata: Optional[Dict[str, Any]] = None  # Additional metadata

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """Check if token is expired or will expire within buffer period."""
        if self.expires_at is None:
            return False  # No expiration set, assume valid
        return time.time() >= (self.expires_at - buffer_seconds)

    def time_until_expiry(self) -> Optional[float]:
        """Get seconds until token expires, or None if no expiration."""
        if self.expires_at is None:
            return None
        return max(0, self.expires_at - time.time())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoredToken":
        """Create from dictionary."""
        return cls(**data)


class MCPTokenStorage:
    """Secure encrypted storage for per-user MCP authentication tokens.

    Tokens are stored in an encrypted JSON file on disk, keyed by the
    combination of user email and server name. The encryption key is
    derived from the MCP_TOKEN_ENCRYPTION_KEY environment variable using
    PBKDF2. If no key is set, a random key is generated (tokens will not
    persist across restarts in this case).

    Storage location: {storage_dir}/mcp_tokens.enc
    """

    # Salt for key derivation (constant, not secret)
    _SALT = b"atlas-mcp-token-storage-v1"

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        encryption_key: Optional[str] = None,
    ):
        """Initialize token storage.

        Args:
            storage_dir: Directory to store encrypted tokens. Defaults to
                        config/secure or runtime/tokens
            encryption_key: Base64-encoded encryption key or passphrase.
                           Defaults to MCP_TOKEN_ENCRYPTION_KEY env var.
        """
        self._storage_dir = storage_dir or self._default_storage_dir()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._storage_file = self._storage_dir / "mcp_tokens.enc"

        # Get or generate encryption key
        key_source = encryption_key or os.environ.get("MCP_TOKEN_ENCRYPTION_KEY")
        if key_source:
            self._fernet = self._derive_fernet(key_source)
            logger.info("Token storage initialized with configured encryption key")
        else:
            # Generate ephemeral key (tokens won't persist across restarts)
            ephemeral_key = Fernet.generate_key()
            self._fernet = Fernet(ephemeral_key)
            logger.warning(
                "No MCP_TOKEN_ENCRYPTION_KEY set. Using ephemeral key - "
                "tokens will not persist across application restarts."
            )

        # In-memory cache of decrypted tokens
        # Key format: "user_email:server_name"
        self._tokens: Dict[str, StoredToken] = {}
        self._load_tokens()

    def _default_storage_dir(self) -> Path:
        """Get default storage directory."""
        # Try project root locations
        candidates = [
            Path(__file__).parent.parent.parent.parent / "config" / "secure",
            Path(__file__).parent.parent.parent.parent / "runtime" / "tokens",
            Path.home() / ".atlas-ui" / "tokens",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                # Test write access
                test_file = candidate / ".write_test"
                test_file.write_text("test")
                test_file.unlink()
                return candidate
            except (PermissionError, OSError):
                continue

        # Fallback to temp directory
        import tempfile
        return Path(tempfile.gettempdir()) / "atlas-mcp-tokens"

    def _derive_fernet(self, key_source: str) -> Fernet:
        """Derive Fernet key from passphrase or base64 key."""
        try:
            # Try to use as direct Fernet key (base64-encoded 32 bytes)
            return Fernet(key_source.encode())
        except (ValueError, Exception):
            # Derive key from passphrase using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self._SALT,
                iterations=480000,  # OWASP recommended minimum
            )
            derived_key = base64.urlsafe_b64encode(
                kdf.derive(key_source.encode())
            )
            return Fernet(derived_key)

    def _load_tokens(self) -> None:
        """Load and decrypt tokens from storage file."""
        if not self._storage_file.exists():
            self._tokens = {}
            return

        try:
            encrypted_data = self._storage_file.read_bytes()
            decrypted_data = self._fernet.decrypt(encrypted_data)
            tokens_dict = json.loads(decrypted_data.decode())

            self._tokens = {
                key: StoredToken.from_dict(token_data)
                for key, token_data in tokens_dict.items()
            }
            logger.info(f"Loaded {len(self._tokens)} encrypted tokens from storage")

        except InvalidToken:
            logger.error(
                "Failed to decrypt token storage - encryption key may have changed. "
                "Tokens will be reset."
            )
            self._tokens = {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Corrupted token storage: {e}. Tokens will be reset.")
            self._tokens = {}

    def _save_tokens(self) -> None:
        """Encrypt and save tokens to storage file."""
        try:
            tokens_dict = {
                key: token.to_dict()
                for key, token in self._tokens.items()
            }
            json_data = json.dumps(tokens_dict, indent=2)
            encrypted_data = self._fernet.encrypt(json_data.encode())

            # Atomic write: write to temp file then rename
            temp_file = self._storage_file.with_suffix(".tmp")
            temp_file.write_bytes(encrypted_data)
            temp_file.rename(self._storage_file)

            logger.debug(f"Saved {len(self._tokens)} encrypted tokens to storage")

        except Exception as e:
            logger.error(f"Failed to save tokens: {e}")
            raise

    def store_token(
        self,
        user_email: str,
        server_name: str,
        token_value: str,
        token_type: str = "bearer",
        expires_at: Optional[float] = None,
        scopes: Optional[str] = None,
        refresh_token: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StoredToken:
        """Store an authentication token for a user and MCP server.

        Args:
            user_email: User's email address
            server_name: Name of the MCP server
            token_value: The token value (JWT, access token, etc.)
            token_type: Type of token ("bearer", "oauth_access", "jwt")
            expires_at: Unix timestamp when token expires
            scopes: OAuth scopes (space-separated string)
            refresh_token: OAuth refresh token if available
            metadata: Additional metadata to store

        Returns:
            The stored token object
        """
        token = StoredToken(
            token_type=token_type,
            token_value=token_value,
            user_email=user_email.lower(),
            server_name=server_name,
            created_at=time.time(),
            expires_at=expires_at,
            scopes=scopes,
            refresh_token=refresh_token,
            metadata=metadata,
        )

        key = _make_token_key(user_email, server_name)
        self._tokens[key] = token
        self._save_tokens()

        from core.log_sanitizer import sanitize_for_logging
        logger.info(
            f"Stored {token_type} token for user and server '{sanitize_for_logging(server_name)}' "
            f"(expires: {'never' if expires_at is None else time.ctime(expires_at)})"
        )
        return token

    def get_token(self, user_email: str, server_name: str) -> Optional[StoredToken]:
        """Get stored token for a user and MCP server.

        Args:
            user_email: User's email address
            server_name: Name of the MCP server

        Returns:
            StoredToken if found, None otherwise
        """
        key = _make_token_key(user_email, server_name)
        token = self._tokens.get(key)
        if token is None:
            return None

        # Log warning if expired (but still return it - caller may want to refresh)
        if token.is_expired():
            logger.debug(f"Token for server '{server_name}' has expired")

        return token

    def get_valid_token(self, user_email: str, server_name: str) -> Optional[StoredToken]:
        """Get stored token only if not expired.

        Args:
            user_email: User's email address
            server_name: Name of the MCP server

        Returns:
            StoredToken if found and not expired, None otherwise
        """
        token = self.get_token(user_email, server_name)
        if token is None or token.is_expired():
            return None
        return token

    def remove_token(self, user_email: str, server_name: str) -> bool:
        """Remove stored token for a user and MCP server.

        Args:
            user_email: User's email address
            server_name: Name of the MCP server

        Returns:
            True if token was removed, False if not found
        """
        key = _make_token_key(user_email, server_name)
        if key in self._tokens:
            del self._tokens[key]
            self._save_tokens()
            from core.log_sanitizer import sanitize_for_logging
            logger.info(f"Removed token for server '{sanitize_for_logging(server_name)}'")
            return True
        return False

    def get_user_tokens(self, user_email: str) -> Dict[str, StoredToken]:
        """Get all tokens for a specific user.

        Args:
            user_email: User's email address

        Returns:
            Dictionary mapping server names to tokens
        """
        user_email_lower = user_email.lower()
        return {
            _parse_token_key(key)[1]: token  # Extract server_name from key
            for key, token in self._tokens.items()
            if token.user_email == user_email_lower
        }

    def get_user_auth_status(self, user_email: str) -> Dict[str, Dict[str, Any]]:
        """Get authentication status for all servers for a user.

        Returns metadata about tokens without revealing token values.

        Args:
            user_email: User's email address

        Returns:
            Dictionary mapping server names to auth status info
        """
        user_tokens = self.get_user_tokens(user_email)
        return {
            server_name: {
                "authenticated": True,
                "token_type": token.token_type,
                "created_at": token.created_at,
                "expires_at": token.expires_at,
                "is_expired": token.is_expired(),
                "time_until_expiry": token.time_until_expiry(),
                "has_refresh_token": token.refresh_token is not None,
                "scopes": token.scopes,
            }
            for server_name, token in user_tokens.items()
        }

    def list_all_tokens_metadata(self) -> List[Dict[str, Any]]:
        """List metadata for all stored tokens (admin use).

        Returns token metadata without revealing token values.

        Returns:
            List of token metadata dictionaries
        """
        return [
            {
                "user_email": token.user_email,
                "server_name": token.server_name,
                "token_type": token.token_type,
                "created_at": token.created_at,
                "expires_at": token.expires_at,
                "is_expired": token.is_expired(),
                "has_refresh_token": token.refresh_token is not None,
                "scopes": token.scopes,
            }
            for token in self._tokens.values()
        ]

    def update_oauth_tokens(
        self,
        user_email: str,
        server_name: str,
        access_token: str,
        expires_at: Optional[float] = None,
        refresh_token: Optional[str] = None,
        scopes: Optional[str] = None,
    ) -> StoredToken:
        """Update OAuth tokens after a refresh or new authorization.

        Preserves existing metadata and refresh token if new one not provided.

        Args:
            user_email: User's email address
            server_name: Name of the MCP server
            access_token: New access token
            expires_at: Unix timestamp when token expires
            refresh_token: New refresh token (or None to keep existing)
            scopes: OAuth scopes

        Returns:
            Updated StoredToken
        """
        existing = self.get_token(user_email, server_name)

        return self.store_token(
            user_email=user_email,
            server_name=server_name,
            token_value=access_token,
            token_type="oauth_access",
            expires_at=expires_at,
            scopes=scopes or (existing.scopes if existing else None),
            refresh_token=refresh_token or (existing.refresh_token if existing else None),
            metadata=existing.metadata if existing else None,
        )

    def clear_user_tokens(self, user_email: str) -> int:
        """Remove all tokens for a specific user.

        Args:
            user_email: User's email address

        Returns:
            Number of tokens removed
        """
        user_email_lower = user_email.lower()
        keys_to_remove = [
            key for key, token in self._tokens.items()
            if token.user_email == user_email_lower
        ]

        for key in keys_to_remove:
            del self._tokens[key]

        if keys_to_remove:
            self._save_tokens()
            logger.info(f"Cleared {len(keys_to_remove)} tokens for user")

        return len(keys_to_remove)

    def clear_all(self) -> int:
        """Remove all stored tokens (admin use).

        Returns:
            Number of tokens removed
        """
        count = len(self._tokens)
        self._tokens.clear()
        self._save_tokens()
        logger.info(f"Cleared all {count} stored tokens")
        return count


# Global token storage instance (lazy initialization)
_token_storage: Optional[MCPTokenStorage] = None


def get_token_storage() -> MCPTokenStorage:
    """Get the global token storage instance."""
    global _token_storage
    if _token_storage is None:
        _token_storage = MCPTokenStorage()
    return _token_storage
