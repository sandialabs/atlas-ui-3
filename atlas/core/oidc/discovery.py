"""OIDC provider metadata discovery with caching.

Atlas reads the IdP's ``/.well-known/openid-configuration`` document rather
than requiring every endpoint to be configured by hand. The document is cached
in-process with a TTL because it changes rarely and is needed on every login.

Security notes:

- The issuer must be an ``https://`` URL (an ``http://`` issuer is accepted
  only for loopback hosts, so local development against a mock IdP works).
- The ``issuer`` claim in the returned document must match the configured
  issuer exactly, per OpenID Connect Discovery section 4.3. Skipping that
  check would let a redirect on the discovery URL substitute a different
  provider's endpoints.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 10.0
DISCOVERY_CACHE_TTL_SECONDS = 3600.0

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})


class OIDCDiscoveryError(RuntimeError):
    """Raised when provider metadata cannot be fetched or fails validation."""


@dataclass(frozen=True)
class ProviderMetadata:
    """The subset of OIDC provider metadata Atlas relies on."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: Optional[str] = None
    end_session_endpoint: Optional[str] = None
    token_endpoint_auth_methods_supported: List[str] = field(default_factory=list)
    code_challenge_methods_supported: List[str] = field(default_factory=list)
    grant_types_supported: List[str] = field(default_factory=list)

    def supports_pkce_s256(self) -> bool:
        """Whether the provider advertises S256 PKCE.

        An empty list means the provider did not advertise the field at all;
        OAuth 2.1 and the MCP authorization spec both require S256, so we
        proceed rather than refusing login on a missing advertisement.
        """
        methods = self.code_challenge_methods_supported
        return not methods or "S256" in methods


def _validate_issuer_url(issuer: str) -> None:
    parsed = urlparse(issuer)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and (parsed.hostname or "") in _LOOPBACK_HOSTS:
        logger.warning("OIDC issuer uses http:// on a loopback host; this is for local development only")
        return
    raise OIDCDiscoveryError("OIDC issuer must be an https:// URL")


def discovery_url(issuer: str) -> str:
    """Build the discovery document URL for an issuer."""
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def parse_provider_metadata(issuer: str, document: Dict[str, Any]) -> ProviderMetadata:
    """Validate a discovery document and project it onto :class:`ProviderMetadata`."""
    if not isinstance(document, dict):
        raise OIDCDiscoveryError("OIDC discovery document is not a JSON object")

    advertised_issuer = document.get("issuer")
    if advertised_issuer != issuer.rstrip("/") and advertised_issuer != issuer:
        raise OIDCDiscoveryError(
            "OIDC discovery document issuer does not match the configured issuer"
        )

    required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    missing = [key for key in required if not document.get(key)]
    if missing:
        raise OIDCDiscoveryError(
            f"OIDC discovery document is missing required field(s): {', '.join(missing)}"
        )

    def _string_list(key: str) -> List[str]:
        value = document.get(key) or []
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    return ProviderMetadata(
        issuer=str(advertised_issuer),
        authorization_endpoint=str(document["authorization_endpoint"]),
        token_endpoint=str(document["token_endpoint"]),
        jwks_uri=str(document["jwks_uri"]),
        userinfo_endpoint=document.get("userinfo_endpoint"),
        end_session_endpoint=document.get("end_session_endpoint"),
        token_endpoint_auth_methods_supported=_string_list("token_endpoint_auth_methods_supported"),
        code_challenge_methods_supported=_string_list("code_challenge_methods_supported"),
        grant_types_supported=_string_list("grant_types_supported"),
    )


class _MetadataCache:
    """TTL cache for provider metadata, guarded so concurrent logins fetch once."""

    def __init__(self, ttl_seconds: float = DISCOVERY_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: Dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._entries.clear()

    async def get(self, issuer: str) -> ProviderMetadata:
        now = time.monotonic()
        cached = self._entries.get(issuer)
        if cached and cached[1] > now:
            return cached[0]

        async with self._lock:
            # Re-check: another coroutine may have populated the entry while
            # we waited for the lock.
            cached = self._entries.get(issuer)
            if cached and cached[1] > time.monotonic():
                return cached[0]

            metadata = await self._fetch(issuer)
            self._entries[issuer] = (metadata, time.monotonic() + self._ttl)
            return metadata

    async def _fetch(self, issuer: str) -> ProviderMetadata:
        _validate_issuer_url(issuer)
        url = discovery_url(issuer)
        try:
            async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                document = response.json()
        except httpx.HTTPError as exc:
            raise OIDCDiscoveryError(f"Failed to fetch OIDC discovery document: {exc}") from exc
        except ValueError as exc:
            raise OIDCDiscoveryError("OIDC discovery document is not valid JSON") from exc

        metadata = parse_provider_metadata(issuer, document)
        logger.info("Loaded OIDC provider metadata for the configured issuer")
        return metadata


_cache = _MetadataCache()


async def get_provider_metadata(issuer: str) -> ProviderMetadata:
    """Fetch (or return cached) provider metadata for ``issuer``."""
    if not issuer:
        raise OIDCDiscoveryError("OIDC issuer is not configured")
    return await _cache.get(issuer)


def clear_metadata_cache() -> None:
    """Drop cached provider metadata. Used by tests and by config reloads."""
    _cache.clear()
