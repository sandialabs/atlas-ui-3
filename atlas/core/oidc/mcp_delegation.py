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
from typing import Any, Dict, Optional

from atlas.core.oidc.delegation import (
    DelegationError,
    DelegationRequest,
    get_delegation_manager_async,
)
from atlas.core.oidc.session import get_session_store

logger = logging.getLogger(__name__)

DELEGATED_AUTH_TYPE = "delegated"


def is_delegated_server(server_config: Dict[str, Any]) -> bool:
    """Whether this MCP server should be reached with a delegated credential."""
    return (server_config or {}).get("auth_type") == DELEGATED_AUTH_TYPE


def _delegation_settings(server_config: Dict[str, Any]) -> Dict[str, Any]:
    delegation = (server_config or {}).get("delegation")
    return delegation if isinstance(delegation, dict) else {}


def _find_subject_token(user_email: str) -> Optional[str]:
    """Find the live OIDC access token to exchange for this user.

    The store is keyed by session, not by user, so this walks live sessions.
    Returns None when the user has no OIDC login -- a header-authenticated
    user has no token Atlas may delegate from, and the caller degrades to the
    normal "not authenticated for this server" path rather than inventing one.
    """
    store = get_session_store()
    normalized = (user_email or "").strip().lower()
    if not normalized:
        return None
    # Access the sessions through the store's public surface; the store keeps
    # its map private, so ask it for a snapshot.
    for session in store.iter_sessions():
        if session.user_id.strip().lower() == normalized and session.access_token:
            return session.access_token
    return None


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
            server_name,
        )
        return None

    subject_token = _find_subject_token(user_email)
    if not subject_token:
        logger.debug(
            "No OIDC session token available to delegate for MCP server '%s'", server_name
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
        logger.error("Delegated token exchange failed for MCP server '%s': %s", server_name, exc)
        return None
    except Exception as exc:  # pragma: no cover - network/provider surprises
        logger.error(
            "Unexpected error minting a delegated token for MCP server '%s': %s",
            server_name,
            exc,
            exc_info=True,
        )
        return None
