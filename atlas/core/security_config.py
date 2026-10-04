"""Shared startup guards that do not load the application configuration."""

import ipaddress
import logging
import os
import sys

logger = logging.getLogger(__name__)

_CAPABILITY_SECRET_PLACEHOLDERS = frozenset({
    "replace-with-openssl-rand-hex-32",
    "dev-capability-secret",
    "your-random-string-at-least-32-chars",
    "your-capability-token-secret",
    "change-me",
    "changeme",
})


def validate_capability_secret(secret: str) -> str:
    """Reject unsafe configured HMAC secrets; empty selects a random process key."""
    if secret == "":
        return secret
    if (
        secret.strip().lower() in _CAPABILITY_SECRET_PLACEHOLDERS
        or len(secret.strip().encode("utf-8")) < 32
    ):
        raise ValueError(
            "CAPABILITY_TOKEN_SECRET must be a unique random secret of at least "
            "32 bytes, not a placeholder. Generate one with `openssl rand -hex 32`, "
            "or leave it empty to use a random per-process secret."
        )
    return secret


def resolve_bind_host(default: str = "127.0.0.1") -> str:
    """Best-effort effective bind host for pre-serve safety checks.

    ``ATLAS_HOST`` covers the ``atlas-server`` CLI and the documented env var,
    but a direct ``uvicorn main:app --host 0.0.0.0`` start only sets Uvicorn's
    own configuration. Fall back to ``UVICORN_HOST`` (Uvicorn reads env vars
    with that prefix) and then the ``--host`` CLI flag so the debug guard
    cannot be bypassed by binding publicly outside our entry point.
    """
    for name in ("ATLAS_HOST", "UVICORN_HOST"):
        value = os.environ.get(name)
        if value:
            return value
    argv = sys.argv[1:]
    for index, arg in enumerate(argv):
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
        if arg == "--host" and index + 1 < len(argv):
            return argv[index + 1]
    return default


def validate_debug_configuration(settings, host: str) -> None:
    """Refuse unsafe debug server binds unless the operator explicitly opts in."""
    if not settings.debug_mode:
        return
    logger.warning(
        "SECURITY WARNING: DEBUG_MODE=true bypasses authentication. "
        "Do not expose this server to untrusted clients."
    )
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if (
        settings.environment.strip().lower() == "production" or not loopback
    ) and not settings.allow_debug_non_loopback:
        raise ValueError(
            "DEBUG_MODE=true requires a non-production ENVIRONMENT and a loopback "
            "bind host. Set DEBUG_MODE=false, or explicitly set "
            "ALLOW_DEBUG_NON_LOOPBACK=true to accept unauthenticated access."
        )
