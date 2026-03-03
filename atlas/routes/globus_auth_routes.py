"""Globus OAuth authentication routes for ALCF endpoint integration.

Provides OAuth 2.0 authorization code flow with Globus Auth:
- /auth/globus/login    - Initiate Globus login (redirects to Globus Auth)
- /auth/globus/callback - Handle OAuth callback and store tokens
- /auth/globus/logout   - Clear Globus tokens
- /api/globus/status    - Check Globus auth status (JSON API)

Service-specific tokens (ALCF, Globus Compute, etc.) are extracted from
the 'other_tokens' field in the Globus token response and stored in
MCPTokenStorage. Models configured with api_key_source: "globus" will
automatically use these tokens.

Updated: 2026-02-24
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from atlas.core.globus_auth import (
    build_authorize_url,
    build_scopes,
    exchange_code_for_tokens,
    generate_oauth_state,
    get_globus_auth_status,
    remove_globus_tokens,
    store_globus_tokens,
)
from atlas.core.log_sanitizer import get_current_user
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

# Browser-facing routes (redirects, not JSON APIs)
browser_router = APIRouter(prefix="/auth/globus", tags=["globus-auth"])

# JSON API routes (require auth header like all other /api/ routes)
api_router = APIRouter(prefix="/api/globus", tags=["globus-auth"])


def _get_globus_config():
    """Get Globus configuration from app settings, raising if not configured."""
    config_manager = app_factory.get_config_manager()
    settings = config_manager.app_settings

    if not settings.feature_globus_auth_enabled:
        raise HTTPException(status_code=404, detail="Globus auth is not enabled")

    if not settings.globus_client_id or not settings.globus_client_secret:
        raise HTTPException(
            status_code=500,
            detail="Globus OAuth not configured (missing GLOBUS_CLIENT_ID or GLOBUS_CLIENT_SECRET)",
        )

    return settings


@browser_router.get("/login")
async def globus_login(request: Request):
    """Initiate Globus OAuth login flow.

    Generates CSRF state, stores it in the session, and redirects
    the user to Globus Auth with the configured scopes.
    """
    settings = _get_globus_config()

    state = generate_oauth_state()
    request.session["globus_oauth_state"] = state

    redirect_uri = settings.globus_redirect_uri or str(
        request.url_for("globus_callback")
    )
    scopes = build_scopes(settings.globus_scopes)

    authorize_url = build_authorize_url(
        client_id=settings.globus_client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
    )

    logger.info("Initiating Globus login flow")
    return RedirectResponse(authorize_url, status_code=302)


@browser_router.get("/callback", name="globus_callback")
async def globus_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    """Handle Globus OAuth callback after user authorization.

    Validates state, exchanges code for tokens, extracts service-specific
    tokens from 'other_tokens', stores them in MCPTokenStorage, and
    redirects back to the app.
    """
    # Handle OAuth errors
    if error:
        logger.warning("Globus OAuth error: %s - %s", error, error_description)
        return RedirectResponse(f"/?globus_error={error}", status_code=302)

    if not code or not state:
        logger.warning("Globus callback missing code or state")
        return RedirectResponse("/?globus_error=missing_params", status_code=302)

    # Validate CSRF state
    expected_state = request.session.get("globus_oauth_state")
    if not expected_state or state != expected_state:
        logger.warning("Globus callback state mismatch (potential CSRF)")
        return RedirectResponse("/?globus_error=invalid_state", status_code=302)

    # Clear used state
    request.session.pop("globus_oauth_state", None)

    settings = _get_globus_config()
    redirect_uri = settings.globus_redirect_uri or str(
        request.url_for("globus_callback")
    )

    try:
        token_data = await exchange_code_for_tokens(
            code=code,
            redirect_uri=redirect_uri,
            client_id=settings.globus_client_id,
            client_secret=settings.globus_client_secret,
        )
    except Exception as e:
        logger.error("Globus token exchange failed: %s", e, exc_info=True)
        return RedirectResponse("/?globus_error=token_exchange_failed", status_code=302)

    # Determine user email from the token response or current auth
    # Globus identity tokens include email claims
    user_email = None

    # Try to get email from the id_token claims or userinfo
    id_token_email = token_data.get("id_token_claims", {}).get("email")
    if id_token_email:
        user_email = id_token_email

    # Fall back to existing auth header
    if not user_email:
        user_email = getattr(request.state, "user_email", None)

    # Fall back to test user in debug mode
    if not user_email:
        config_manager = app_factory.get_config_manager()
        if config_manager.app_settings.debug_mode:
            user_email = config_manager.app_settings.test_user

    if not user_email:
        logger.error("Cannot determine user email after Globus auth")
        return RedirectResponse("/?globus_error=no_user_email", status_code=302)

    # Store the user email in session for subsequent requests
    request.session["globus_user_email"] = user_email

    # Store all tokens (main + service-specific from other_tokens)
    token_count = store_globus_tokens(user_email, token_data)
    logger.info(
        "Globus auth completed for user, stored %d tokens",
        token_count,
    )

    # Remove id_token from stored data (not needed, contains PII)
    token_data.pop("id_token", None)

    return RedirectResponse("/?globus_auth=success", status_code=302)


@browser_router.get("/logout")
async def globus_logout(request: Request):
    """Clear Globus tokens and redirect to app.

    Also opens the Globus logout URL in a new window via the frontend
    to clear the Globus session cookies.
    """
    # Get user email from session or auth header
    user_email = request.session.get("globus_user_email")
    if not user_email:
        user_email = getattr(request.state, "user_email", None)

    if user_email:
        remove_globus_tokens(user_email)

    # Clear session data
    request.session.pop("globus_user_email", None)
    request.session.pop("globus_oauth_state", None)

    logger.info("Globus logout completed")
    return RedirectResponse("/?globus_auth=logged_out", status_code=302)


@api_router.get("/status")
async def globus_auth_status(current_user: str = Depends(get_current_user)):
    """Get Globus authentication status for the current user.

    Returns which Globus resource servers have valid tokens,
    including ALCF inference service.
    """
    try:
        settings = app_factory.get_config_manager().app_settings
        if not settings.feature_globus_auth_enabled:
            return {
                "enabled": False,
                "authenticated": False,
                "resource_servers": [],
                "user": current_user,
            }

        status = get_globus_auth_status(current_user)
        return {
            "enabled": True,
            **status,
        }
    except Exception as e:
        logger.error("Error getting Globus auth status: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching Globus auth status",
        )


@api_router.delete("/tokens")
async def remove_all_globus_tokens(current_user: str = Depends(get_current_user)):
    """Remove all stored Globus tokens for the current user."""
    try:
        removed = remove_globus_tokens(current_user)
        return {
            "message": f"Removed {removed} Globus tokens",
            "removed_count": removed,
        }
    except Exception as e:
        logger.error("Error removing Globus tokens: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while removing Globus tokens",
        )
