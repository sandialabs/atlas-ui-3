"""MCP Authentication routes for per-user API key and token management.

This module provides user-facing endpoints for managing authentication tokens
with MCP servers. Each user's tokens are stored separately and securely encrypted.

Users can manually upload API keys, JWTs, or bearer tokens for MCP servers that
require authentication. This supports any type of token that can be passed as a
bearer token in the Authorization header.

Updated: 2025-01-21
"""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from atlas.core.auth import is_user_in_group
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.mcp_tools import mcp_oauth_service
from atlas.modules.mcp_tools.mcp_oauth import MCPOAuthError
from atlas.modules.mcp_tools.oauth_pending_store import (
    PendingAuthorization,
    get_pending_store,
)
from atlas.modules.mcp_tools.token_storage import get_token_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/auth", tags=["mcp-auth"])

# Session key holding the ``state`` values this browser has started flows for.
# Only the states live in the cookie; the PKCE verifier and the rest of the
# record are held server-side (see oauth_pending_store) because a Starlette
# session is signed but readable.
_PENDING_KEY = "mcp_oauth_states"

# Cap on states carried in the cookie, so repeatedly hitting /oauth/start
# cannot grow it without bound.
_MAX_PENDING = 16

# Error names reflected to the SPA as a query parameter. Written as a map from
# a constant to itself and read with ``.get``, so the value that reaches the
# log and the redirect is always one of these literals -- a provider-supplied
# (or attacker-crafted) string is never echoed back, only used as a lookup key.
_ALLOWED_PROVIDER_ERROR_NAMES = {
    name: name
    for name in (
        "access_denied", "invalid_request", "unauthorized_client",
        "unsupported_response_type", "invalid_scope", "server_error",
        "temporarily_unavailable", "consent_required", "login_required",
        "interaction_required", "invalid_client", "invalid_grant",
    )
}



def _drop_user_tool_cache(mcp_manager, user_email: str, server_name: str) -> None:
    """Forget the tools discovered for this user/server under the old token."""
    clear = getattr(mcp_manager, "clear_user_tool_cache", None)
    if clear is None:
        return
    try:
        clear(user_email, server_name)
    except Exception:
        logger.debug("Could not clear per-user tool cache", exc_info=True)


async def _invalidate_user_client_quietly(
    mcp_manager, user_email: str, server_name: str
) -> None:
    """Drop this user's cached client for the server, never raising.

    Called after the token is already persisted, where an exception would
    report a failed upload for one that in fact succeeded. The stale client is
    also re-created on next use, so failing to close it costs a connection,
    not correctness.
    """
    if mcp_manager is None:
        return
    invalidate = getattr(mcp_manager, "_invalidate_user_client", None)
    if invalidate is None:
        return
    try:
        await invalidate(user_email, server_name)
    except Exception:
        logger.warning(
            "Could not invalidate the cached client for MCP server '%s' after a "
            "token upload; it will be rebuilt on next use",
            sanitize_for_logging(server_name),
            exc_info=True,
        )


async def _rediscover_after_authorization(mcp_manager, user_email: str, server_name: str) -> None:
    """Re-run tool discovery for one server as the user who just authorized.

    A server that gates ``tools/list`` behind authorization contributed no
    tools to the startup sweep, and nothing else re-runs discovery -- so
    without this the user completes the flow, is told they are connected, and
    still sees no tools (issue #912). Best-effort: a failure here must not turn
    a successful authorization into an error.
    """
    if mcp_manager is None:
        return
    _drop_user_tool_cache(mcp_manager, user_email, server_name)
    discover = getattr(mcp_manager, "discover_tools_for_user", None)
    if discover is None:
        return
    try:
        await discover(user_email, server_name, force=True)
    except Exception:
        logger.warning(
            "Post-authorization tool discovery failed for MCP server '%s'",
            sanitize_for_logging(server_name),
            exc_info=True,
        )


class TokenUpload(BaseModel):
    """Request body for uploading an API key or token."""
    token: str
    expires_at: Optional[float] = None  # Unix timestamp, or None for no expiry
    scopes: Optional[str] = None  # Space-separated scopes (optional)


# --- Routes ---


@router.get("/status")
async def get_auth_status(current_user: str = Depends(get_current_user)):
    """Get authentication status for all MCP servers accessible to the user.

    Returns information about which servers require authentication,
    which the user has authenticated with, and token status.
    """
    try:
        mcp_manager = app_factory.get_mcp_manager()
        token_storage = get_token_storage()

        # Get servers the user is authorized to access
        authorized_servers = await mcp_manager.get_authorized_servers(
            current_user, is_user_in_group
        )

        # Get user's current auth status
        user_auth_status = token_storage.get_user_auth_status(current_user)

        # Build response with server auth requirements and user's status
        servers_status = []

        for server_name in authorized_servers:
            server_config = mcp_manager.servers_config.get(server_name, {})
            auth_type = server_config.get("auth_type", "none")

            # Get user's token status for this server
            token_status = user_auth_status.get(server_name)

            server_info = {
                "server_name": server_name,
                "auth_type": auth_type,
                "auth_required": auth_type != "none",
                "authenticated": token_status is not None,
                "description": server_config.get("description", ""),
                # The Tools panel synthesizes a row from this for a server with
                # no discovered tools, and a compliance filter has to be able to
                # judge that row on the same footing as a real one.
                "compliance_level": server_config.get("compliance_level"),
            }

            # An oauth server is connected by visiting Atlas's own start
            # route, not by pasting a token, so the UI needs the URL here.
            if auth_type == "oauth":
                server_info["oauth_start_url"] = (
                    f"/api/mcp/auth/{quote(server_name, safe='')}/oauth/start"
                )

            # Add token details if authenticated
            if token_status:
                server_info.update({
                    "token_type": token_status["token_type"],
                    "is_expired": token_status["is_expired"],
                    "expires_at": token_status["expires_at"],
                    "time_until_expiry": token_status["time_until_expiry"],
                    "has_refresh_token": token_status["has_refresh_token"],
                    "scopes": token_status["scopes"],
                })

            servers_status.append(server_info)

        return {
            "servers": servers_status,
            "user": current_user,
        }

    except Exception as e:
        logger.error(f"Error getting auth status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching auth status")


@router.post("/{server_name}/token")
async def upload_token(
    server_name: str,
    token_data: TokenUpload,
    current_user: str = Depends(get_current_user),
):
    """Upload an API key or bearer token for an MCP server.

    This allows users to manually provide tokens for servers that require
    authentication. Tokens can be:
    - API keys
    - JWT tokens
    - Bearer tokens
    - Any other string token that can be used in Authorization header

    The token will be securely encrypted and stored per-user.
    """
    try:
        mcp_manager = app_factory.get_mcp_manager()
        token_storage = get_token_storage()

        # Verify server exists and user has access
        authorized_servers = await mcp_manager.get_authorized_servers(
            current_user, is_user_in_group
        )

        if server_name not in authorized_servers:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to access server '{server_name}'"
            )

        # Verify server accepts token authentication
        server_config = mcp_manager.servers_config.get(server_name, {})
        auth_type = server_config.get("auth_type", "none")

        if auth_type not in ("jwt", "bearer", "api_key", "oauth"):
            raise HTTPException(
                status_code=400,
                detail=f"Server '{server_name}' does not accept token authentication (auth_type: {auth_type})"
            )

        # Validate token is not empty
        if not token_data.token or not token_data.token.strip():
            raise HTTPException(
                status_code=400,
                detail="Token cannot be empty"
            )

        # Determine token type based on server config
        token_type = "bearer"
        if auth_type == "jwt":
            token_type = "jwt"
        elif auth_type == "api_key":
            token_type = "api_key"

        # Store the token
        stored_token = token_storage.store_token(
            user_email=current_user,
            server_name=server_name,
            token_value=token_data.token.strip(),
            token_type=token_type,
            expires_at=token_data.expires_at,
            scopes=token_data.scopes,
        )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User uploaded token for MCP server '{sanitized_server}'")

        # The stored token supersedes whatever this user's client and tool
        # catalogue were built from, including the empty catalogue left by an
        # anonymous startup sweep that the server answered with a 401.
        # The token is stored by this point, so nothing here may turn a
        # successful upload into an error. _invalidate_user_client closes live
        # sessions and can raise on its own, so it is best-effort like the
        # rediscovery that follows it, not merely guarded against a missing
        # manager.
        await _invalidate_user_client_quietly(mcp_manager, current_user, server_name)
        await _rediscover_after_authorization(mcp_manager, current_user, server_name)

        return {
            "message": f"Token stored for server '{server_name}'",
            "server_name": server_name,
            "token_type": stored_token.token_type,
            "expires_at": stored_token.expires_at,
            "scopes": stored_token.scopes,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while uploading token")


@router.delete("/{server_name}/token")
async def remove_token(
    server_name: str,
    current_user: str = Depends(get_current_user),
):
    """Remove stored token for an MCP server (disconnect).

    This removes the user's authentication token for the specified server.
    The user will need to re-authenticate to use the server's tools.
    """
    try:
        token_storage = get_token_storage()

        # Read the record before deleting it: revoking at the provider needs
        # the token values, and disconnect must still succeed locally when the
        # provider is unreachable.
        existing = token_storage.get_token(current_user, server_name)

        # Remove the token
        removed = token_storage.remove_token(current_user, server_name)

        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No token found for server '{server_name}'"
            )

        # Invalidate any cached client for this user/server combination
        tool_manager = app_factory.get_mcp_manager()
        if tool_manager is not None:
            await tool_manager._invalidate_user_client(current_user, server_name)
            _drop_user_tool_cache(tool_manager, current_user, server_name)
            logger.debug(f"Invalidated cached client for server '{server_name}'")

        # Best-effort revocation at the provider, after the local record is
        # gone so a provider outage cannot block disconnect. Gated on the
        # token's own type rather than on config alone: only a credential this
        # flow issued has something to revoke, and a hand-uploaded bearer
        # token must not be sent to a revocation endpoint.
        revoked = False
        if existing is not None and getattr(existing, "token_type", None) == "oauth_access":
            server_config = {}
            if tool_manager is not None:
                configs = getattr(tool_manager, "servers_config", None)
                if isinstance(configs, dict):
                    server_config = configs.get(server_name) or {}
            if mcp_oauth_service.is_oauth_server(server_config):
                try:
                    revoked = await mcp_oauth_service.revoke_stored_token(
                        server_name, server_config, existing
                    )
                except Exception as exc:
                    # The local token is already deleted at this point, so the
                    # disconnect has succeeded from the user's perspective. A
                    # store or provider error must not turn that into a 500
                    # that leaves the UI thinking it is still connected.
                    logger.warning(
                        "OAuth revocation failed for '%s': %s",
                        sanitize_for_logging(server_name),
                        exc,
                    )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User removed token for MCP server '{sanitized_server}'")

        return {
            "message": f"Token removed for server '{server_name}'",
            "server_name": server_name,
            "revoked_at_provider": revoked,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while removing token")


# --- OAuth 2.1 authorization flow ----------------------------------------
#
# Browser-facing routes. They live on the same ``/api/mcp/auth`` prefix as the
# JSON endpoints above because that is the path Atlas already advertises to
# the frontend in ``AuthenticationRequiredException.oauth_start_url``; they
# return redirects rather than JSON.


async def _authorized_oauth_config(server_name: str, current_user: str) -> Dict[str, Any]:
    """Resolve a server's config, enforcing access and ``auth_type: oauth``.

    Both OAuth routes go through this, so an unauthorized user cannot use the
    flow to probe which servers exist or to mint a token for a server they are
    not in the groups for.
    """
    mcp_manager = app_factory.get_mcp_manager()
    authorized_servers = await mcp_manager.get_authorized_servers(
        current_user, is_user_in_group
    )
    if server_name not in authorized_servers:
        raise HTTPException(
            status_code=403, detail=f"Not authorized to access server '{server_name}'"
        )

    config = mcp_manager.servers_config.get(server_name, {})
    if not mcp_oauth_service.is_oauth_server(config):
        raise HTTPException(
            status_code=400,
            detail=f"Server '{server_name}' is not configured for OAuth authorization",
        )
    return config


def _oauth_redirect(server_name: str, **params: str) -> RedirectResponse:
    """Send the browser back to the SPA with a machine-readable outcome.

    The query string is encoded rather than interpolated: a server name is
    operator-supplied, and one containing ``&``, ``#`` or ``=`` would
    otherwise split into extra parameters and confuse the frontend.
    """
    query = urlencode({"mcp_auth_server": server_name, **params})
    return RedirectResponse(f"/?{query}", status_code=302)


def _oauth_error_redirect(server_name: str, code: str) -> RedirectResponse:
    """Send the browser back to the SPA with a machine-readable error code."""
    return _oauth_redirect(server_name, mcp_auth_error=code)


def _session_available(request: Request) -> bool:
    """Whether SessionMiddleware is installed on this deployment.

    The flow needs a browser session for its PKCE verifier and single-use
    state. Rather than raising a 500 when a deployment has no session
    configured, both routes report a specific, actionable error.
    """
    return "session" in request.scope


def _session_states(request: Request) -> List[str]:
    """The states this browser has open flows for, newest last."""
    raw = request.session.get(_PENDING_KEY) or []
    return [item for item in raw if isinstance(item, str)][-_MAX_PENDING:]


@router.get("/{server_name}/oauth/start")
async def start_oauth(
    request: Request,
    server_name: str,
    current_user: str = Depends(get_current_user),
):
    """Begin the OAuth 2.1 Authorization Code + PKCE flow for an MCP server.

    Discovers the authorization server from the MCP endpoint, registers Atlas
    dynamically if it holds no credentials yet, and redirects the browser to
    the provider. The PKCE verifier and a single-use ``state`` are held in the
    user's Atlas session; nothing sensitive reaches the browser.
    """
    config = await _authorized_oauth_config(server_name, current_user)

    if not _session_available(request):
        logger.error(
            "MCP OAuth requires a browser session, but no session middleware is "
            "installed. Set MCP_OAUTH_SESSION_SECRET (or enable OIDC/Globus login)."
        )
        return _oauth_error_redirect(server_name, "session_unavailable")

    app_settings = app_factory.get_config_manager().app_settings
    base_url = mcp_oauth_service.resolve_base_url(app_settings)

    try:
        prepared = await mcp_oauth_service.prepare_authorization(
            server_name, config, base_url
        )
    except MCPOAuthError as exc:
        logger.error(
            "Could not start OAuth for MCP server '%s': %s",
            sanitize_for_logging(server_name),
            exc,
        )
        return _oauth_error_redirect(server_name, "discovery_failed")
    except Exception:
        # A malformed provider response can surface as ValueError/TypeError
        # from deep inside parsing, and an unreachable one as OSError. Neither
        # should strand the user on a raw 500 page mid-flow.
        logger.exception(
            "Unexpected error starting OAuth for MCP server '%s'",
            sanitize_for_logging(server_name),
        )
        return _oauth_error_redirect(server_name, "discovery_failed")

    # Only the state goes in the cookie. The verifier is the secret binding
    # the code to this client, so it stays server-side.
    get_pending_store().put(
        prepared.state,
        PendingAuthorization(
            server_name=server_name,
            # Bound to the user who started the flow: a callback replayed in
            # someone else's browser must not mint a token for this account.
            user=current_user.lower(),
            code_verifier=prepared.code_verifier,
            redirect_uri=prepared.redirect_uri,
            created_at=time.time(),
            client_id=prepared.client_id,
            issuer=prepared.issuer,
        ),
    )
    request.session[_PENDING_KEY] = (_session_states(request) + [prepared.state])[
        -_MAX_PENDING:
    ]

    logger.info(
        "Starting MCP OAuth flow for server '%s'", sanitize_for_logging(server_name)
    )
    return RedirectResponse(prepared.authorize_url, status_code=302)


@router.get("/{server_name}/oauth/callback", name="mcp_oauth_callback")
async def oauth_callback(
    request: Request,
    server_name: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    current_user: str = Depends(get_current_user),
):
    """Complete the flow: validate state, redeem the code, store the tokens."""
    if not _session_available(request):
        logger.error("MCP OAuth callback arrived but no session middleware is installed")
        return _oauth_error_redirect(server_name, "session_unavailable")

    states = _session_states(request)

    # Single-use on both sides: drop the state from the cookie and take the
    # server-side record before doing any work, so a replayed callback cannot
    # reuse the same state/verifier pair.
    known_to_browser = bool(state) and state in states
    if state:
        request.session[_PENDING_KEY] = [item for item in states if item != state]
    # Looked up only when the browser presented a state it actually started:
    # otherwise a third party could burn someone else's pending record.
    entry = get_pending_store().take(state) if known_to_browser else None

    if error:
        error_name = _ALLOWED_PROVIDER_ERROR_NAMES.get(error, "unknown_error")
        logger.warning(
            "MCP OAuth authorization error for server '%s': %s",
            sanitize_for_logging(server_name),
            error_name,
        )
        return _oauth_error_redirect(server_name, error_name)

    if not code or not state:
        logger.warning("MCP OAuth callback missing required parameters")
        return _oauth_error_redirect(server_name, "missing_params")

    if entry is None:
        # Distinguished so an operator reading logs can tell a genuine CSRF
        # attempt from a user who took too long, or from a cookie the browser
        # dropped between start and callback.
        if not known_to_browser:
            logger.warning(
                "MCP OAuth callback state is absent from the browser session "
                "(potential CSRF, or the session cookie was lost)"
            )
        else:
            logger.warning(
                "MCP OAuth callback state was in the session but has no pending "
                "record left: it expired, was already used, or was evicted"
            )
        return _oauth_error_redirect(server_name, "invalid_state")

    # The state was issued for one server and one user. A callback that
    # arrives for a different server, or in a session that has since become a
    # different user, is rejected rather than silently storing the token.
    if entry.server_name != server_name:
        logger.warning("MCP OAuth callback state does not match the server in the path")
        return _oauth_error_redirect(server_name, "invalid_state")
    if entry.user != (current_user or "").lower():
        logger.warning("MCP OAuth callback state belongs to a different user")
        return _oauth_error_redirect(server_name, "invalid_state")

    try:
        config = await _authorized_oauth_config(server_name, current_user)
    except HTTPException:
        logger.warning(
            "MCP OAuth callback for a server the user may no longer access: '%s'",
            sanitize_for_logging(server_name),
        )
        return _oauth_error_redirect(server_name, "not_authorized")

    try:
        await mcp_oauth_service.complete_authorization(
            user_email=current_user,
            server_name=server_name,
            config=config,
            code=code,
            code_verifier=entry.code_verifier,
            redirect_uri=entry.redirect_uri,
            client_id=entry.client_id,
            issuer=entry.issuer,
        )
    except MCPOAuthError as exc:
        logger.error(
            "MCP OAuth token exchange failed for server '%s': %s",
            sanitize_for_logging(server_name),
            exc,
        )
        return _oauth_error_redirect(server_name, "token_exchange_failed")
    except Exception:
        logger.exception(
            "Unexpected error completing OAuth for MCP server '%s'",
            sanitize_for_logging(server_name),
        )
        return _oauth_error_redirect(server_name, "token_exchange_failed")

    # A newly authorized server may have a cached client built when no token
    # existed; drop it so the next call picks the token up.
    mcp_manager = app_factory.get_mcp_manager()
    if mcp_manager is not None:
        await mcp_manager._invalidate_user_client(current_user, server_name)
        # Servers that gate tools/list behind authorization have no tools until
        # this runs, so it is part of completing the flow, not an optimisation.
        await _rediscover_after_authorization(mcp_manager, current_user, server_name)

    logger.info(
        "MCP OAuth authorization complete for server '%s'",
        sanitize_for_logging(server_name),
    )
    return _oauth_redirect(server_name, mcp_auth_success="1")
