"""Pluggable delegated downstream authorization.

Atlas never forwards a user's inbound access token to a downstream service.
Instead it exchanges that token for a short-lived, audience-specific,
minimally-scoped token, using one of two interchangeable mechanisms:

- :class:`TokenExchangeProvider` -- RFC 8693 OAuth 2.0 Token Exchange, the
  standards-based path.
- :class:`EntraOboProvider` -- Microsoft Entra ID On-Behalf-Of, which provides
  the equivalent delegated user-to-service behaviour using Microsoft's
  supported flow.

Both are registered by name so a deployment picks one by configuration, and a
third mechanism can be added without touching call sites. The subject token
and Atlas's own client credentials stay inside this module's caller; only the
derived, audience-bound token is handed to the downstream client.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

from atlas.core.oidc.client_authentication import ClientCredentials

logger = logging.getLogger(__name__)

DELEGATION_TIMEOUT_SECONDS = 20.0

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


class DelegationError(RuntimeError):
    """Raised when a delegated token cannot be obtained."""


@dataclass(frozen=True)
class DelegationRequest:
    """What the caller wants: a token for one audience, on one user's behalf."""

    user_id: str
    subject_token: str
    audience: Optional[str] = None
    resource: Optional[str] = None
    scope: Optional[str] = None
    # Free-form provenance for audit trails (e.g. the agent run that asked).
    actor: Optional[str] = None

    def cache_key(self) -> Tuple[str, str, str, str]:
        return (
            self.user_id.lower(),
            self.audience or "",
            self.resource or "",
            self.scope or "",
        )


@dataclass(frozen=True)
class DelegatedToken:
    """A short-lived downstream credential."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: Optional[float] = None
    scope: str = ""
    audience: Optional[str] = None
    issued_token_type: Optional[str] = None

    def is_expired(self, min_ttl_seconds: float = 60.0, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            # No expiry advertised: treat as single-use rather than caching it
            # past this call, since we cannot know when it stops working.
            return True
        return (now if now is not None else time.time()) >= (self.expires_at - min_ttl_seconds)


def _parse_token_response(
    payload: Dict[str, Any], request: DelegationRequest, now: Optional[float] = None
) -> DelegatedToken:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise DelegationError("Delegation response did not include an access_token")

    expires_in = payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = (now if now is not None else time.time()) + float(expires_in)

    return DelegatedToken(
        access_token=access_token,
        token_type=str(payload.get("token_type") or "Bearer"),
        expires_at=expires_at,
        scope=str(payload.get("scope") or request.scope or ""),
        audience=request.audience or request.resource,
        issued_token_type=payload.get("issued_token_type"),
    )


async def _post_delegation_request(
    token_endpoint: str, data: Dict[str, str], credentials: ClientCredentials
) -> Dict[str, Any]:
    payload = {**data, **credentials.form_fields}
    async with httpx.AsyncClient(timeout=DELEGATION_TIMEOUT_SECONDS) as client:
        response = await client.post(
            token_endpoint,
            data=payload,
            auth=credentials.basic_auth,
            headers={"Accept": "application/json"},
        )
    if response.status_code >= 400:
        error_code = "unknown_error"
        try:
            error_code = str(response.json().get("error", error_code))
        except ValueError:
            # Non-JSON error body: keep the generic code rather than surfacing
            # provider HTML, which can echo the submitted assertion back.
            error_code = "unknown_error"
        raise DelegationError(
            f"Delegation endpoint returned {response.status_code} ({error_code})"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise DelegationError("Delegation response is not valid JSON") from exc


class DelegationProvider:
    """Base class for delegation mechanisms.

    Credentials are supplied as a factory rather than a value: a
    ``private_key_jwt`` client assertion is single-use and expires minutes
    after it is signed, so a provider built once at startup must mint a fresh
    one for every exchange.
    """

    name = "base"

    def __init__(
        self,
        token_endpoint: str,
        credentials_factory: Callable[[], ClientCredentials],
    ) -> None:
        self.token_endpoint = token_endpoint
        self._credentials_factory = credentials_factory

    def build_credentials(self) -> ClientCredentials:
        return self._credentials_factory()

    async def exchange(self, request: DelegationRequest) -> DelegatedToken:
        raise NotImplementedError


class TokenExchangeProvider(DelegationProvider):
    """RFC 8693 OAuth 2.0 Token Exchange."""

    name = "token_exchange"

    async def exchange(self, request: DelegationRequest) -> DelegatedToken:
        if not request.audience and not request.resource:
            raise DelegationError(
                "Token exchange requires an audience or resource for the downstream service"
            )
        data = {
            "grant_type": TOKEN_EXCHANGE_GRANT_TYPE,
            "subject_token": request.subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
        }
        if request.audience:
            data["audience"] = request.audience
        if request.resource:
            data["resource"] = request.resource
        if request.scope:
            data["scope"] = request.scope
        payload = await _post_delegation_request(
            self.token_endpoint, data, self.build_credentials()
        )
        return _parse_token_response(payload, request)


class EntraOboProvider(DelegationProvider):
    """Microsoft Entra ID On-Behalf-Of flow.

    Entra predates and diverges from RFC 8693: it uses the JWT bearer grant
    with ``requested_token_use=on_behalf_of``, and expresses the target
    service through the scope rather than an ``audience`` parameter. The
    audience configured for a resource is therefore folded into the scope as
    ``<audience>/.default`` when no explicit scope is given.
    """

    name = "entra_obo"

    async def exchange(self, request: DelegationRequest) -> DelegatedToken:
        scope = request.scope
        if not scope:
            target = request.audience or request.resource
            if not target:
                raise DelegationError(
                    "Entra OBO requires a scope, audience, or resource for the downstream service"
                )
            scope = f"{target.rstrip('/')}/.default"
        data = {
            "grant_type": JWT_BEARER_GRANT_TYPE,
            "assertion": request.subject_token,
            "scope": scope,
            "requested_token_use": "on_behalf_of",
        }
        payload = await _post_delegation_request(
            self.token_endpoint, data, self.build_credentials()
        )
        return _parse_token_response(payload, request)


PROVIDER_REGISTRY: Dict[str, type] = {
    TokenExchangeProvider.name: TokenExchangeProvider,
    EntraOboProvider.name: EntraOboProvider,
}


def build_provider(
    name: str,
    token_endpoint: str,
    credentials_factory: Callable[[], ClientCredentials],
) -> DelegationProvider:
    """Instantiate a registered delegation provider by name."""
    provider_class = PROVIDER_REGISTRY.get((name or "").strip())
    if provider_class is None:
        raise DelegationError(
            f"Unknown delegation provider '{name}'. "
            f"Available: {', '.join(sorted(PROVIDER_REGISTRY))}"
        )
    return provider_class(token_endpoint, credentials_factory)


class DelegationManager:
    """Caches delegated tokens per (user, audience, scope) and serialises misses."""

    def __init__(self, provider: DelegationProvider, min_ttl_seconds: float = 60.0) -> None:
        self.provider = provider
        self.min_ttl_seconds = min_ttl_seconds
        self._cache: Dict[Tuple[str, str, str, str], DelegatedToken] = {}
        self._lock = asyncio.Lock()

    async def get_token(self, request: DelegationRequest) -> DelegatedToken:
        """Return a live delegated token, minting one if needed."""
        key = request.cache_key()
        cached = self._cache.get(key)
        if cached and not cached.is_expired(self.min_ttl_seconds):
            return cached

        async with self._lock:
            cached = self._cache.get(key)
            if cached and not cached.is_expired(self.min_ttl_seconds):
                return cached
            token = await self.provider.exchange(request)
            # A token with no advertised expiry is never cached: we cannot tell
            # when it stops being valid, and a stale one fails closed as a 401
            # on the downstream call rather than being re-minted.
            if token.expires_at is not None:
                self._cache[key] = token
            logger.info(
                "Minted delegated token via '%s' for a downstream audience (actor=%s)",
                self.provider.name,
                request.actor or "user",
            )
            return token

    def invalidate_user(self, user_id: str) -> int:
        """Drop every cached delegated token for one user (e.g. at logout)."""
        prefix = user_id.lower()
        keys = [key for key in self._cache if key[0] == prefix]
        for key in keys:
            del self._cache[key]
        return len(keys)

    def clear(self) -> None:
        self._cache.clear()


# The built manager alongside the configuration signature it was built from, so
# a settings change (provider, endpoint, or client auth method) rebuilds it
# instead of silently reusing a manager wired to the old configuration.
_manager_cache: Dict[str, Any] = {"manager": None, "signature": None}


def get_delegation_manager(settings=None) -> Optional[DelegationManager]:
    """Build (or return) the configured delegation manager.

    Returns ``None`` when delegation is disabled or not fully configured, so
    callers can degrade to their existing behaviour instead of failing.
    """
    if settings is None:
        from atlas.infrastructure.app_factory import app_factory

        settings = app_factory.get_config_manager().app_settings

    if not getattr(settings, "feature_oidc_delegation_enabled", False):
        return None

    token_endpoint = getattr(settings, "oidc_delegation_token_endpoint", None)
    if not token_endpoint:
        logger.debug("Delegation enabled but no token endpoint resolved yet")
        return None

    provider_name = getattr(settings, "oidc_delegation_provider", "token_exchange")
    signature = (provider_name, token_endpoint, settings.oidc_client_auth_method or "")
    cached_manager = _manager_cache["manager"]
    if cached_manager is not None and _manager_cache["signature"] == signature:
        return cached_manager

    from atlas.core.oidc.client_authentication import build_client_credentials_from_settings

    def credentials_factory() -> ClientCredentials:
        return build_client_credentials_from_settings(settings, token_endpoint)

    try:
        # Build once eagerly so a misconfiguration surfaces here rather than on
        # the first tool call, then discard the result: the real credential is
        # minted per exchange.
        credentials_factory()
        provider = build_provider(provider_name, token_endpoint, credentials_factory)
    except Exception as exc:
        logger.error("Cannot build delegation provider: %s", exc)
        return None

    manager = DelegationManager(
        provider,
        min_ttl_seconds=float(getattr(settings, "oidc_delegation_min_ttl_seconds", 60)),
    )
    _manager_cache["manager"] = manager
    _manager_cache["signature"] = signature
    return manager


async def get_delegation_manager_async(settings=None) -> Optional[DelegationManager]:
    """Async form that discovers the token endpoint when one is not configured.

    Most deployments delegate against the same authorization server they log
    users in with, so requiring ``OIDC_DELEGATION_TOKEN_ENDPOINT`` to be typed
    out would be redundant configuration that can silently drift from the
    issuer.
    """
    if settings is None:
        from atlas.infrastructure.app_factory import app_factory

        settings = app_factory.get_config_manager().app_settings

    if not getattr(settings, "feature_oidc_delegation_enabled", False):
        return None

    if not getattr(settings, "oidc_delegation_token_endpoint", None):
        issuer = getattr(settings, "oidc_issuer", None)
        if not issuer:
            logger.error(
                "Delegation is enabled but neither OIDC_DELEGATION_TOKEN_ENDPOINT "
                "nor OIDC_ISSUER is configured"
            )
            return None
        from atlas.core.oidc.discovery import OIDCDiscoveryError, get_provider_metadata

        try:
            metadata = await get_provider_metadata(issuer)
        except OIDCDiscoveryError as exc:
            logger.error("Cannot discover the delegation token endpoint: %s", exc)
            return None
        settings.oidc_delegation_token_endpoint = metadata.token_endpoint

    return get_delegation_manager(settings)


def reset_delegation_manager() -> None:
    """Forget the cached manager (used by tests and on reconfiguration)."""
    _manager_cache["manager"] = None
    _manager_cache["signature"] = None
