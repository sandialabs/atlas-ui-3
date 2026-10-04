"""Keeping an OIDC session's access token usable for its whole lifetime.

An Atlas login session lasts hours; the IdP's access token usually lasts
minutes. Without this, a session that is still perfectly valid would carry a
long-dead access token, and everything downstream of it -- delegation above
all -- would start failing partway through the session with no signal to the
user beyond a tool going unauthenticated.

The refresh token never leaves the server-side session store, so refreshing is
something only this process can do on the user's behalf.
"""

import asyncio
import logging
import time
from typing import Optional
from weakref import WeakValueDictionary

from atlas.core.oidc.client_authentication import (
    ClientAuthenticationError,
    build_client_credentials_from_settings,
)
from atlas.core.oidc.discovery import OIDCDiscoveryError, get_provider_metadata
from atlas.core.oidc.oidc_client import OIDCFlowError, refresh_access_token
from atlas.core.oidc.session import OIDCSession, get_session_store

logger = logging.getLogger(__name__)

# One in-flight refresh per session. Several concurrent tool calls can notice
# the same expiring token at once; without this they would each burn the
# refresh token, and providers that rotate it would invalidate the others.
_refresh_locks: WeakValueDictionary = WeakValueDictionary()
_refresh_locks_guard = asyncio.Lock()


async def _lock_for(session_id: str) -> asyncio.Lock:
    async with _refresh_locks_guard:
        lock = _refresh_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _refresh_locks[session_id] = lock
        return lock


def _forget_lock(session_id: str) -> None:
    _refresh_locks.pop(session_id, None)


async def ensure_fresh_access_token(
    session: OIDCSession, settings=None
) -> Optional[str]:
    """Return a usable access token for ``session``, refreshing if needed.

    Returns None when the token cannot be refreshed. An invalid grant also
    removes the login session and its delegated credentials; transient failures
    leave the session available for a later retry.
    """
    if get_session_store().get(session.session_id) is not session:
        return None
    if not session.access_token_needs_refresh():
        return session.access_token

    if not session.refresh_token:
        logger.info("OIDC access token expired and the session has no refresh token")
        return None

    if settings is None:
        from atlas.infrastructure.app_factory import app_factory

        settings = app_factory.get_config_manager().app_settings

    lock = await _lock_for(session.session_id)
    async with lock:
        # Logout or a refused refresh may have removed it while we waited.
        if get_session_store().get(session.session_id) is not session:
            _forget_lock(session.session_id)
            return None
        # Another coroutine may have refreshed while we waited.
        if not session.access_token_needs_refresh():
            return session.access_token

        try:
            metadata = await get_provider_metadata(settings.oidc_issuer or "")
            credentials = build_client_credentials_from_settings(
                settings, metadata.token_endpoint
            )
            response = await refresh_access_token(
                token_endpoint=metadata.token_endpoint,
                refresh_token=session.refresh_token,
                credentials=credentials,
            )
        except (OIDCDiscoveryError, ClientAuthenticationError, OIDCFlowError) as exc:
            if (
                isinstance(exc, OIDCFlowError)
                and exc.error_code == "invalid_grant"
                and exc.status_code is not None
                and 400 <= exc.status_code < 500
            ):
                from atlas.core.oidc.mcp_delegation import revoke_delegated_credentials

                # Remove first: cleanup must never leave an authenticatable
                # session behind, even if a credential store is unavailable.
                get_session_store().remove(session.session_id)
                _forget_lock(session.session_id)
                try:
                    await revoke_delegated_credentials(session.user_id)
                except Exception:
                    logger.exception("Could not revoke delegated credentials after OIDC session ended")
                logger.info("OIDC session ended because the IdP refused its refresh grant")
                return None
            logger.warning("Could not refresh the OIDC access token: %s", exc)
            return None
        except Exception as exc:  # pragma: no cover - network surprises
            logger.warning(
                "Unexpected error refreshing the OIDC access token: %s", exc, exc_info=True
            )
            return None

        expires_in = response.get("expires_in")
        expires_at = (
            time.time() + float(expires_in)
            if isinstance(expires_in, (int, float)) and expires_in > 0
            else None
        )
        if get_session_store().get(session.session_id) is not session:
            _forget_lock(session.session_id)
            return None
        updated = get_session_store().update_tokens(
            session.session_id,
            access_token=response.get("access_token"),
            refresh_token=response.get("refresh_token"),
            access_token_expires_at=expires_at,
            scope=str(response.get("scope")) if response.get("scope") else None,
        )
        if updated is None:
            # The session was dropped (logout, expiry) while we refreshed.
            _forget_lock(session.session_id)
            return None
        logger.info("Refreshed the OIDC access token for a live session")
        return updated.access_token


async def get_refreshed_session(session_id: Optional[str], settings=None) -> Optional[OIDCSession]:
    """Refresh on authenticated use, retaining sessions during transient outages."""
    session = get_session_store().get(session_id)
    if session is None:
        return None
    await ensure_fresh_access_token(session, settings)
    # A failed refresh can mean either a transient outage or a removed session.
    # Re-read after awaiting rather than authenticating from a stale reference.
    return get_session_store().get(session_id)
