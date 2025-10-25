"""
Capability-token utilities for secure, headless access to resources.

Provides short-lived HMAC-signed tokens suitable for embedding in URLs,
primarily for file downloads by tools that don't carry session cookies.
"""

import base64
import hmac
import json
import logging
import os
import time
from hashlib import sha256
from typing import Any, Dict, Optional

from modules.config import config_manager

logger = logging.getLogger(__name__)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _get_secret() -> bytes:
    """Get the capability token secret as bytes.

    Order of precedence:
    - ENV: CAPABILITY_TOKEN_SECRET
    - App settings (config)
    - Fallback development secret (unsafe for production)
    """
    env_secret = os.getenv("CAPABILITY_TOKEN_SECRET")
    if env_secret:
        return env_secret.encode("utf-8")

    try:
        settings = config_manager.app_settings
        if getattr(settings, "capability_token_secret", None):
            return settings.capability_token_secret.encode("utf-8")
    except Exception:
        # Config not ready; continue to fallback
        pass

    logger.warning("Using fallback dev capability token secret. Set CAPABILITY_TOKEN_SECRET for security.")
    return b"dev-capability-secret"


def _get_default_ttl_seconds() -> int:
    try:
        settings = config_manager.app_settings
        ttl = getattr(settings, "capability_token_ttl_seconds", None)
        if isinstance(ttl, int) and ttl > 0:
            return ttl
    except Exception:
        pass
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
    """Create a relative download URL for a given file key, optionally with a token."""
    if user_email:
        token = generate_file_token(user_email, file_key)
        return f"/api/files/download/{file_key}?token={token}"
    # Fallback: no user context available
    return f"/api/files/download/{file_key}"
