"""OAuth 2.1 authorization for remote MCP servers.

Atlas acts as the OAuth client on behalf of each user. Given only the MCP
server's URL, this module walks the discovery chain the MCP authorization
spec mandates and drives an Authorization Code + PKCE flow:

1. RFC 9728 -- an unauthenticated request to the MCP endpoint answers ``401``
   with ``WWW-Authenticate: Bearer resource_metadata="..."``. That document
   names the authorization server(s) protecting the resource.
2. RFC 8414 / OpenID Discovery -- the authorization server's metadata supplies
   the authorization, token, registration and revocation endpoints.
3. RFC 7591 -- when the server advertises a ``registration_endpoint`` and
   Atlas holds no credentials for it yet, Atlas registers itself dynamically.
4. RFC 7636 -- the code is redeemed with a PKCE ``S256`` verifier.

Why Atlas drives this rather than ``fastmcp.client.auth.OAuth``: that helper
binds a callback listener on ``127.0.0.1`` and opens a local browser, which
assumes a single-user desktop client. Atlas is a multi-user server whose users
browse from other machines, so the redirect target has to be an Atlas route.
The resulting access token is handed to FastMCP as a bearer credential, and
this module owns refresh and revocation.

Security notes:

- Every endpoint used in the flow is required to be ``https://`` (loopback
  ``http://`` is tolerated so local development against a mock provider works).
  Discovery documents are attacker-influenced input as far as Atlas is
  concerned: a compromised MCP server could name any authorization server, so
  the scheme check is applied to discovered URLs, not just configured ones.
- The authorization server named by a protected-resource document is only
  accepted if the document itself validates, and the AS metadata's ``issuer``
  must match the issuer we fetched it from (RFC 8414 section 3.3). Skipping
  that lets a redirect substitute another provider's endpoints.
- No token, authorization code, or client secret is ever logged.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 10.0
TOKEN_TIMEOUT_SECONDS = 20.0
REGISTRATION_TIMEOUT_SECONDS = 20.0
DISCOVERY_CACHE_TTL_SECONDS = 3600.0

# Mirrors atlas.core.oidc.discovery: an http:// endpoint is only tolerated on a
# loopback host so local development against a mock provider works.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "testserver"})

# Default scope requested when neither the server config nor the
# protected-resource metadata says anything. Empty means "whatever the
# authorization server grants by default", which is the correct behaviour --
# inventing scope names would just get the authorization request rejected.
_DEFAULT_SCOPES: List[str] = []


class MCPOAuthError(RuntimeError):
    """Raised when any step of the MCP OAuth flow fails."""


# --- URL validation -------------------------------------------------------


def _is_loopback(host: str) -> bool:
    host = (host or "").lower().strip("[]")
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def origin_of(url: str) -> str:
    """The scheme://host:port of a URL, for same-origin comparisons."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def is_loopback_url(url: str) -> bool:
    """Whether a URL points at a loopback host."""
    return _is_loopback(urlsplit(url).hostname or "")


def validate_endpoint_url(url: str, *, what: str, allow_loopback: bool = True) -> str:
    """Require an https:// URL (or http:// on loopback) and return it.

    Applied to every URL Atlas fetches or redirects to during the flow,
    including ones read out of discovery documents.

    ``allow_loopback`` must be False for any URL that came out of a remote
    server's discovery document or challenge header. Otherwise a hostile
    remote MCP server could name ``http://127.0.0.1:...`` and have Atlas make
    the request on its behalf, from inside the deployment's network. Loopback
    stays permitted when the MCP server itself is on loopback, which is the
    local-development case the allowance exists for.
    """
    if not url or not isinstance(url, str):
        raise MCPOAuthError(f"{what} is missing")
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and _is_loopback(parsed.hostname or ""):
        if not allow_loopback:
            raise MCPOAuthError(
                f"{what} points at a loopback address, which a remote server "
                "is not allowed to name"
            )
        logger.warning(
            "MCP OAuth %s uses http:// on a loopback host; local development only", what
        )
        return url
    raise MCPOAuthError(f"{what} must be an https:// URL")


# --- Metadata models ------------------------------------------------------


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    """RFC 9728 protected-resource metadata for an MCP endpoint."""

    resource: str
    authorization_servers: List[str] = field(default_factory=list)
    scopes_supported: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """The subset of RFC 8414 authorization-server metadata Atlas uses."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str] = None
    revocation_endpoint: Optional[str] = None
    scopes_supported: List[str] = field(default_factory=list)
    grant_types_supported: List[str] = field(default_factory=list)
    response_types_supported: List[str] = field(default_factory=list)
    code_challenge_methods_supported: List[str] = field(default_factory=list)
    token_endpoint_auth_methods_supported: List[str] = field(default_factory=list)

    def supports_pkce_s256(self) -> bool:
        """Whether S256 PKCE is usable.

        An empty list means the server advertised nothing; OAuth 2.1 and the
        MCP authorization spec both require S256, so Atlas proceeds rather
        than refusing on a missing advertisement.
        """
        methods = self.code_challenge_methods_supported
        return not methods or "S256" in methods

    def supports_refresh(self) -> bool:
        grants = self.grant_types_supported
        return not grants or "refresh_token" in grants


def _string_list(document: Dict[str, Any], key: str) -> List[str]:
    value = document.get(key) or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


# --- Step 1: the 401 challenge -------------------------------------------


# ``WWW-Authenticate: Bearer resource_metadata="https://...", error="..."``.
# Matched case-insensitively on the parameter name; the value may be quoted or
# bare per RFC 7235's auth-param grammar.
_RESOURCE_METADATA_PARAM = re.compile(
    r'resource_metadata\s*=\s*(?:"([^"]*)"|([^\s,]+))', re.IGNORECASE
)


def parse_resource_metadata_challenge(header_value: Optional[str]) -> Optional[str]:
    """Extract the ``resource_metadata`` URL from a ``WWW-Authenticate`` header.

    Returns None when the header is absent or carries no such parameter, which
    is not an error: Atlas then falls back to the well-known location.
    """
    if not header_value:
        return None
    match = _RESOURCE_METADATA_PARAM.search(header_value)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


async def probe_resource_metadata_url(mcp_url: str) -> Optional[str]:
    """Ask the MCP endpoint where its protected-resource metadata lives.

    Sends an unauthenticated MCP ``initialize`` and reads the RFC 9728
    challenge off the ``401``. A server that answers anything else simply
    yields None and the caller falls back to the well-known path.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "Atlas", "version": "1"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                mcp_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
    except httpx.HTTPError as exc:
        logger.debug("MCP OAuth challenge probe failed: %s", exc)
        return None

    if response.status_code not in (401, 403):
        return None
    return parse_resource_metadata_challenge(response.headers.get("www-authenticate"))


def default_resource_metadata_urls(mcp_url: str) -> List[str]:
    """Well-known protected-resource metadata locations for an MCP URL.

    RFC 9728 inserts the well-known segment between host and path, so a
    resource at ``/mcp`` publishes at ``/.well-known/...-resource/mcp``. The
    root form is tried as well because it is what servers mounted at the
    origin actually use.
    """
    parsed = urlsplit(mcp_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = []
    if path:
        candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    candidates.append(f"{origin}/.well-known/oauth-protected-resource")
    return candidates


def parse_protected_resource_metadata(document: Any) -> ProtectedResourceMetadata:
    if not isinstance(document, dict):
        raise MCPOAuthError("Protected resource metadata is not a JSON object")
    servers = _string_list(document, "authorization_servers")
    if not servers:
        raise MCPOAuthError(
            "Protected resource metadata names no authorization_servers"
        )
    return ProtectedResourceMetadata(
        resource=str(document.get("resource") or ""),
        authorization_servers=servers,
        scopes_supported=_string_list(document, "scopes_supported"),
    )


# Redirects are followed by hand (see _fetch_json) so every hop can be checked
# against the transport rule; this bounds that loop.
MAX_DISCOVERY_REDIRECTS = 5


async def _fetch_json(
    url: str,
    *,
    what: str,
    allow_loopback: bool = True,
    require_origin: Optional[str] = None,
) -> Dict[str, Any]:
    """GET a discovery document, validating the transport of every hop.

    Redirects are followed manually rather than by httpx. With
    ``follow_redirects=True`` only the first URL would be checked, so a
    compromised server could answer the initial https request with a redirect
    to plaintext or to an internal address and defeat the very restriction
    :func:`validate_endpoint_url` exists to impose.

    ``require_origin`` pins every hop to one origin. RFC 9728 publishes
    protected-resource metadata on the resource's own origin, so a challenge
    (or a redirect) naming somewhere else is not a document Atlas has any
    reason to fetch -- and following it would turn the MCP server into a
    request forgery primitive against the deployment's network.
    """
    def _check(candidate: str, label: str) -> None:
        validate_endpoint_url(candidate, what=label, allow_loopback=allow_loopback)
        if require_origin and origin_of(candidate) != require_origin:
            raise MCPOAuthError(
                f"{label} is outside the expected origin {require_origin}"
            )

    _check(url, what)
    current = url
    try:
        async with httpx.AsyncClient(
            timeout=DISCOVERY_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            for _ in range(MAX_DISCOVERY_REDIRECTS + 1):
                response = await client.get(
                    current, headers={"Accept": "application/json"}
                )
                if response.is_redirect:
                    location = response.headers.get("location") or ""
                    if not location:
                        raise MCPOAuthError(f"{what} redirect carried no Location")
                    # Resolve relative redirects against the current URL, then
                    # hold the destination to the same rules as the first hop.
                    current = str(httpx.URL(current).join(location))
                    _check(current, f"{what} redirect target")
                    continue
                response.raise_for_status()
                return response.json()
        raise MCPOAuthError(f"{what} exceeded the redirect limit")
    except httpx.HTTPError as exc:
        raise MCPOAuthError(f"Failed to fetch {what}: {exc}") from exc
    except ValueError as exc:
        raise MCPOAuthError(f"{what} is not valid JSON") from exc


async def discover_protected_resource(mcp_url: str) -> Optional[ProtectedResourceMetadata]:
    """Resolve RFC 9728 metadata for an MCP endpoint, or None if unpublished.

    The metadata URL is pinned to the MCP endpoint's own origin. RFC 9728
    publishes it there, so a challenge naming another host is not a document
    Atlas has any reason to fetch; honouring it would let a hostile MCP server
    aim server-side GETs at arbitrary hosts, including internal ones.
    """
    # A loopback MCP server is the local-development case, and only there may
    # discovery resolve to loopback addresses.
    allow_loopback = is_loopback_url(mcp_url)
    validate_endpoint_url(mcp_url, what="MCP server URL")
    expected_origin = origin_of(mcp_url)

    candidates: List[str] = []
    challenge_url = await probe_resource_metadata_url(mcp_url)
    if challenge_url:
        if origin_of(challenge_url) == expected_origin:
            candidates.append(challenge_url)
        else:
            logger.warning(
                "Ignoring MCP resource_metadata challenge pointing outside the "
                "server's own origin"
            )
    candidates.extend(
        url for url in default_resource_metadata_urls(mcp_url) if url not in candidates
    )

    for url in candidates:
        try:
            document = await _fetch_json(
                url,
                what="protected resource metadata",
                allow_loopback=allow_loopback,
                require_origin=expected_origin,
            )
            return parse_protected_resource_metadata(document)
        except MCPOAuthError as exc:
            logger.debug("Protected resource metadata not usable at a candidate URL: %s", exc)
            continue
    return None


# --- Step 2: authorization server metadata --------------------------------


def authorization_server_metadata_urls(issuer: str) -> List[str]:
    """Candidate metadata URLs for an issuer, in RFC 8414 precedence order."""
    parsed = urlsplit(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
    ]
    if path:
        # Some providers publish under the issuer path instead of the
        # path-insertion form; try both rather than failing discovery.
        candidates.append(urljoin(f"{issuer.rstrip('/')}/", ".well-known/oauth-authorization-server"))
        candidates.append(urljoin(f"{issuer.rstrip('/')}/", ".well-known/openid-configuration"))
    return candidates


def parse_authorization_server_metadata(
    issuer: str, document: Any, *, allow_loopback: bool = True
) -> AuthorizationServerMetadata:
    if not isinstance(document, dict):
        raise MCPOAuthError("Authorization server metadata is not a JSON object")

    advertised = document.get("issuer")
    # Normalize both sides: a provider may publish a trailing slash where the
    # protected-resource document named it without one (or the reverse), and
    # comparing raw strings would make such a provider undiscoverable.
    if not isinstance(advertised, str) or advertised.rstrip("/") != issuer.rstrip("/"):
        # RFC 8414 section 3.3: the issuer in the document must match the one
        # the document was fetched for, or a redirect could substitute another
        # provider's endpoints for the one the resource actually trusts.
        raise MCPOAuthError(
            "Authorization server metadata issuer does not match the requested issuer"
        )

    required = ("authorization_endpoint", "token_endpoint")
    missing = [key for key in required if not document.get(key)]
    if missing:
        raise MCPOAuthError(
            f"Authorization server metadata is missing: {', '.join(missing)}"
        )

    metadata = AuthorizationServerMetadata(
        issuer=str(advertised),
        authorization_endpoint=str(document["authorization_endpoint"]),
        token_endpoint=str(document["token_endpoint"]),
        registration_endpoint=document.get("registration_endpoint") or None,
        revocation_endpoint=document.get("revocation_endpoint") or None,
        scopes_supported=_string_list(document, "scopes_supported"),
        grant_types_supported=_string_list(document, "grant_types_supported"),
        response_types_supported=_string_list(document, "response_types_supported"),
        code_challenge_methods_supported=_string_list(
            document, "code_challenge_methods_supported"
        ),
        token_endpoint_auth_methods_supported=_string_list(
            document, "token_endpoint_auth_methods_supported"
        ),
    )

    # Discovered endpoints are attacker-influenced; hold them to the same
    # transport requirement as configured ones.
    validate_endpoint_url(
        metadata.authorization_endpoint,
        what="authorization endpoint",
        allow_loopback=allow_loopback,
    )
    validate_endpoint_url(
        metadata.token_endpoint, what="token endpoint", allow_loopback=allow_loopback
    )
    if metadata.registration_endpoint:
        validate_endpoint_url(
            metadata.registration_endpoint,
            what="registration endpoint",
            allow_loopback=allow_loopback,
        )
    if metadata.revocation_endpoint:
        validate_endpoint_url(
            metadata.revocation_endpoint,
            what="revocation endpoint",
            allow_loopback=allow_loopback,
        )
    return metadata


async def discover_authorization_server(
    issuer: str, *, allow_loopback: bool = True
) -> AuthorizationServerMetadata:
    """Fetch and validate authorization-server metadata for an issuer.

    The authorization server legitimately lives on a different origin from the
    resource, so this is not origin-pinned -- but a remote resource still may
    not name a loopback issuer.
    """
    validate_endpoint_url(
        issuer, what="authorization server issuer", allow_loopback=allow_loopback
    )
    last_error: Optional[MCPOAuthError] = None
    for url in authorization_server_metadata_urls(issuer):
        try:
            document = await _fetch_json(
                url,
                what="authorization server metadata",
                allow_loopback=allow_loopback,
            )
            return parse_authorization_server_metadata(
                issuer, document, allow_loopback=allow_loopback
            )
        except MCPOAuthError as exc:
            last_error = exc
            continue
    raise MCPOAuthError(
        f"Could not discover authorization server metadata for the issuer: {last_error}"
    )


# --- The full chain, cached ----------------------------------------------


@dataclass(frozen=True)
class ServerOAuthMetadata:
    """Everything the flow needs about one MCP server's authorization."""

    mcp_url: str
    authorization_server: AuthorizationServerMetadata
    protected_resource: Optional[ProtectedResourceMetadata] = None

    @property
    def resource(self) -> Optional[str]:
        """The RFC 8707 ``resource`` indicator, when the server publishes one."""
        if self.protected_resource and self.protected_resource.resource:
            return self.protected_resource.resource
        return None

    def default_scopes(self) -> List[str]:
        if self.protected_resource and self.protected_resource.scopes_supported:
            return list(self.protected_resource.scopes_supported)
        if self.authorization_server.scopes_supported:
            return list(self.authorization_server.scopes_supported)
        return list(_DEFAULT_SCOPES)


# A provider that is down would otherwise re-pay the full sequence of HTTP
# timeouts on every single tool call. Failures are cached too, but briefly, so
# a recovered provider is picked up again quickly.
DISCOVERY_FAILURE_CACHE_TTL_SECONDS = 60.0


class _MetadataCache:
    """TTL cache keyed by MCP URL, guarded so concurrent starts fetch once.

    Locks are per URL rather than global: one unreachable provider holding a
    single shared lock for the length of its timeouts would stall discovery
    for every healthy server behind it.
    """

    def __init__(
        self,
        ttl_seconds: float = DISCOVERY_CACHE_TTL_SECONDS,
        failure_ttl_seconds: float = DISCOVERY_FAILURE_CACHE_TTL_SECONDS,
    ) -> None:
        self._ttl = ttl_seconds
        self._failure_ttl = failure_ttl_seconds
        self._entries: Dict[str, Tuple[ServerOAuthMetadata, float]] = {}
        self._failures: Dict[str, Tuple[str, float]] = {}
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def clear(self) -> None:
        self._entries.clear()
        self._failures.clear()

    def _cached(self, mcp_url: str) -> Optional[ServerOAuthMetadata]:
        entry = self._entries.get(mcp_url)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        return None

    def _cached_failure(self, mcp_url: str) -> Optional[str]:
        entry = self._failures.get(mcp_url)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        return None

    async def get(self, mcp_url: str) -> ServerOAuthMetadata:
        cached = self._cached(mcp_url)
        if cached is not None:
            return cached
        failure = self._cached_failure(mcp_url)
        if failure is not None:
            raise MCPOAuthError(failure)

        async with self._locks[mcp_url]:
            # Another coroutine may have resolved (or failed) while we waited.
            cached = self._cached(mcp_url)
            if cached is not None:
                return cached
            failure = self._cached_failure(mcp_url)
            if failure is not None:
                raise MCPOAuthError(failure)

            try:
                metadata = await self._discover(mcp_url)
            except MCPOAuthError as exc:
                self._failures[mcp_url] = (
                    str(exc), time.monotonic() + self._failure_ttl
                )
                raise
            self._failures.pop(mcp_url, None)
            self._entries[mcp_url] = (metadata, time.monotonic() + self._ttl)
            return metadata

    async def _discover(self, mcp_url: str) -> ServerOAuthMetadata:
        allow_loopback = is_loopback_url(mcp_url)
        protected_resource = await discover_protected_resource(mcp_url)

        issuers: List[str] = []
        if protected_resource:
            issuers.extend(protected_resource.authorization_servers)
        # A server that publishes no RFC 9728 document may still be its own
        # authorization server, which is the pre-9728 shape of the MCP spec.
        parsed = urlsplit(mcp_url)
        fallback_issuer = f"{parsed.scheme}://{parsed.netloc}"
        if fallback_issuer not in issuers:
            issuers.append(fallback_issuer)

        last_error: Optional[MCPOAuthError] = None
        for issuer in issuers:
            try:
                authorization_server = await discover_authorization_server(
                    issuer, allow_loopback=allow_loopback
                )
            except MCPOAuthError as exc:
                last_error = exc
                continue
            return ServerOAuthMetadata(
                mcp_url=mcp_url,
                authorization_server=authorization_server,
                protected_resource=protected_resource,
            )
        raise MCPOAuthError(
            f"No usable authorization server found for the MCP server: {last_error}"
        )


_metadata_cache = _MetadataCache()


async def get_server_oauth_metadata(mcp_url: str) -> ServerOAuthMetadata:
    """Discover (or return cached) OAuth metadata for an MCP server URL."""
    return await _metadata_cache.get(mcp_url)


def clear_metadata_cache() -> None:
    """Drop cached discovery results. Used by tests and by config reloads."""
    _metadata_cache.clear()


# --- Step 3: dynamic client registration (RFC 7591) -----------------------


@dataclass
class RegisteredClient:
    """Client credentials Atlas holds for one authorization server."""

    client_id: str
    issuer: str
    redirect_uri: str
    client_secret: Optional[str] = None
    registered_at: float = 0.0
    client_secret_expires_at: Optional[float] = None
    scopes: Optional[str] = None

    def is_expired(self) -> bool:
        """Whether an issued client secret has passed its expiry.

        ``client_secret_expires_at`` of 0 means "never expires" per RFC 7591.
        """
        if not self.client_secret_expires_at:
            return False
        return time.time() >= self.client_secret_expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_id": self.client_id,
            "issuer": self.issuer,
            "redirect_uri": self.redirect_uri,
            "client_secret": self.client_secret,
            "registered_at": self.registered_at,
            "client_secret_expires_at": self.client_secret_expires_at,
            "scopes": self.scopes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegisteredClient":
        return cls(
            client_id=str(data.get("client_id") or ""),
            issuer=str(data.get("issuer") or ""),
            redirect_uri=str(data.get("redirect_uri") or ""),
            client_secret=data.get("client_secret"),
            registered_at=float(data.get("registered_at") or 0.0),
            client_secret_expires_at=data.get("client_secret_expires_at"),
            scopes=data.get("scopes"),
        )


async def register_client(
    metadata: AuthorizationServerMetadata,
    *,
    redirect_uri: str,
    client_name: str,
    scopes: Optional[str] = None,
) -> RegisteredClient:
    """Register Atlas with an authorization server via RFC 7591.

    Atlas registers as a public client using PKCE: it runs one shared
    registration per MCP server rather than per user, and a secret held for
    many users buys nothing that PKCE does not already provide. A server that
    insists on issuing a secret anyway gets one stored, encrypted.
    """
    if not metadata.registration_endpoint:
        raise MCPOAuthError(
            "Authorization server does not support dynamic client registration "
            "and no client_id is configured for this server"
        )

    body: Dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "web",
    }
    if scopes:
        body["scope"] = scopes

    try:
        async with httpx.AsyncClient(timeout=REGISTRATION_TIMEOUT_SECONDS) as client:
            response = await client.post(
                metadata.registration_endpoint,
                json=body,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise MCPOAuthError(f"Dynamic client registration request failed: {exc}") from exc

    if response.status_code >= 400:
        raise MCPOAuthError(
            f"Dynamic client registration returned {response.status_code} "
            f"({_error_code(response)})"
        )

    try:
        document = response.json()
    except ValueError as exc:
        raise MCPOAuthError("Dynamic client registration response is not JSON") from exc

    client_id = document.get("client_id")
    if not client_id:
        raise MCPOAuthError("Dynamic client registration returned no client_id")

    expires_at = document.get("client_secret_expires_at")
    logger.info(
        "Registered Atlas as an OAuth client with authorization server '%s'",
        sanitize_for_logging(metadata.issuer),
    )
    return RegisteredClient(
        client_id=str(client_id),
        issuer=metadata.issuer,
        redirect_uri=redirect_uri,
        client_secret=document.get("client_secret"),
        registered_at=time.time(),
        client_secret_expires_at=float(expires_at) if expires_at else None,
        scopes=scopes,
    )


# --- Step 4: token endpoint calls ----------------------------------------


def _error_code(response: httpx.Response) -> str:
    """Best-effort OAuth error code from a failed response.

    Only the ``error`` member is surfaced. The raw body is never logged:
    some providers echo submitted credentials back inside an HTML error page.
    """
    try:
        document = response.json()
    except ValueError:
        return "unknown_error"
    if not isinstance(document, dict):
        return "unknown_error"
    return str(document.get("error") or "unknown_error")


@dataclass
class TokenResponse:
    """A normalized token endpoint response."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    scopes: Optional[str] = None


def _parse_token_response(document: Any) -> TokenResponse:
    if not isinstance(document, dict):
        raise MCPOAuthError("Token endpoint response is not a JSON object")
    access = document.get("access_token")
    if not access or not isinstance(access, str):
        raise MCPOAuthError("Token endpoint response carried no access_token")

    expires_in = document.get("expires_in")
    expires_at = (
        time.time() + float(expires_in)
        if isinstance(expires_in, (int, float)) and expires_in > 0
        else None
    )
    refresh = document.get("refresh_token")
    scope = document.get("scope")
    return TokenResponse(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=expires_at,
        scopes=scope if isinstance(scope, str) and scope else None,
    )


async def _post_token_endpoint(
    token_endpoint: str, data: Dict[str, str], client: RegisteredClient
) -> TokenResponse:
    payload = dict(data)
    payload["client_id"] = client.client_id
    # Public clients send no secret. When the server insisted on issuing one,
    # Atlas is a confidential client and authenticates with it.
    auth = (client.client_id, client.client_secret) if client.client_secret else None

    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as http_client:
            response = await http_client.post(
                token_endpoint,
                data=payload,
                auth=auth,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise MCPOAuthError(f"Token endpoint request failed: {exc}") from exc

    if response.status_code >= 400:
        raise MCPOAuthError(
            f"Token endpoint returned {response.status_code} ({_error_code(response)})"
        )
    try:
        return _parse_token_response(response.json())
    except ValueError as exc:
        raise MCPOAuthError("Token endpoint response is not valid JSON") from exc


async def exchange_authorization_code(
    *,
    metadata: ServerOAuthMetadata,
    client: RegisteredClient,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> TokenResponse:
    """Redeem an authorization code for tokens using the PKCE verifier."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    resource = metadata.resource
    if resource:
        # RFC 8707: bind the issued token to this resource so it cannot be
        # replayed against a different server behind the same provider.
        data["resource"] = resource
    return await _post_token_endpoint(
        metadata.authorization_server.token_endpoint, data, client
    )


async def refresh_access_token(
    *,
    metadata: ServerOAuthMetadata,
    client: RegisteredClient,
    refresh_token: str,
    scopes: Optional[str] = None,
) -> TokenResponse:
    """Exchange a refresh token for a new access token."""
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if scopes:
        data["scope"] = scopes
    resource = metadata.resource
    if resource:
        data["resource"] = resource

    response = await _post_token_endpoint(
        metadata.authorization_server.token_endpoint, data, client
    )
    # RFC 6749 section 6: a refresh response may omit the refresh token, in
    # which case the existing one stays valid. Carry it forward so the caller
    # does not overwrite it with None and lose the ability to refresh again.
    if response.refresh_token is None:
        response.refresh_token = refresh_token
    return response


async def revoke_token(
    *,
    metadata: ServerOAuthMetadata,
    client: RegisteredClient,
    token_value: str,
    token_type_hint: str = "access_token",
) -> bool:
    """Best-effort RFC 7009 revocation. Returns False when unsupported/failed.

    Disconnect must succeed locally even if the provider is unreachable, so a
    failure here is logged and swallowed rather than raised.
    """
    endpoint = metadata.authorization_server.revocation_endpoint
    if not endpoint:
        return False
    data = {
        "token": token_value,
        "token_type_hint": token_type_hint,
        "client_id": client.client_id,
    }
    auth = (client.client_id, client.client_secret) if client.client_secret else None
    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as http_client:
            response = await http_client.post(endpoint, data=data, auth=auth)
    except httpx.HTTPError as exc:
        logger.warning("MCP OAuth revocation request failed: %s", exc)
        return False
    if response.status_code >= 400:
        logger.warning(
            "MCP OAuth revocation returned %s (%s)",
            response.status_code,
            _error_code(response),
        )
        return False
    return True
