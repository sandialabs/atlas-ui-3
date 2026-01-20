"""MCP Authentication routes for per-user OAuth and JWT token management.

This module provides user-facing endpoints for authenticating with MCP servers
that require OAuth 2.1 or manual JWT tokens. Each user's tokens are stored
separately and securely encrypted.

Updated: 2025-01-19
"""

import base64
import hashlib
import logging
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.auth import is_user_in_group
from core.log_sanitizer import get_current_user, sanitize_for_logging
from infrastructure.app_factory import app_factory
from modules.mcp_tools.token_storage import get_token_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/auth", tags=["mcp-auth"])

# In-memory storage for OAuth state (PKCE verifiers, etc.)
# In production, this should be Redis or similar for multi-instance deployments
_oauth_state_store: Dict[str, Dict[str, Any]] = {}


class TokenUpload(BaseModel):
    """Request body for uploading a JWT/token."""
    token: str
    expires_at: Optional[float] = None  # Unix timestamp, or None for no expiry
    scopes: Optional[str] = None  # Space-separated scopes


class OAuthStartResponse(BaseModel):
    """Response from OAuth start endpoint."""
    authorization_url: str
    state: str


# --- Helper Functions ---


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge.

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    # Generate 32-byte random verifier (43 chars base64url)
    code_verifier = secrets.token_urlsafe(32)

    # Create SHA256 hash and base64url encode
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    return code_verifier, code_challenge


def _generate_state() -> str:
    """Generate random state parameter for OAuth."""
    return secrets.token_urlsafe(32)


def _store_oauth_state(
    state: str,
    user_email: str,
    server_name: str,
    code_verifier: str,
    redirect_uri: str,
    ttl_seconds: int = 600,
) -> None:
    """Store OAuth state for callback verification.

    Args:
        state: OAuth state parameter
        user_email: User initiating the flow
        server_name: MCP server being authenticated
        code_verifier: PKCE code verifier
        redirect_uri: Redirect URI used
        ttl_seconds: Time-to-live in seconds
    """
    _oauth_state_store[state] = {
        "user_email": user_email,
        "server_name": server_name,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
        "expires_at": time.time() + ttl_seconds,
    }


def _get_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Retrieve and validate OAuth state.

    Returns:
        State data if valid and not expired, None otherwise
    """
    state_data = _oauth_state_store.get(state)
    if state_data is None:
        return None

    # Check expiration
    if time.time() > state_data["expires_at"]:
        del _oauth_state_store[state]
        return None

    return state_data


def _consume_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Retrieve and remove OAuth state (single use).

    Returns:
        State data if valid and not expired, None otherwise
    """
    state_data = _get_oauth_state(state)
    if state_data:
        del _oauth_state_store[state]
    return state_data


def _cleanup_expired_states() -> None:
    """Remove expired OAuth states."""
    now = time.time()
    expired = [
        state for state, data in _oauth_state_store.items()
        if now > data["expires_at"]
    ]
    for state in expired:
        del _oauth_state_store[state]


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
            }

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

            # Add OAuth config if applicable
            if auth_type == "oauth":
                oauth_config = server_config.get("oauth_config", {})
                server_info["oauth_config"] = {
                    "scopes": oauth_config.get("scopes", []),
                    "client_name": oauth_config.get("client_name", "Atlas UI"),
                }

            servers_status.append(server_info)

        return {
            "servers": servers_status,
            "user": current_user,
        }

    except Exception as e:
        logger.error(f"Error getting auth status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_name}/token")
async def upload_token(
    server_name: str,
    token_data: TokenUpload,
    current_user: str = Depends(get_current_user),
):
    """Upload a JWT or bearer token for an MCP server.

    This allows users to manually provide tokens for servers that use
    JWT authentication or for cases where OAuth flow cannot be used.
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

        # Verify server is configured for JWT auth
        server_config = mcp_manager.servers_config.get(server_name, {})
        auth_type = server_config.get("auth_type", "none")

        if auth_type not in ("jwt", "bearer", "oauth"):
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

        # Store the token
        stored_token = token_storage.store_token(
            user_email=current_user,
            server_name=server_name,
            token_value=token_data.token.strip(),
            token_type="jwt" if auth_type == "jwt" else "bearer",
            expires_at=token_data.expires_at,
            scopes=token_data.scopes,
        )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User uploaded token for MCP server '{sanitized_server}'")

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
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_name}/token")
async def remove_token(
    server_name: str,
    current_user: str = Depends(get_current_user),
):
    """Remove stored token for an MCP server (disconnect)."""
    try:
        token_storage = get_token_storage()

        # Remove the token
        removed = token_storage.remove_token(current_user, server_name)

        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No token found for server '{server_name}'"
            )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"User removed token for MCP server '{sanitized_server}'")

        return {
            "message": f"Token removed for server '{server_name}'",
            "server_name": server_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing token: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_name}/oauth/start")
async def start_oauth_flow(
    server_name: str,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Start OAuth 2.1 authorization flow for an MCP server.

    Returns the authorization URL that the frontend should open in a browser.
    The user will authenticate with the OAuth provider and be redirected back
    to the callback endpoint.
    """
    try:
        mcp_manager = app_factory.get_mcp_manager()

        # Verify server exists and user has access
        authorized_servers = await mcp_manager.get_authorized_servers(
            current_user, is_user_in_group
        )

        if server_name not in authorized_servers:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to access server '{server_name}'"
            )

        # Verify server is configured for OAuth
        server_config = mcp_manager.servers_config.get(server_name, {})
        auth_type = server_config.get("auth_type", "none")

        if auth_type != "oauth":
            raise HTTPException(
                status_code=400,
                detail=f"Server '{server_name}' is not configured for OAuth (auth_type: {auth_type})"
            )

        # Get OAuth configuration
        oauth_config = server_config.get("oauth_config", {})
        server_url = server_config.get("url")

        if not server_url:
            raise HTTPException(
                status_code=400,
                detail=f"Server '{server_name}' has no URL configured"
            )

        # Discover OAuth endpoints from the server
        # MCP servers should expose /.well-known/oauth-authorization-server
        import httpx
        parsed_url = urlparse(server_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                discovery_response = await client.get(discovery_url)
                if discovery_response.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Failed to discover OAuth endpoints from server: {discovery_response.status_code}"
                    )
                oauth_metadata = discovery_response.json()
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to OAuth server: {str(e)}"
            )

        authorization_endpoint = oauth_metadata.get("authorization_endpoint")
        if not authorization_endpoint:
            raise HTTPException(
                status_code=502,
                detail="OAuth server did not provide authorization_endpoint"
            )

        # Generate PKCE pair and state
        code_verifier, code_challenge = _generate_pkce_pair()
        state = _generate_state()

        # Build callback URL
        callback_url = str(request.url_for("oauth_callback", server_name=server_name))

        # Store state for callback verification
        _store_oauth_state(
            state=state,
            user_email=current_user,
            server_name=server_name,
            code_verifier=code_verifier,
            redirect_uri=callback_url,
        )

        # Build authorization URL
        scopes = oauth_config.get("scopes", [])
        scope_string = " ".join(scopes) if scopes else ""

        auth_params = {
            "response_type": "code",
            "client_id": oauth_config.get("client_name", "Atlas UI"),
            "redirect_uri": callback_url,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if scope_string:
            auth_params["scope"] = scope_string

        authorization_url = f"{authorization_endpoint}?{urlencode(auth_params)}"

        # Cleanup expired states periodically
        _cleanup_expired_states()

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"Started OAuth flow for MCP server '{sanitized_server}'")

        return OAuthStartResponse(
            authorization_url=authorization_url,
            state=state,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting OAuth flow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_name}/oauth/callback", name="oauth_callback")
async def oauth_callback(
    server_name: str,
    code: str = Query(..., description="Authorization code from OAuth provider"),
    state: str = Query(..., description="State parameter for verification"),
    error: Optional[str] = Query(None, description="Error code if authorization failed"),
    error_description: Optional[str] = Query(None, description="Error description"),
):
    """Handle OAuth 2.1 callback from the authorization server.

    This endpoint receives the authorization code and exchanges it for tokens.
    After successful token exchange, it displays a success page that can be
    closed by the user.
    """
    try:
        # Handle OAuth errors
        if error:
            error_msg = error_description or error
            logger.warning(f"OAuth error for server '{server_name}': {error_msg}")
            return HTMLResponse(
                content=f"""
                <!DOCTYPE html>
                <html>
                <head><title>Authentication Failed</title></head>
                <body style="font-family: system-ui; padding: 40px; text-align: center;">
                    <h1>Authentication Failed</h1>
                    <p>{error_msg}</p>
                    <p>You can close this window and try again.</p>
                    <script>
                        // Notify parent window of failure
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'oauth_error',
                                server: '{server_name}',
                                error: '{error}'
                            }}, '*');
                        }}
                    </script>
                </body>
                </html>
                """,
                status_code=400,
            )

        # Retrieve and validate state
        state_data = _consume_oauth_state(state)
        if state_data is None:
            logger.warning(f"Invalid or expired OAuth state for server '{server_name}'")
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head><title>Authentication Failed</title></head>
                <body style="font-family: system-ui; padding: 40px; text-align: center;">
                    <h1>Authentication Failed</h1>
                    <p>Invalid or expired authentication session. Please try again.</p>
                    <script>
                        if (window.opener) {
                            window.opener.postMessage({
                                type: 'oauth_error',
                                error: 'invalid_state'
                            }, '*');
                        }
                    </script>
                </body>
                </html>
                """,
                status_code=400,
            )

        user_email = state_data["user_email"]
        code_verifier = state_data["code_verifier"]
        redirect_uri = state_data["redirect_uri"]

        # Get server config for token endpoint discovery
        mcp_manager = app_factory.get_mcp_manager()
        server_config = mcp_manager.servers_config.get(server_name, {})
        server_url = server_config.get("url")
        oauth_config = server_config.get("oauth_config", {})

        if not server_url:
            raise HTTPException(status_code=500, detail="Server URL not configured")

        # Discover token endpoint
        import httpx
        parsed_url = urlparse(server_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

        async with httpx.AsyncClient(timeout=10.0) as client:
            discovery_response = await client.get(discovery_url)
            if discovery_response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail="Failed to discover OAuth token endpoint"
                )
            oauth_metadata = discovery_response.json()

        token_endpoint = oauth_metadata.get("token_endpoint")
        if not token_endpoint:
            raise HTTPException(
                status_code=502,
                detail="OAuth server did not provide token_endpoint"
            )

        # Exchange authorization code for tokens
        token_request_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": oauth_config.get("client_name", "Atlas UI"),
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                token_endpoint,
                data=token_request_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if token_response.status_code != 200:
            error_detail = token_response.text
            logger.error(f"Token exchange failed: {error_detail}")
            return HTMLResponse(
                content=f"""
                <!DOCTYPE html>
                <html>
                <head><title>Authentication Failed</title></head>
                <body style="font-family: system-ui; padding: 40px; text-align: center;">
                    <h1>Authentication Failed</h1>
                    <p>Failed to exchange authorization code for tokens.</p>
                    <p>You can close this window and try again.</p>
                    <script>
                        if (window.opener) {{
                            window.opener.postMessage({{
                                type: 'oauth_error',
                                server: '{server_name}',
                                error: 'token_exchange_failed'
                            }}, '*');
                        }}
                    </script>
                </body>
                </html>
                """,
                status_code=400,
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        scope = token_data.get("scope", "")

        if not access_token:
            raise HTTPException(
                status_code=502,
                detail="OAuth server did not return access_token"
            )

        # Calculate expiration time
        expires_at = None
        if expires_in:
            expires_at = time.time() + int(expires_in)

        # Store tokens
        token_storage = get_token_storage()
        token_storage.update_oauth_tokens(
            user_email=user_email,
            server_name=server_name,
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=refresh_token,
            scopes=scope,
        )

        sanitized_server = sanitize_for_logging(server_name)
        logger.info(f"OAuth flow completed for MCP server '{sanitized_server}'")

        # Return success page
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head><title>Authentication Successful</title></head>
            <body style="font-family: system-ui; padding: 40px; text-align: center;">
                <h1>Authentication Successful</h1>
                <p>You have successfully authenticated with <strong>{server_name}</strong>.</p>
                <p>You can close this window now.</p>
                <script>
                    // Notify parent window of success
                    if (window.opener) {{
                        window.opener.postMessage({{
                            type: 'oauth_success',
                            server: '{server_name}'
                        }}, '*');
                        // Auto-close after brief delay
                        setTimeout(() => window.close(), 2000);
                    }}
                </script>
            </body>
            </html>
            """,
            status_code=200,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in OAuth callback: {e}", exc_info=True)
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head><title>Authentication Failed</title></head>
            <body style="font-family: system-ui; padding: 40px; text-align: center;">
                <h1>Authentication Failed</h1>
                <p>An unexpected error occurred. Please try again.</p>
                <script>
                    if (window.opener) {{
                        window.opener.postMessage({{
                            type: 'oauth_error',
                            server: '{server_name}',
                            error: 'unexpected_error'
                        }}, '*');
                    }}
                </script>
            </body>
            </html>
            """,
            status_code=500,
        )
