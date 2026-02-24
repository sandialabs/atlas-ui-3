"""Globus OAuth helper functions for ALCF endpoint token integration.

Handles the Globus OAuth 2.0 authorization code flow:
1. Redirect user to Globus Auth with requested scopes (including ALCF)
2. Exchange authorization code for tokens
3. Extract service-specific tokens from 'other_tokens' in the response
4. Store them in MCPTokenStorage for automatic use by LLM caller

Updated: 2026-02-24
"""

import logging
import os
import secrets
import time
from typing import Any, Dict, List
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# HTTP timeout for Globus API calls (seconds)
GLOBUS_HTTP_TIMEOUT = 20

# Default Globus Auth base URL (overridable via env var for testing with mock server)
_GLOBUS_AUTH_BASE_DEFAULT = "https://auth.globus.org/v2/oauth2"


def _get_auth_base() -> str:
    """Get Globus Auth base URL, checking env var each call for testability."""
    return os.environ.get("GLOBUS_AUTH_BASE_URL", _GLOBUS_AUTH_BASE_DEFAULT)


def _get_authorize_url() -> str:
    return f"{_get_auth_base()}/authorize"


def _get_token_url() -> str:
    return f"{_get_auth_base()}/token"


def _get_userinfo_url() -> str:
    return f"{_get_auth_base()}/userinfo"


def _get_logout_url() -> str:
    return os.environ.get(
        "GLOBUS_LOGOUT_URL", "https://auth.globus.org/v2/web/logout"
    )


def build_scopes(configured_scopes: str) -> str:
    """Combine base scopes with configured service-specific scopes.

    Always includes 'openid profile email' for identity.
    Additional scopes (e.g. ALCF inference) come from configuration.
    """
    base = "openid profile email"
    extra = configured_scopes.strip() if configured_scopes else ""
    if extra and extra != base:
        return f"{base} {extra}"
    return base


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
) -> str:
    """Build Globus authorization URL for the OAuth flow."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    return f"{_get_authorize_url()}?{urlencode(params)}"


def build_logout_url(client_id: str) -> str:
    """Build Globus logout URL to clear Globus session."""
    return f"{_get_logout_url()}?{urlencode({'client_id': client_id})}"


def generate_oauth_state() -> str:
    """Generate a cryptographically secure state parameter for CSRF protection."""
    return secrets.token_urlsafe(32)


async def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Dict[str, Any]:
    """Exchange authorization code for tokens via Globus token endpoint.

    Returns the full token response including 'other_tokens' for
    service-specific scopes (ALCF, Globus Compute, etc.).

    Raises:
        httpx.HTTPStatusError: If the token exchange fails.
        ValueError: If the response is not valid JSON.
    """
    async with httpx.AsyncClient(timeout=GLOBUS_HTTP_TIMEOUT) as client:
        resp = await client.post(
            _get_token_url(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_globus_userinfo(access_token: str) -> Dict[str, Any]:
    """Fetch user info from Globus /userinfo endpoint."""
    async with httpx.AsyncClient(timeout=GLOBUS_HTTP_TIMEOUT) as client:
        resp = await client.get(
            _get_userinfo_url(),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def extract_scope_tokens(token_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract service-specific tokens from the Globus token response.

    Globus returns additional scoped tokens in the 'other_tokens' field.
    Each entry has: resource_server, access_token, expires_in, scope, token_type.

    Returns:
        List of token dicts, each containing:
        - resource_server: UUID identifying the service
        - access_token: Bearer token for that service
        - expires_in: Seconds until expiration
        - scope: Space-separated scope string
        - token_type: Usually "Bearer"
    """
    other_tokens = token_data.get("other_tokens", [])
    if not isinstance(other_tokens, list):
        logger.warning("Globus token response 'other_tokens' is not a list")
        return []
    return other_tokens


def store_globus_tokens(
    user_email: str,
    token_data: Dict[str, Any],
) -> int:
    """Store Globus tokens in MCPTokenStorage for later use by LLM caller.

    Stores the main Globus identity token and all service-specific tokens
    from 'other_tokens'. Tokens are keyed as 'globus:{resource_server}'.

    Args:
        user_email: Authenticated user's email
        token_data: Full Globus token response

    Returns:
        Number of tokens stored (main + other_tokens)
    """
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    token_storage = get_token_storage()
    count = 0

    # Store main Globus identity token
    main_access_token = token_data.get("access_token")
    if main_access_token:
        main_resource_server = token_data.get("resource_server", "auth.globus.org")
        expires_in = token_data.get("expires_in")
        expires_at = (time.time() + expires_in) if expires_in else None

        token_storage.store_token(
            user_email=user_email,
            server_name=f"globus:{main_resource_server}",
            token_value=main_access_token,
            token_type="oauth_access",
            expires_at=expires_at,
            scopes=token_data.get("scope", ""),
            refresh_token=token_data.get("refresh_token"),
            metadata={"provider": "globus", "resource_server": main_resource_server},
        )
        count += 1
        logger.info("Stored main Globus token for resource_server '%s'", main_resource_server)

    # Store each service-specific token from other_tokens
    for other_token in extract_scope_tokens(token_data):
        resource_server = other_token.get("resource_server")
        access_token = other_token.get("access_token")

        if not resource_server or not access_token:
            logger.warning("Skipping other_token entry missing resource_server or access_token")
            continue

        expires_in = other_token.get("expires_in")
        expires_at = (time.time() + expires_in) if expires_in else None

        token_storage.store_token(
            user_email=user_email,
            server_name=f"globus:{resource_server}",
            token_value=access_token,
            token_type="oauth_access",
            expires_at=expires_at,
            scopes=other_token.get("scope", ""),
            metadata={
                "provider": "globus",
                "resource_server": resource_server,
                "token_type": other_token.get("token_type", "Bearer"),
            },
        )
        count += 1
        logger.info("Stored Globus scoped token for resource_server '%s'", resource_server)

    return count


def remove_globus_tokens(user_email: str) -> int:
    """Remove all Globus tokens for a user.

    Returns:
        Number of tokens removed.
    """
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    token_storage = get_token_storage()
    removed = 0

    # Get all tokens for this user and remove globus-prefixed ones
    with token_storage._lock:
        keys_to_remove = [
            key for key in token_storage._tokens
            if key.startswith(f"{user_email.lower()}:globus:")
        ]
        for key in keys_to_remove:
            del token_storage._tokens[key]
            removed += 1
        if removed:
            token_storage._save_tokens()

    logger.info("Removed %d Globus tokens for user", removed)
    return removed


def get_globus_auth_status(user_email: str) -> Dict[str, Any]:
    """Get the Globus authentication status for a user.

    Returns dict with:
    - authenticated: bool
    - resource_servers: list of resource server UUIDs with valid tokens
    - user_email: the user's email
    """
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    token_storage = get_token_storage()
    resource_servers = []

    with token_storage._lock:
        for key, token in token_storage._tokens.items():
            if not key.startswith(f"{user_email.lower()}:globus:"):
                continue
            server_name = token.server_name
            # Extract resource_server from "globus:{resource_server}"
            resource_server = server_name.removeprefix("globus:")

            resource_servers.append({
                "resource_server": resource_server,
                "is_expired": token.is_expired(),
                "expires_at": token.expires_at,
                "scopes": token.scopes or "",
            })

    return {
        "authenticated": any(not rs["is_expired"] for rs in resource_servers),
        "resource_servers": resource_servers,
        "user_email": user_email,
    }
