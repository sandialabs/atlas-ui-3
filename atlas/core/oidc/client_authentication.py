"""Confidential-client authentication for Atlas's calls to the IdP.

Atlas is a server-side application, so it authenticates itself to the token
endpoint rather than behaving as a public client. Three methods are supported:

- ``client_secret_basic`` -- client ID/secret in the HTTP Basic header (default).
- ``client_secret_post`` -- client ID/secret in the form body.
- ``private_key_jwt`` -- a signed JWT client assertion (RFC 7523 section 2.2),
  which is preferred because no shared secret is transmitted or stored at the
  IdP, and the credential can be rotated as a key pair.

The private key never leaves this process: it is read from disk at first use,
cached, and only ever used to sign an assertion.
"""

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

import jwt

logger = logging.getLogger(__name__)

CLIENT_SECRET_BASIC = "client_secret_basic"
CLIENT_SECRET_POST = "client_secret_post"
PRIVATE_KEY_JWT = "private_key_jwt"

SUPPORTED_AUTH_METHODS = frozenset({CLIENT_SECRET_BASIC, CLIENT_SECRET_POST, PRIVATE_KEY_JWT})

_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

# Client assertions are single-use and presented immediately, so a short
# lifetime is both sufficient and the safer choice if one is ever captured.
_ASSERTION_LIFETIME_SECONDS = 300

_SUPPORTED_ASSERTION_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "PS256", "PS384", "PS512"})

_key_cache: Dict[str, str] = {}
_key_cache_lock = threading.Lock()


class ClientAuthenticationError(RuntimeError):
    """Raised when confidential-client credentials are missing or unusable."""


@dataclass(frozen=True)
class ClientCredentials:
    """How Atlas authenticates itself for one token-endpoint call.

    ``basic_auth`` maps to httpx's ``auth=`` argument; ``form_fields`` are
    merged into the POST body. Exactly one of the two carries the credential.
    """

    basic_auth: Optional[Tuple[str, str]] = None
    form_fields: Dict[str, str] = field(default_factory=dict)


def _load_private_key(path: str) -> str:
    cached = _key_cache.get(path)
    if cached is not None:
        return cached
    with _key_cache_lock:
        cached = _key_cache.get(path)
        if cached is not None:
            return cached
        key_path = Path(path).expanduser()
        try:
            material = key_path.read_text()
        except OSError as exc:
            raise ClientAuthenticationError(
                f"Cannot read OIDC_PRIVATE_KEY_PATH: {exc.strerror or exc}"
            ) from exc
        if "PRIVATE KEY" not in material:
            raise ClientAuthenticationError(
                "OIDC_PRIVATE_KEY_PATH does not contain a PEM private key"
            )
        _key_cache[path] = material
        return material


def clear_private_key_cache() -> None:
    """Forget any cached private key material (used by tests and key rotation)."""
    with _key_cache_lock:
        _key_cache.clear()


def build_client_assertion(
    *,
    client_id: str,
    token_endpoint: str,
    private_key_path: str,
    algorithm: str = "RS256",
    key_id: Optional[str] = None,
    now: Optional[float] = None,
) -> str:
    """Build a signed ``private_key_jwt`` client assertion.

    The audience is the token endpoint, which is what binds the assertion to
    one provider: an assertion captured by a different endpoint cannot be
    replayed against the real one.
    """
    if algorithm not in _SUPPORTED_ASSERTION_ALGORITHMS:
        raise ClientAuthenticationError(
            f"Unsupported OIDC_PRIVATE_KEY_ALGORITHM '{algorithm}'. "
            f"Supported: {', '.join(sorted(_SUPPORTED_ASSERTION_ALGORITHMS))}"
        )

    issued_at = int(now if now is not None else time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "jti": secrets.token_urlsafe(24),
        "iat": issued_at,
        "exp": issued_at + _ASSERTION_LIFETIME_SECONDS,
    }
    headers = {"kid": key_id} if key_id else None
    key_material = _load_private_key(private_key_path)
    try:
        return jwt.encode(claims, key_material, algorithm=algorithm, headers=headers)
    except Exception as exc:  # pragma: no cover - depends on key/alg mismatch
        raise ClientAuthenticationError(
            f"Failed to sign the OIDC client assertion: {exc}"
        ) from exc


def build_client_credentials(
    *,
    client_id: str,
    token_endpoint: str,
    auth_method: str = CLIENT_SECRET_BASIC,
    client_secret: Optional[str] = None,
    private_key_path: Optional[str] = None,
    private_key_algorithm: str = "RS256",
    private_key_id: Optional[str] = None,
) -> ClientCredentials:
    """Assemble the credential Atlas presents to ``token_endpoint``."""
    if not client_id:
        raise ClientAuthenticationError("OIDC_CLIENT_ID is not configured")

    method = (auth_method or CLIENT_SECRET_BASIC).strip()
    if method not in SUPPORTED_AUTH_METHODS:
        raise ClientAuthenticationError(
            f"Unsupported OIDC_CLIENT_AUTH_METHOD '{method}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_AUTH_METHODS))}"
        )

    if method == PRIVATE_KEY_JWT:
        if not private_key_path:
            raise ClientAuthenticationError(
                "OIDC_CLIENT_AUTH_METHOD=private_key_jwt requires OIDC_PRIVATE_KEY_PATH"
            )
        assertion = build_client_assertion(
            client_id=client_id,
            token_endpoint=token_endpoint,
            private_key_path=private_key_path,
            algorithm=private_key_algorithm,
            key_id=private_key_id,
        )
        return ClientCredentials(
            form_fields={
                "client_id": client_id,
                "client_assertion_type": _CLIENT_ASSERTION_TYPE,
                "client_assertion": assertion,
            }
        )

    if not client_secret:
        raise ClientAuthenticationError(
            f"OIDC_CLIENT_AUTH_METHOD={method} requires OIDC_CLIENT_SECRET"
        )

    if method == CLIENT_SECRET_POST:
        return ClientCredentials(
            form_fields={"client_id": client_id, "client_secret": client_secret}
        )

    return ClientCredentials(basic_auth=(client_id, client_secret))


def build_client_credentials_from_settings(
    settings, token_endpoint: str
) -> ClientCredentials:
    """Convenience wrapper reading the ``OIDC_*`` app settings."""
    return build_client_credentials(
        client_id=settings.oidc_client_id or "",
        token_endpoint=token_endpoint,
        auth_method=settings.oidc_client_auth_method,
        client_secret=settings.oidc_client_secret,
        private_key_path=settings.oidc_private_key_path,
        private_key_algorithm=settings.oidc_private_key_algorithm,
        private_key_id=settings.oidc_private_key_id,
    )
