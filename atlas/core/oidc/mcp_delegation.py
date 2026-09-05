"""Delegated credentials for MCP servers.

Ties the delegation layer to MCP tool calls: when a server is configured with
``auth_type: "delegated"``, Atlas exchanges the logged-in user's token for a
short-lived, audience-specific token for *that* server instead of forwarding
the user's own access token, which it never does.

Server configuration (``mcp.json``)::

    "some-server": {
      "url": "https://tools.example.gov/mcp",
      "auth_type": "delegated",
      "delegation": {
        "audience": "api://tools.example.gov",
        "scope": "tools.read tools.invoke"
      }
    }

``audience`` defaults to the server's URL, which is what the MCP authorization
specification uses as the canonical resource identifier.
"""

import logging
import re
from typing import Any, Dict, Optional

from atlas.core.oidc.delegation import (
    DelegationError,
    DelegationRequest,
    get_delegation_manager_async,
)
from atlas.core.oidc.session import get_session_store

logger = logging.getLogger(__name__)

DELEGATED_AUTH_TYPE = "delegated"

# Marks a stored token as one Atlas minted by delegation rather than one the
# user uploaded, so revocation can tell them apart.
DELEGATION_METADATA_SOURCE = "delegation"

# MCP server names reach the log from configuration, but they also flow from
# request-shaped call paths, so they are re-derived from a strict allowlist
# before being logged rather than merely escaped. A name outside the pattern
# is not a name we can attribute anything to anyway.
_LOGGABLE_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def loggable_server_name(server_name: str) -> str:
    """Return a form of the server name that is safe to write to a log line.

    Two steps, because either alone leaves a gap: line breaks are stripped so a
    name can never forge an additional log record, and the result must still
    match the allowlist or it is replaced wholesale. A name outside the pattern
    is not one we could attribute anything to anyway.
    """
    stripped = (server_name or "").replace("\r", "").replace("\n", "")
    return stripped if _LOGGABLE_NAME.fullmatch(stripped) else "<invalid-name>"


def is_delegated_server(server_config: Dict[str, Any]) -> bool:
    """Whether this MCP server should be reached with a delegated credential."""
    return (server_config or {}).get("auth_type") == DELEGATED_AUTH_TYPE


def _delegation_settings(server_config: Dict[str, Any]) -> Dict[str, Any]:
    delegation = (server_config or {}).get("delegation")
    return delegation if isinstance(delegation, dict) else {}


def _find_session(user_email: str):
    """Find this user's live OIDC session, if they have one.

    The store is keyed by session, not by user, so this walks live sessions.
    Returns None when the user has no OIDC login -- a header-authenticated user
    has no token Atlas may delegate from, and the caller degrades to the normal
    "not authenticated for this server" path rather than inventing one.
    """
    normalized = (user_email or "").strip().lower()
    if not normalized:
        return None
    # Access the sessions through the store's public surface; the store keeps
    # its map private, so ask it for a snapshot.
    for session in get_session_store().iter_sessions():
        if session.user_id.strip().lower() == normalized and session.access_token:
            return session
    return None


async def _resolve_subject_token(user_email: str) -> Optional[str]:
    """The access token to exchange, refreshed first if it is at or past expiry.

    An Atlas session outlives the IdP's access token by hours, so exchanging
    whatever token the session happens to hold would start failing partway
    through an otherwise healthy session.
    """
    session = _find_session(user_email)
    if session is None:
        return None
    from atlas.core.oidc.session_refresh import ensure_fresh_access_token

    return await ensure_fresh_access_token(session)


async def mint_delegated_token_for_server(
    user_email: str,
    server_name: str,
    server_config: Dict[str, Any],
    *,
    actor: Optional[str] = None,
):
    """Return a :class:`DelegatedToken` for one MCP server, or None.

    None means "no delegated credential is available" for any reason --
    delegation disabled, no OIDC session, or a failed exchange. Every one of
    those must leave the caller on its existing path rather than raising into
    a tool call.
    """
    if not is_delegated_server(server_config):
        return None

    manager = await get_delegation_manager_async()
    if manager is None:
        logger.debug(
            "MCP server '%s' requests delegation but delegation is not configured",
            loggable_server_name(server_name),
        )
        return None

    subject_token = await _resolve_subject_token(user_email)
    if not subject_token:
        logger.debug(
            "No OIDC session token available to delegate for MCP server '%s'",
            loggable_server_name(server_name),
        )
        return None

    delegation = _delegation_settings(server_config)
    audience = delegation.get("audience") or server_config.get("url")
    request = DelegationRequest(
        user_id=user_email,
        subject_token=subject_token,
        audience=audience,
        resource=delegation.get("resource"),
        scope=delegation.get("scope"),
        actor=actor or f"mcp:{server_name}",
    )

    try:
        return await manager.get_token(request)
    except DelegationError as exc:
        logger.error(
            "Delegated token exchange failed for MCP server '%s': %s",
            loggable_server_name(server_name),
            exc,
        )
        return None
    except Exception as exc:  # pragma: no cover - network/provider surprises
        logger.error(
            "Unexpected error minting a delegated token for MCP server '%s': %s",
            loggable_server_name(server_name),
            exc,
            exc_info=True,
        )
        return None


async def revoke_delegated_credentials(user_email: str) -> int:
    """Discard every delegated credential held for one user.

    Three places hold one, and clearing only the first leaves the credential
    live: the delegation manager's cache, the encrypted token store (a
    delegated token is written there so the existing expiry-driven client
    invalidation keeps working), and any MCP client already built around it.
    A logout or an explicit "drop my delegated tokens" must reach all three,
    or the next tool call simply finds the stored token and carries on.

    Returns the number of stored delegated tokens removed.
    """
    from atlas.core.oidc.delegation import get_delegation_manager

    manager = get_delegation_manager()
    if manager is not None:
        manager.invalidate_user(user_email)

    from atlas.modules.mcp_tools.token_storage import get_token_storage

    token_storage = get_token_storage()
    delegated_servers = [
        server_name
        for server_name, token in token_storage.get_user_tokens(user_email).items()
        if (token.metadata or {}).get("source") == DELEGATION_METADATA_SOURCE
    ]

    removed = 0
    for server_name in delegated_servers:
        if token_storage.remove_token(user_email, server_name):
            removed += 1

    if delegated_servers:
        try:
            from atlas.infrastructure.app_factory import app_factory

            mcp_manager = app_factory.get_mcp_manager()
            for server_name in delegated_servers:
                await mcp_manager._invalidate_user_client(user_email, server_name)
        except Exception:  # pragma: no cover - never fail a logout on this
            logger.debug("Could not evict MCP user clients after revocation", exc_info=True)

    return removed
