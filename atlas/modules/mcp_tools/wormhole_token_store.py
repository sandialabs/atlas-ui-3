"""Session-scoped storage for Wormhole authentication subtokens.

A Wormhole-wrapped Atlas instance receives, on each authenticated request, a
JWT that carries a unique ``x-subtoken`` header. That subtoken must be forwarded
to any Wormhole-enabled MCP servers (as an ``X-Token`` header) when Atlas opens a
streamable-HTTP connection on the user's behalf.

Unlike :mod:`atlas.modules.mcp_tools.token_storage` (which persists per-user,
per-server OAuth/bearer/API-key credentials encrypted on disk), the Wormhole
subtoken is:

- session-scoped and short-lived (its lifetime is managed by Wormhole),
- the same value for every Wormhole-enabled MCP server in a session, and
- never persisted to disk.

So this module keeps an in-memory, per-user map of the most recently observed
subtoken. The latest value seen for a user (from their WebSocket handshake or
HTTP request headers) wins, which naturally handles Wormhole rotating the token.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Mapping, Optional

from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)


def _mask(token: Optional[str]) -> str:
    """Return a log-safe representation of a subtoken (first/last chars only)."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


class WormholeTokenStore:
    """Thread-safe, in-memory, per-user store of Wormhole subtokens.

    Keyed by normalized (lower-cased) user email. Values are never written to
    disk and are dropped when the process exits.
    """

    def __init__(self) -> None:
        self._tokens: Dict[str, str] = {}
        self._lock = threading.Lock()

    def set_subtoken(self, user_email: Optional[str], subtoken: Optional[str]) -> None:
        """Store (or update) the subtoken for a user.

        A falsy ``subtoken`` clears any stored value, so a request that arrives
        without the header does not leave a stale token behind.
        """
        if not user_email:
            return
        key = normalize_user_email(user_email)
        with self._lock:
            if subtoken:
                changed = self._tokens.get(key) != subtoken
                self._tokens[key] = subtoken
                if changed:
                    logger.debug(
                        "Stored Wormhole subtoken for user (token=%s)", _mask(subtoken)
                    )
            elif key in self._tokens:
                del self._tokens[key]
                logger.debug("Cleared Wormhole subtoken for user")

    def get_subtoken(self, user_email: Optional[str]) -> Optional[str]:
        """Return the stored subtoken for a user, or ``None`` if not present."""
        if not user_email:
            return None
        key = normalize_user_email(user_email)
        with self._lock:
            return self._tokens.get(key)

    def clear(self, user_email: Optional[str]) -> None:
        """Remove any stored subtoken for a user (e.g. on disconnect)."""
        if not user_email:
            return
        key = normalize_user_email(user_email)
        with self._lock:
            self._tokens.pop(key, None)

    def clear_all(self) -> None:
        """Remove all stored subtokens (primarily for tests)."""
        with self._lock:
            self._tokens.clear()


# Module-level singleton, mirroring token_storage.get_token_storage().
_wormhole_store: Optional[WormholeTokenStore] = None


def get_wormhole_store() -> WormholeTokenStore:
    """Return the process-wide Wormhole subtoken store (lazily created)."""
    global _wormhole_store
    if _wormhole_store is None:
        _wormhole_store = WormholeTokenStore()
    return _wormhole_store


def capture_subtoken_from_headers(
    headers: Mapping[str, str], user_email: Optional[str]
) -> Optional[str]:
    """Extract and store the Wormhole subtoken from request/WebSocket headers.

    Reads the configured subtoken header (default ``x-subtoken``) from ``headers``
    and, when the Wormhole feature is enabled, writes it through to the store for
    ``user_email``. The write is unconditional: if the header is absent or empty,
    any previously stored subtoken for the user is cleared, so a stale value is
    never forwarded on a later MCP call. Safe to call unconditionally: it no-ops
    only when the feature is disabled or there is no authenticated user.

    Returns the captured subtoken (or ``None`` when absent) to aid logging/testing.
    """
    if not user_email:
        return None

    # Imported lazily to avoid a circular import at module load time.
    from atlas.modules.config import config_manager

    app_settings = config_manager.app_settings
    if not getattr(app_settings, "feature_wormhole_enabled", False):
        return None

    header_name = getattr(app_settings, "wormhole_subtoken_header", "x-subtoken")
    subtoken = headers.get(header_name)
    if subtoken is None:
        # Starlette Headers are case-insensitive, but plain dicts used in tests
        # are not; fall back to a case-insensitive lookup for robustness.
        lowered = header_name.lower()
        for key, value in headers.items():
            if key.lower() == lowered:
                subtoken = value
                break

    # Write through unconditionally: set_subtoken clears the stored value when
    # ``subtoken`` is falsy, so a request that arrives without the header does
    # not leave a previous session's subtoken behind to be forwarded later.
    get_wormhole_store().set_subtoken(user_email, subtoken)
    return subtoken
