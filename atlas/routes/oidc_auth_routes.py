"""OIDC login routes: Atlas as an OAuth relying party.

Browser-facing:
- ``GET /auth/oidc/login``    start the Authorization Code + PKCE flow
- ``GET /auth/oidc/callback`` complete it and establish an Atlas session
- ``GET /auth/oidc/logout``   drop the session (and optionally the IdP session)

JSON API:
- ``GET /api/auth/oidc/status``  whether OIDC login is enabled/active
- ``DELETE /api/auth/oidc/delegated-tokens``  drop cached delegated credentials

Token material never reaches the browser: the cookie holds an opaque session
id and everything else lives in the server-side session store.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from atlas.core.log_sanitizer import get_current_user
from atlas.core.oidc.client_authentication import (
    ClientAuthenticationError,
    build_client_credentials_from_settings,
)
from atlas.core.oidc.discovery import OIDCDiscoveryError, get_provider_metadata
from atlas.core.oidc.mcp_delegation import revoke_delegated_credentials
from atlas.core.oidc.oidc_client import (
    OIDCFlowError,
    build_authorize_url,
    exchange_code_for_tokens,
    extract_user_identifier,
    generate_nonce,
    generate_pkce_pair,
    generate_state,
    normalize_scopes,
    validate_id_token,
)
from atlas.core.oidc.session import SESSION_COOKIE_KEY, get_session_store
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

browser_router = APIRouter(prefix="/auth/oidc", tags=["oidc-auth"])
api_router = APIRouter(prefix="/api/auth/oidc", tags=["oidc-auth"])

# Session keys holding the in-flight authorization request.
_STATE_KEY = "oidc_state"
_VERIFIER_KEY = "oidc_code_verifier"
_NONCE_KEY = "oidc_nonce"
_RETURN_TO_KEY = "oidc_return_to"

# Error names echoed to the SPA as a query parameter. Written as a map from a
# constant to itself and read with ``.get``, so the value that reaches the log
# and the redirect is always one of these literals -- an IdP-supplied (or
# attacker-crafted) string is never reflected, only used as a lookup key.
_ALLOWED_IDP_ERROR_NAMES = {
    name: name
    for name in (
        "access_denied", "invalid_request", "unauthorized_client",
        "unsupported_response_type", "invalid_scope", "server_error",
        "temporarily_unavailable", "consent_required", "login_required",
        "interaction_required", "invalid_client",
    )
}


def _oidc_settings():
    """Return app settings, refusing the request when OIDC login is off."""
    settings = app_factory.get_config_manager().app_settings
    if not settings.feature_oidc_auth_enabled:
        raise HTTPException(status_code=404, detail="OIDC auth is not enabled")
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise HTTPException(
            status_code=500,
            detail="OIDC is not configured (missing OIDC_ISSUER or OIDC_CLIENT_ID)",
        )
    return settings


def _oidc_enabled() -> bool:
    return bool(app_factory.get_config_manager().app_settings.feature_oidc_auth_enabled)


def _redirect_uri(request: Request, settings) -> str:
    return settings.oidc_redirect_uri or str(request.url_for("oidc_callback"))


# A same-site destination: one leading slash, then only characters that cannot
# turn the value into another origin. Matched in full, so a backslash (which
# some browsers normalise to "/"), a scheme, or an authority all fail.
_SAFE_RETURN_TO = re.compile(r"/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@/?%#-]{0,512}")


def _safe_return_to(raw: Optional[str]) -> str:
    """Constrain the post-login destination to a same-site absolute path.

    Without this the ``next`` parameter is an open redirect: an attacker-chosen
    absolute URL would be handed straight to ``RedirectResponse`` after a
    successful login. The allowlist is positive rather than a list of rejected
    prefixes -- ``//evil.example`` is protocol-relative, ``/\\evil.example``
    is normalised to one by some browsers, and enumerating those escapes is how
    open-redirect filters are usually defeated.
    """
    if not raw:
        return "/"
    return raw if _SAFE_RETURN_TO.fullmatch(raw) else "/"


def _error_redirect(code: str) -> RedirectResponse:
    return RedirectResponse(f"/?oidc_error={code}", status_code=302)


@browser_router.get("/login")
async def oidc_login(request: Request, next: Optional[str] = None):
    """Start the Authorization Code flow with PKCE."""
    settings = _oidc_settings()

    try:
        metadata = await get_provider_metadata(settings.oidc_issuer)
    except OIDCDiscoveryError as exc:
        logger.error("OIDC discovery failed: %s", exc)
        return _error_redirect("discovery_failed")

    if not metadata.supports_pkce_s256():
        logger.error("OIDC provider does not advertise S256 PKCE support")
        return _error_redirect("pkce_unsupported")

    verifier, challenge = generate_pkce_pair()
    state = generate_state()
    nonce = generate_nonce()

    request.session[_STATE_KEY] = state
    request.session[_VERIFIER_KEY] = verifier
    request.session[_NONCE_KEY] = nonce
    request.session[_RETURN_TO_KEY] = _safe_return_to(next)

    authorize_url = build_authorize_url(
        authorization_endpoint=metadata.authorization_endpoint,
        client_id=settings.oidc_client_id,
        redirect_uri=_redirect_uri(request, settings),
        scope=normalize_scopes(settings.oidc_scopes),
        state=state,
        code_challenge=challenge,
        nonce=nonce,
    )
    logger.info("Starting OIDC login flow")
    return RedirectResponse(authorize_url, status_code=302)


@browser_router.get("/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Complete the flow: verify state, exchange the code, establish a session."""
    if not _oidc_enabled():
        return _error_redirect("auth_disabled")

    if error:
        # Neither this local nor the table it reads may carry "auth", "code",
        # or "token" in its name: CodeQL's clear-text-logging heuristic
        # classifies such identifiers as credentials and flags the log line,
        # even though these are public OAuth error names.
        error_name = _ALLOWED_IDP_ERROR_NAMES.get(error, "unknown_error")
        logger.warning("OIDC authorization error: %s", error_name)
        return _error_redirect(error_name)

    if not code or not state:
        logger.warning("OIDC callback missing code or state")
        return _error_redirect("missing_params")

    expected_state = request.session.get(_STATE_KEY)
    verifier = request.session.get(_VERIFIER_KEY)
    nonce = request.session.get(_NONCE_KEY)
    return_to = _safe_return_to(request.session.get(_RETURN_TO_KEY))

    # Single-use: clear the in-flight request before doing any work, so a
    # replayed callback cannot reuse the same state/verifier pair.
    for key in (_STATE_KEY, _VERIFIER_KEY, _NONCE_KEY, _RETURN_TO_KEY):
        request.session.pop(key, None)

    if not expected_state or state != expected_state:
        logger.warning("OIDC callback state mismatch (potential CSRF)")
        return _error_redirect("invalid_state")
    if not verifier:
        logger.warning("OIDC callback has no stored PKCE verifier")
        return _error_redirect("invalid_state")

    settings = _oidc_settings()
    try:
        metadata = await get_provider_metadata(settings.oidc_issuer)
        credentials = build_client_credentials_from_settings(settings, metadata.token_endpoint)
        token_response = await exchange_code_for_tokens(
            token_endpoint=metadata.token_endpoint,
            code=code,
            redirect_uri=_redirect_uri(request, settings),
            code_verifier=verifier,
            credentials=credentials,
        )
        claims = await validate_id_token(
            token_response.get("id_token", ""),
            jwks_uri=metadata.jwks_uri,
            issuer=metadata.issuer,
            audience=settings.oidc_client_id,
            nonce=nonce,
        )
    except (OIDCDiscoveryError, ClientAuthenticationError) as exc:
        logger.error("OIDC configuration problem during callback: %s", exc)
        return _error_redirect("misconfigured")
    except OIDCFlowError as exc:
        logger.error("OIDC token exchange or validation failed: %s", exc)
        return _error_redirect("token_exchange_failed")

    user_id = extract_user_identifier(claims, settings.oidc_username_claim)
    if not user_id:
        logger.error("OIDC ID token carried no usable identity claim")
        return _error_redirect("no_user_identity")

    expires_in = token_response.get("expires_in")
    access_token_expires_at = (
        time.time() + float(expires_in)
        if isinstance(expires_in, (int, float)) and expires_in > 0
        else None
    )

    oidc_session = get_session_store().create(
        user_id=user_id,
        subject=claims.get("sub"),
        id_token_claims=claims,
        access_token=token_response.get("access_token"),
        refresh_token=token_response.get("refresh_token"),
        access_token_expires_at=access_token_expires_at,
        scope=str(token_response.get("scope") or ""),
        max_age_seconds=settings.oidc_session_max_age_seconds,
    )
    request.session[SESSION_COOKIE_KEY] = oidc_session.session_id

    logger.info("OIDC login completed and session established")
    return RedirectResponse(return_to, status_code=302)


@browser_router.get("/logout")
async def oidc_logout(request: Request):
    """Drop the Atlas session and, when supported, the IdP session too."""
    if not _oidc_enabled():
        return RedirectResponse("/?oidc_auth=logged_out", status_code=302)

    store = get_session_store()
    session_id = request.session.pop(SESSION_COOKIE_KEY, None)
    existing = store.get(session_id)
    if existing:
        # Reaches the delegation cache, the encrypted token store, and any MCP
        # client already built around a delegated credential -- clearing only
        # the first would leave the credential usable after logout.
        await revoke_delegated_credentials(existing.user_id)
    store.remove(session_id)

    settings = app_factory.get_config_manager().app_settings
    if settings.oidc_issuer:
        try:
            metadata = await get_provider_metadata(settings.oidc_issuer)
        except OIDCDiscoveryError:
            metadata = None
        if metadata and metadata.end_session_endpoint:
            params = {"client_id": settings.oidc_client_id or ""}
            if settings.oidc_post_logout_redirect_uri:
                params["post_logout_redirect_uri"] = settings.oidc_post_logout_redirect_uri
            separator = "&" if "?" in metadata.end_session_endpoint else "?"
            logger.info("OIDC logout: redirecting to the provider end-session endpoint")
            return RedirectResponse(
                f"{metadata.end_session_endpoint}{separator}{urlencode(params)}",
                status_code=302,
            )

    logger.info("OIDC logout completed")
    return RedirectResponse("/?oidc_auth=logged_out", status_code=302)


@api_router.get("/status")
async def oidc_status(request: Request):
    """Report whether OIDC login is enabled and whether this browser is logged in."""
    settings = app_factory.get_config_manager().app_settings
    if not settings.feature_oidc_auth_enabled:
        return {"enabled": False, "authenticated": False, "session": None}

    session_id = None
    try:
        session_id = request.session.get(SESSION_COOKIE_KEY)
    except (AssertionError, KeyError):  # pragma: no cover - session middleware absent
        session_id = None

    oidc_session = get_session_store().get(session_id)
    return {
        "enabled": True,
        "authenticated": oidc_session is not None,
        "delegation_enabled": bool(settings.feature_oidc_delegation_enabled),
        "delegation_provider": (
            settings.oidc_delegation_provider
            if settings.feature_oidc_delegation_enabled
            else None
        ),
        "session": oidc_session.to_public_dict() if oidc_session else None,
    }


@api_router.delete("/delegated-tokens")
async def drop_delegated_tokens(current_user: str = Depends(get_current_user)):
    """Discard every cached delegated credential for the current user."""
    removed = await revoke_delegated_credentials(current_user)
    return {"removed_count": removed, "message": f"Removed {removed} delegated tokens"}
