"""OIDC Authorization Code flow with PKCE.

Login shape: browser -> Atlas -> IdP (Authorization Code + PKCE) -> Atlas
session. Atlas holds the resulting tokens server-side; the browser only ever
receives a signed session cookie carrying an opaque session id.

PKCE is always used even though Atlas is a confidential client: it protects
against authorization-code interception independently of client
authentication, and OAuth 2.1 requires it for the code grant.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
import jwt

from atlas.core.oidc.client_authentication import ClientCredentials

logger = logging.getLogger(__name__)

TOKEN_TIMEOUT_SECONDS = 20.0

# Clock skew allowance when validating ID token time claims.
_ID_TOKEN_LEEWAY_SECONDS = 60

_ID_TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256", "PS384", "PS512"]

# JWKS clients are cached per URI: each holds its own key cache, so rebuilding
# one per login would refetch the key set on every request.
_jwks_clients: Dict[str, "jwt.PyJWKClient"] = {}


class OIDCFlowError(RuntimeError):
    """Raised when a step of the authorization code flow fails."""


def generate_pkce_pair() -> Tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` pair using S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def generate_state() -> str:
    """Generate a CSRF ``state`` value for the authorization request."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Generate a replay-protection ``nonce`` bound into the ID token."""
    return secrets.token_urlsafe(32)


def build_authorize_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    nonce: str,
    extra_params: Optional[Dict[str, str]] = None,
) -> str:
    """Build the IdP authorization URL for the code flow with PKCE."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if extra_params:
        params.update(extra_params)
    separator = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{separator}{urlencode(params)}"


def normalize_scopes(configured: Optional[str]) -> str:
    """Ensure ``openid`` is present and scopes are de-duplicated in order."""
    tokens = (configured or "").split()
    if "openid" not in tokens:
        tokens.insert(0, "openid")
    seen = set()
    ordered = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return " ".join(ordered)


async def _post_token_request(
    token_endpoint: str,
    data: Dict[str, str],
    credentials: ClientCredentials,
) -> Dict[str, Any]:
    payload = {**data, **credentials.form_fields}
    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            token_endpoint,
            data=payload,
            auth=credentials.basic_auth,
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        # OAuth error bodies carry an `error` code; log that but never the body
        # verbatim, which can echo back credentials on some providers.
        error_code = "unknown_error"
        try:
            error_code = str(response.json().get("error", error_code))
        except ValueError:
            # A non-JSON error body carries nothing we can safely surface, so
            # the generic code above stands. Deliberately not logged: some
            # providers echo the submitted credential back in an HTML error.
            error_code = "unknown_error"
        raise OIDCFlowError(
            f"Token endpoint returned {response.status_code} ({error_code})"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OIDCFlowError("Token endpoint response is not valid JSON") from exc


async def exchange_code_for_tokens(
    *,
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    credentials: ClientCredentials,
) -> Dict[str, Any]:
    """Exchange an authorization code (plus PKCE verifier) for tokens."""
    return await _post_token_request(
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        credentials,
    )


async def refresh_access_token(
    *,
    token_endpoint: str,
    refresh_token: str,
    credentials: ClientCredentials,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Refresh the user's access token. Refresh tokens stay server-side."""
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if scope:
        data["scope"] = scope
    return await _post_token_request(token_endpoint, data, credentials)


def _get_jwks_client(jwks_uri: str) -> "jwt.PyJWKClient":
    client = _jwks_clients.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
        _jwks_clients[jwks_uri] = client
    return client


def clear_jwks_cache() -> None:
    """Drop cached JWKS clients (used by tests and on issuer reconfiguration)."""
    _jwks_clients.clear()


def _validate_id_token_sync(
    id_token: str,
    *,
    jwks_uri: str,
    issuer: str,
    audience: str,
    nonce: Optional[str],
) -> Dict[str, Any]:
    try:
        signing_key = _get_jwks_client(jwks_uri).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=_ID_TOKEN_ALGORITHMS,
            audience=audience,
            issuer=issuer,
            leeway=_ID_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCFlowError(f"ID token validation failed: {exc}") from exc
    except Exception as exc:
        raise OIDCFlowError(f"ID token validation failed: {exc}") from exc

    # The nonce binds this ID token to the authorization request this browser
    # started, so a token minted for another session cannot be injected here.
    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCFlowError("ID token nonce does not match the authorization request")

    return claims


async def validate_id_token(
    id_token: str,
    *,
    jwks_uri: str,
    issuer: str,
    audience: str,
    nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify an ID token's signature, standard claims, and nonce.

    Runs in a worker thread: PyJWT's JWKS client fetches key material with a
    blocking HTTP call, which would otherwise stall the event loop on a cache
    miss.
    """
    if not id_token:
        raise OIDCFlowError("Token response did not include an id_token")
    return await asyncio.to_thread(
        _validate_id_token_sync,
        id_token,
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=audience,
        nonce=nonce,
    )


def extract_user_identifier(claims: Dict[str, Any], username_claim: str = "email") -> Optional[str]:
    """Pick the Atlas identity out of validated ID token claims.

    Falls back to ``preferred_username`` and then ``sub`` so a provider that
    does not release ``email`` still yields a stable identity rather than
    failing login outright.
    """
    for key in (username_claim, "preferred_username", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
