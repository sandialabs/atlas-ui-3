"""Encrypted storage for OAuth client registrations, keyed per MCP server.

Dynamic client registration (RFC 7591) issues credentials that identify
*Atlas*, not a user, so unlike access tokens these are shared across every
user of a given MCP server. Registering once and persisting the result keeps
Atlas from creating a fresh client record on every restart -- some
authorization servers rate-limit registration, and all of them accumulate
orphaned clients.

The file is encrypted with the same ``MCP_TOKEN_ENCRYPTION_KEY`` as the token
store (under a distinct salt) because a registration may include a
``client_secret``.

Storage location: {token storage dir}/mcp_oauth_clients.enc
"""

import base64
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.mcp_tools.mcp_oauth import RegisteredClient
from atlas.modules.mcp_tools.token_storage import resolve_encryption_key

logger = logging.getLogger(__name__)


def _make_key(server_name: str, issuer: str) -> str:
    """Key a registration by server and issuer.

    The issuer is part of the key so that repointing a server at a different
    authorization server re-registers instead of presenting a client_id the
    new provider never issued.
    """
    return f"{server_name}|{issuer.rstrip('/')}"


class MCPOAuthClientStore:
    """Encrypted, process-wide store of per-server OAuth client registrations."""

    # Distinct from the token store's salt: same passphrase, separate files,
    # so a key derived for one is not directly usable against the other.
    _SALT = b"atlas-mcp-oauth-client-storage-v1"

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        encryption_key: Optional[str] = None,
    ):
        from atlas.modules.config.config_manager import get_app_settings

        app_settings = get_app_settings()
        key_source = resolve_encryption_key(encryption_key, app_settings)

        if storage_dir is None:
            from atlas.modules.mcp_tools.token_storage import get_token_storage

            storage_dir = get_token_storage().storage_dir
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._storage_file = self._storage_dir / "mcp_oauth_clients.enc"

        self._fernet = self._derive_fernet(key_source)
        self._lock = threading.Lock()
        self._clients: Dict[str, RegisteredClient] = {}
        self._load()

    def _derive_fernet(self, key_source: str) -> Fernet:
        try:
            return Fernet(key_source.encode())
        except Exception:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self._SALT,
                iterations=480000,  # OWASP recommended minimum
            )
            return Fernet(base64.urlsafe_b64encode(kdf.derive(key_source.encode())))

    def _load(self) -> None:
        if not self._storage_file.exists():
            self._clients = {}
            return
        try:
            decrypted = self._fernet.decrypt(self._storage_file.read_bytes())
            raw = json.loads(decrypted.decode())
            self._clients = {
                key: RegisteredClient.from_dict(value) for key, value in raw.items()
            }
            logger.info(
                "Loaded %d encrypted MCP OAuth client registration(s)", len(self._clients)
            )
        except InvalidToken:
            # Same posture as the token store: a rotated key makes the file
            # unreadable, and re-registering is cheap and automatic.
            logger.error(
                "Failed to decrypt MCP OAuth client registrations - encryption key "
                "may have changed. Registrations will be reset."
            )
            self._clients = {}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error("Corrupted MCP OAuth client store: %s. Resetting.", exc)
            self._clients = {}

    def _save(self) -> None:
        payload = {key: value.to_dict() for key, value in self._clients.items()}
        encrypted = self._fernet.encrypt(json.dumps(payload, indent=2).encode())
        temp_file = self._storage_file.with_suffix(".tmp")
        temp_file.write_bytes(encrypted)
        temp_file.rename(self._storage_file)

    def get(self, server_name: str, issuer: str) -> Optional[RegisteredClient]:
        """Return the stored registration, or None when absent or unusable."""
        with self._lock:
            client = self._clients.get(_make_key(server_name, issuer))
        if client is None:
            return None
        if client.is_expired():
            logger.info(
                "Stored OAuth client registration for server '%s' has expired; "
                "re-registering",
                sanitize_for_logging(server_name),
            )
            self.remove(server_name, issuer)
            return None
        return client

    def put(self, server_name: str, client: RegisteredClient) -> RegisteredClient:
        with self._lock:
            self._clients[_make_key(server_name, client.issuer)] = client
            self._save()
        logger.info(
            "Stored OAuth client registration for MCP server '%s'",
            sanitize_for_logging(server_name),
        )
        return client

    def remove(self, server_name: str, issuer: str) -> bool:
        with self._lock:
            existed = self._clients.pop(_make_key(server_name, issuer), None) is not None
            if existed:
                self._save()
        return existed

    def clear(self) -> int:
        with self._lock:
            count = len(self._clients)
            self._clients = {}
            self._save()
        return count


_store: Optional[MCPOAuthClientStore] = None
_store_lock = threading.Lock()


def get_oauth_client_store() -> MCPOAuthClientStore:
    """Return the process-wide client registration store."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MCPOAuthClientStore()
    return _store


def reset_oauth_client_store() -> None:
    """Drop the singleton. Used by tests and by config reloads."""
    global _store
    with _store_lock:
        _store = None
