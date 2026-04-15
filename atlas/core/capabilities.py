"""
Capability-token utilities for secure, headless access to resources.

Provides short-lived HMAC-signed tokens suitable for embedding in URLs,
primarily for file downloads by tools that don't carry session cookies.
"""

import base64
import hmac
import json
import logging
import secrets
import threading
import time
from hashlib import sha256
from typing import Any, Dict, Optional

from atlas.modules.config import config_manager

logger = logging.getLogger(__name__)

# Ephemeral per-process secret used only when CAPABILITY_TOKEN_SECRET is not
# configured. Generated on first use from a cryptographically secure RNG, so
# it cannot be predicted or forged by an attacker. Tokens signed with an
# ephemeral secret do not survive process restarts; operators must set
# CAPABILITY_TOKEN_SECRET in production for durable tokens.
_ephemeral_secret: Optional[bytes] = None
_ephemeral_lock = threading.Lock()
_ephemeral_warned: bool = False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _get_ephemeral_secret() -> bytes:
    """Return (and lazily create) the per-process ephemeral secret.

    The first caller in the process allocates 32 random bytes; subsequent
    callers receive the same value so mint/verify are consistent within
    one process lifetime.
    """
    global _ephemeral_secret, _ephemeral_warned
    with _ephemeral_lock:
        if _ephemeral_secret is None:
            _ephemeral_secret = secrets.token_bytes(32)
        if not _ephemeral_warned:
            _ephemeral_warned = True
            # Elevate severity in production so this cannot be missed in logs.
            try:
                production = not bool(
                    getattr(config_manager.app_settings, "debug_mode", False)
                )
            except Exception:
                production = True
            message = (
                "CAPABILITY_TOKEN_SECRET is not configured; using an ephemeral "
                "per-process secret for HMAC-signed file download tokens. "
                "Tokens will not survive process restarts. Set "
                "CAPABILITY_TOKEN_SECRET in the environment for production."
            )
            if production:
                logger.critical(message)
            else:
                logger.warning(message)
        return _ephemeral_secret


def _get_secret() -> bytes:
    """Get the capability token secret as bytes.

    Order of precedence:
    - Configured CAPABILITY_TOKEN_SECRET from app settings
    - Cryptographically random per-process ephemeral secret (fail-closed:
      never falls back to a hardcoded value that an attacker could predict)
    """
    try:
        settings = config_manager.app_settings
        configured = getattr(settings, "capability_token_secret", None)
        if configured:
            return configured.encode("utf-8")
    except Exception:
        logger.debug("Capability token secret config not ready; using ephemeral secret.")

    return _get_ephemeral_secret()


def _reset_ephemeral_secret_for_tests() -> None:
    """Test helper: clear the cached ephemeral secret and warning flag.

    Do not call from production code paths.
    """
    global _ephemeral_secret, _ephemeral_warned
    with _ephemeral_lock:
        _ephemeral_secret = None
        _ephemeral_warned = False


def _get_default_ttl_seconds() -> int:
    try:
        settings = config_manager.app_settings
        ttl = getattr(settings, "capability_token_ttl_seconds", None)
        if isinstance(ttl, int) and ttl > 0:
            return ttl
    except Exception:
        logger.debug("Capability token TTL not available; using default TTL.")
    return 3600


def generate_file_token(user_email: str, file_key: str, ttl_seconds: Optional[int] = None) -> str:
    """Generate a short-lived token authorizing access to a file key for a user."""
    exp = int(time.time()) + (ttl_seconds or _get_default_ttl_seconds())
    payload = {"u": user_email, "k": file_key, "e": exp}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(payload_bytes)
    sig = hmac.new(_get_secret(), body.encode("ascii"), sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify_file_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a file token and return claims if valid, else None."""
    try:
        body, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(_get_secret(), body.encode("ascii"), sha256).digest()
        given_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, given_sig):
            return None

        claims = json.loads(_b64url_decode(body).decode("utf-8"))
        if int(claims.get("e", 0)) < int(time.time()):
            return None
        # Ensure required claims exist
        if not claims.get("u") or not claims.get("k"):
            return None
        return claims
    except Exception:
        return None


def create_download_url(file_key: str, user_email: Optional[str]) -> str:
    """Create a download URL for a given file key, optionally with a token.

    If BACKEND_PUBLIC_URL is configured, returns an absolute URL that remote MCP servers
    can access. Otherwise, returns a relative URL (only works for local/stdio servers).

    Args:
        file_key: S3 key of the file to download
        user_email: User email for token generation

    Returns:
        Download URL (absolute if BACKEND_PUBLIC_URL configured, relative otherwise)
    """
    # Build relative path with token using /mcp/ prefix for MCP server access.
    # MCP servers use /mcp/files/download/ which bypasses nginx auth_request
    # and authenticates via HMAC capability tokens instead.
    # Browsers use /api/files/download/ which goes through nginx auth_request.
    if user_email:
        token = generate_file_token(user_email, file_key)
        relative_path = f"/mcp/files/download/{file_key}?token={token}"
    else:
        # Fallback: no user context available
        relative_path = f"/mcp/files/download/{file_key}"

    # Check if we should use absolute URLs for remote MCP server access
    try:
        settings = config_manager.app_settings
        backend_public_url = getattr(settings, "backend_public_url", None)
        if backend_public_url:
            # Strip trailing slash from base URL and combine with relative path
            base = backend_public_url.rstrip("/")
            return f"{base}{relative_path}"
    except Exception as e:
        logger.debug(f"Could not check backend_public_url config: {e}")

    # Return relative URL as default
    return relative_path
