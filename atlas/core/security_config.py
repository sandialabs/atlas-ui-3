"""Shared startup guards that do not load the application configuration."""

import ipaddress
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Flags that bind a server to an address. Uvicorn uses ``--host``; Gunicorn and
# Hypercorn use ``--bind``/``-b`` (often as ``host:port``). Any of these in argv
# is a stronger signal than the environment because the CLI overrides it.
_BIND_HOST_FLAGS = ("--host", "--bind", "-b")
_BIND_HOST_PREFIXES = ("--host=", "--bind=")

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


def _iter_argv_bind_hosts(argv):
    """Yield every bind host hinted by command-line flags, in argument order."""
    index = 0
    while index < len(argv):
        arg = argv[index]
        for prefix in _BIND_HOST_PREFIXES:
            if arg.startswith(prefix):
                yield arg.split("=", 1)[1]
        if arg in _BIND_HOST_FLAGS and index + 1 < len(argv):
            yield argv[index + 1]
            index += 1
        elif arg.startswith("-b") and not arg.startswith("--") and len(arg) > 2:
            # Attached Gunicorn/Hypercorn form: ``-b0.0.0.0:8000``.
            yield arg[2:]
        index += 1


def _normalize_host(host: Optional[str]) -> Optional[str]:
    """Strip a ``host:port`` suffix and IPv6 brackets from a bind value."""
    if not host:
        return host
    value = host.strip()
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end]
    if value.count(":") == 1:
        maybe_host, maybe_port = value.rsplit(":", 1)
        if maybe_port.isdigit():
            return maybe_host
    return value


def _is_loopback_host(host: Optional[str]) -> bool:
    """Treat an unknown bind host as non-loopback so callers fail closed."""
    value = _normalize_host(host)
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.lower() == "localhost"


def resolve_bind_host(default: Optional[str] = None) -> Optional[str]:
    """Best-effort effective bind host for pre-serve safety checks.

    Gather every bind hint: the ``--host`` / ``--bind`` / ``-b`` CLI flags
    (including the attached ``-bHOST:PORT`` form), then ``UVICORN_HOST`` and
    ``ATLAS_HOST``. Any non-loopback hint makes the guard unsafe, so a
    non-loopback value is returned whenever one exists; otherwise the last
    loopback value is returned. When no source names a host, return ``default``
    (``None``) so callers fail closed instead of assuming loopback.
    """
    hosts = list(_iter_argv_bind_hosts(sys.argv[1:]))
    for name in ("UVICORN_HOST", "ATLAS_HOST"):
        value = os.environ.get(name)
        if value:
            hosts.append(value)
    if not hosts:
        return default
    for host in hosts:
        if not _is_loopback_host(host):
            return host
    return hosts[-1]


def validate_debug_configuration(settings, host: Optional[str]) -> None:
    """Refuse unsafe debug binds unless the operator explicitly opts in."""
    if not settings.debug_mode:
        return
    logger.warning(
        "SECURITY WARNING: DEBUG_MODE=true bypasses authentication. "
        "Do not expose this server to untrusted clients."
    )
    if settings.environment.strip().lower() == "production" and not (
        getattr(settings, "allow_debug_production", False)
    ):
        raise ValueError(
            "DEBUG_MODE=true is not permitted when ENVIRONMENT=production. "
            "Set DEBUG_MODE=false, or explicitly set ALLOW_DEBUG_PRODUCTION=true "
            "to accept unauthenticated access in production."
        )
    if not _is_loopback_host(host) and not settings.allow_debug_non_loopback:
        if host:
            detail = (
                f"DEBUG_MODE=true refuses the non-loopback bind host {host!r}. Bind to "
                "127.0.0.1 (for example `--host 127.0.0.1`), set DEBUG_MODE=false, or "
                "explicitly set ALLOW_DEBUG_NON_LOOPBACK=true to accept unauthenticated "
                "access on every interface."
            )
        else:
            detail = (
                "DEBUG_MODE=true cannot determine the bind host and will not assume "
                "loopback. Set ATLAS_HOST=127.0.0.1 (or pass `--host 127.0.0.1`), set "
                "DEBUG_MODE=false, or explicitly set ALLOW_DEBUG_NON_LOOPBACK=true to "
                "accept unauthenticated access on every interface."
            )
        raise ValueError(detail)
