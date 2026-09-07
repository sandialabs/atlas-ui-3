"""Orchestration for the per-user MCP OAuth flow.

Sits between the HTTP routes / client factory and the protocol-level helpers
in :mod:`atlas.modules.mcp_tools.mcp_oauth`. Everything that needs to know
about Atlas -- server config, the redirect URI, the encrypted stores -- lives
here, so the protocol module stays a pure OAuth implementation.

The three entry points mirror the three moments in the flow:

- :func:`prepare_authorization` -- build the provider authorization URL to
  redirect the user's browser to, registering Atlas dynamically if needed.
- :func:`complete_authorization` -- redeem the code and persist tokens.
- :func:`refresh_stored_token` -- renew an expired access token silently.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Optional

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.oidc.oidc_client import build_authorize_url, generate_pkce_pair, generate_state
from atlas.modules.mcp_tools.mcp_oauth import (
    MCPOAuthError,
    RegisteredClient,
    ServerOAuthMetadata,
    exchange_authorization_code,
    get_server_oauth_metadata,
    refresh_access_token,
    register_client,
    revoke_token,
    validate_endpoint_url,
)
from atlas.modules.mcp_tools.oauth_client_store import get_oauth_client_store
from atlas.modules.mcp_tools.token_storage import StoredToken, get_token_storage

logger = logging.getLogger(__name__)

# Marks tokens this flow produced, so status and disconnect can tell an
# interactively authorized server from one whose token was pasted in by hand.
OAUTH_METADATA_SOURCE = "mcp_oauth_flow"

DEFAULT_CLIENT_NAME = "Atlas UI"

# Registration and refresh are both read-modify-write cycles against a shared
# store, so concurrent callers must not race. Keyed per (server, issuer) and
# per (user, server) respectively; a global lock would serialize unrelated
# servers behind one slow provider.
_registration_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_refresh_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def is_oauth_server(config: Dict[str, Any]) -> bool:
    """Whether a server config opts into the interactive OAuth flow."""
    return (config or {}).get("auth_type") == "oauth"


def server_url(config: Dict[str, Any]) -> str:
    """The MCP endpoint URL, with a scheme filled in as elsewhere in Atlas."""
    url = (config or {}).get("url") or ""
    if not url:
        raise MCPOAuthError("Server has no url configured")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _oauth_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = (config or {}).get("oauth_config") or {}
    return raw if isinstance(raw, dict) else {}


def configured_scopes(config: Dict[str, Any]) -> Optional[str]:
    """Space-separated scopes from ``oauth_config.scopes``, if set."""
    scopes = _oauth_config(config).get("scopes")
    if isinstance(scopes, str):
        return scopes.strip() or None
    if isinstance(scopes, list):
        joined = " ".join(str(item) for item in scopes if item)
        return joined or None
    return None


def client_name(config: Dict[str, Any]) -> str:
    """Client name presented at dynamic registration."""
    name = _oauth_config(config).get("client_name")
    return str(name) if name else DEFAULT_CLIENT_NAME


def configured_client_id(config: Dict[str, Any]) -> Optional[str]:
    """A pre-registered ``client_id``, for providers without DCR."""
    value = _oauth_config(config).get("client_id")
    return str(value) if value else None


def configured_client_secret(config: Dict[str, Any]) -> Optional[str]:
    value = _oauth_config(config).get("client_secret")
    return str(value) if value else None


def redirect_uri_for(server_name: str, base_url: str) -> str:
    """The Atlas callback URL registered as this server's ``redirect_uri``.

    Derived from a configured public base URL rather than the inbound Host
    header: an attacker-controlled Host would otherwise be registered as a
    redirect target with the provider.
    """
    if not base_url:
        raise MCPOAuthError(
            "No public base URL is configured for OAuth callbacks. Set "
            "BACKEND_PUBLIC_URL (or MCP_OAUTH_REDIRECT_BASE_URL) to the URL "
            "browsers use to reach Atlas."
        )
    base = base_url.rstrip("/")
    validate_endpoint_url(base, what="OAuth callback base URL")
    return f"{base}/api/mcp/auth/{server_name}/oauth/callback"


def resolve_base_url(app_settings) -> str:
    """Public base URL for building the redirect URI."""
    explicit = getattr(app_settings, "mcp_oauth_redirect_base_url", None)
    return str(explicit or getattr(app_settings, "backend_public_url", None) or "")


async def _resolve_client(
    server_name: str,
    config: Dict[str, Any],
    metadata: ServerOAuthMetadata,
    redirect_uri: str,
) -> RegisteredClient:
    """Return client credentials for this server, registering if necessary.

    Precedence: an explicitly configured ``client_id`` wins (some providers do
    not offer DCR, or an operator has pre-registered Atlas); otherwise a
    stored dynamic registration is reused; otherwise Atlas registers now.
    """
    issuer = metadata.authorization_server.issuer

    explicit_id = configured_client_id(config)
    if explicit_id:
        return RegisteredClient(
            client_id=explicit_id,
            issuer=issuer,
            redirect_uri=redirect_uri,
            client_secret=configured_client_secret(config),
        )

    store = get_oauth_client_store()

    # Serialized per server and issuer: two users starting authorization at the
    # same time would otherwise both miss the store, register separate clients,
    # and have the second registration overwrite the first -- leaving the first
    # user's callback exchanging its code under a client_id the provider never
    # issued that code to.
    async with _registration_locks[f"{server_name}|{issuer.rstrip('/')}"]:
        existing = store.get(server_name, issuer)
        if existing is not None and existing.redirect_uri == redirect_uri:
            return existing
        if existing is not None:
            # Atlas moved: the provider only accepts the redirect_uri that was
            # registered, so a stale registration must be replaced.
            logger.info(
                "Redirect URI changed for MCP server '%s'; re-registering OAuth client",
                sanitize_for_logging(server_name),
            )

        registered = await register_client(
            metadata.authorization_server,
            redirect_uri=redirect_uri,
            client_name=client_name(config),
            scopes=configured_scopes(config) or " ".join(metadata.default_scopes()) or None,
        )
        return store.put(server_name, registered)


def _existing_client(
    server_name: str, config: Dict[str, Any], issuer: str, redirect_uri: str
) -> Optional[RegisteredClient]:
    """Return already-known credentials without registering new ones.

    Used by revocation: registering a fresh client just to revoke a token that
    a *different* client_id was issued would create an orphaned registration at
    the provider and revoke nothing.
    """
    explicit_id = configured_client_id(config)
    if explicit_id:
        return RegisteredClient(
            client_id=explicit_id,
            issuer=issuer,
            redirect_uri=redirect_uri,
            client_secret=configured_client_secret(config),
        )
    return get_oauth_client_store().get(server_name, issuer)


@dataclass
class AuthorizationRequest:
    """An in-flight authorization request, held in the user's session."""

    authorize_url: str
    state: str
    code_verifier: str
    redirect_uri: str
    issuer: str


async def prepare_authorization(
    server_name: str, config: Dict[str, Any], base_url: str
) -> AuthorizationRequest:
    """Discover, register if needed, and build the provider authorize URL."""
    url = server_url(config)
    redirect_uri = redirect_uri_for(server_name, base_url)
    metadata = await get_server_oauth_metadata(url)

    authorization_server = metadata.authorization_server
    if not authorization_server.supports_pkce_s256():
        raise MCPOAuthError(
            "Authorization server does not advertise S256 PKCE, which OAuth 2.1 requires"
        )

    client = await _resolve_client(server_name, config, metadata, redirect_uri)

    verifier, challenge = generate_pkce_pair()
    state = generate_state()
    scopes = configured_scopes(config) or " ".join(metadata.default_scopes())

    extra: Dict[str, str] = {}
    resource = metadata.resource
    if resource:
        # RFC 8707 resource indicator, so the provider audience-binds the token.
        extra["resource"] = resource

    authorize_url = build_authorize_url(
        authorization_endpoint=authorization_server.authorization_endpoint,
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        # Omitted when empty rather than sent blank. The MCP flow has no ID
        # token, so it has no nonce; the single-use state bound to the session
        # is what ties the callback to this browser.
        scope=scopes or None,
        state=state,
        code_challenge=challenge,
        extra_params=extra,
    )

    logger.info(
        "Prepared OAuth authorization for MCP server '%s'",
        sanitize_for_logging(server_name),
    )
    return AuthorizationRequest(
        authorize_url=authorize_url,
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        issuer=authorization_server.issuer,
    )


async def complete_authorization(
    *,
    user_email: str,
    server_name: str,
    config: Dict[str, Any],
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> StoredToken:
    """Redeem the authorization code and persist the tokens for this user."""
    url = server_url(config)
    metadata = await get_server_oauth_metadata(url)
    client = await _resolve_client(server_name, config, metadata, redirect_uri)

    response = await exchange_authorization_code(
        metadata=metadata,
        client=client,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )

    stored = get_token_storage().store_token(
        user_email=user_email,
        server_name=server_name,
        token_value=response.access_token,
        token_type="oauth_access",
        expires_at=response.expires_at,
        scopes=response.scopes or configured_scopes(config),
        refresh_token=response.refresh_token,
        metadata={
            "source": OAUTH_METADATA_SOURCE,
            "issuer": metadata.authorization_server.issuer,
        },
    )
    logger.info(
        "Completed OAuth authorization for MCP server '%s'",
        sanitize_for_logging(server_name),
    )
    return stored


async def refresh_stored_token(
    user_email: str, server_name: str, config: Dict[str, Any]
) -> Optional[StoredToken]:
    """Renew an expired access token from its refresh token.

    Returns None when there is nothing to refresh or the provider refuses, in
    which case the caller reports the server as needing re-authorization
    rather than failing the tool call with an opaque error.
    """
    token_storage = get_token_storage()
    existing = token_storage.get_token(user_email, server_name)
    if existing is None or not existing.refresh_token:
        return None

    # Serialized per user and server. Parallel tool calls hitting the same
    # expired token would otherwise each present the same refresh token; a
    # provider that rotates refresh tokens accepts the first and rejects the
    # rest, so calls that could have used the newly stored token would instead
    # report that re-authorization is required.
    async with _refresh_locks[f"{user_email.lower()}|{server_name}"]:
        # Another caller may have refreshed while we waited for the lock.
        current = token_storage.get_valid_token(user_email, server_name)
        if current is not None:
            return current

        existing = token_storage.get_token(user_email, server_name)
        if existing is None or not existing.refresh_token:
            return None

        return await _refresh_locked(user_email, server_name, config, existing)


async def _refresh_locked(
    user_email: str,
    server_name: str,
    config: Dict[str, Any],
    existing: StoredToken,
) -> Optional[StoredToken]:
    """Perform the refresh. Caller holds the per-user/server refresh lock."""
    token_storage = get_token_storage()
    try:
        metadata = await get_server_oauth_metadata(server_url(config))
        base_url = _base_url_from_settings()
        redirect_uri = redirect_uri_for(server_name, base_url)

        # Never register during a refresh. The stored refresh token was issued
        # to one client_id; registering a new one here would rebind the server
        # to fresh credentials and invalidate every user's refresh token at
        # once -- turning one user's expired token into a silent mass logout.
        client = _existing_client(
            server_name, config, metadata.authorization_server.issuer, redirect_uri
        )
        if client is None:
            logger.info(
                "No OAuth client registration held for MCP server '%s'; the user "
                "must authorize again rather than re-registering mid-refresh",
                sanitize_for_logging(server_name),
            )
            return None
        if client.redirect_uri != redirect_uri:
            # Atlas's public URL changed. The provider will reject a refresh
            # bound to the old registration, and re-registering here would
            # break every other user, so this user re-authorizes instead.
            logger.info(
                "Redirect URI changed for MCP server '%s'; re-authorization is "
                "required before refresh can work again",
                sanitize_for_logging(server_name),
            )
            return None

        response = await refresh_access_token(
            metadata=metadata,
            client=client,
            refresh_token=existing.refresh_token,
            scopes=existing.scopes,
        )
    except MCPOAuthError as exc:
        logger.info(
            "OAuth refresh failed for MCP server '%s'; user must re-authorize: %s",
            sanitize_for_logging(server_name),
            exc,
        )
        return None

    refreshed = token_storage.update_oauth_tokens(
        user_email=user_email,
        server_name=server_name,
        access_token=response.access_token,
        expires_at=response.expires_at,
        refresh_token=response.refresh_token,
        scopes=response.scopes or existing.scopes,
    )
    logger.info(
        "Refreshed OAuth access token for MCP server '%s'",
        sanitize_for_logging(server_name),
    )
    return refreshed


def _base_url_from_settings() -> str:
    from atlas.modules.config.config_manager import get_app_settings

    return resolve_base_url(get_app_settings())


async def revoke_stored_token(
    server_name: str, config: Dict[str, Any], stored: StoredToken
) -> bool:
    """Best-effort revocation at the provider when disconnecting."""
    try:
        metadata = await get_server_oauth_metadata(server_url(config))
        redirect_uri = redirect_uri_for(server_name, _base_url_from_settings())
    except MCPOAuthError as exc:
        logger.debug("Skipping OAuth revocation for '%s': %s", sanitize_for_logging(server_name), exc)
        return False

    client = _existing_client(
        server_name, config, metadata.authorization_server.issuer, redirect_uri
    )
    if client is None:
        # Nothing was registered (or the registration is gone), so there are no
        # credentials the provider would accept for this revocation.
        logger.debug(
            "No OAuth client registration held for '%s'; skipping revocation",
            sanitize_for_logging(server_name),
        )
        return False

    revoked = False
    if stored.refresh_token:
        # Revoking the refresh token invalidates the grant; providers that
        # implement RFC 7009 fully drop the access token with it.
        revoked = await revoke_token(
            metadata=metadata,
            client=client,
            token_value=stored.refresh_token,
            token_type_hint="refresh_token",
        )
    if stored.token_value:
        revoked = (
            await revoke_token(
                metadata=metadata,
                client=client,
                token_value=stored.token_value,
                token_type_hint="access_token",
            )
            or revoked
        )
    return revoked
