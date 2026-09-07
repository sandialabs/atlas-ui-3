"""Tests for the MCP OAuth 2.1 discovery and authorization-code flow.

HTTP is exercised through ``httpx.MockTransport`` rather than by patching the
functions under test, so request construction -- form encoding, PKCE
parameters, resource indicators, client authentication -- is what the
assertions actually see.
"""

import json
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from atlas.modules.mcp_tools import mcp_oauth
from atlas.modules.mcp_tools.mcp_oauth import (
    AuthorizationServerMetadata,
    MCPOAuthError,
    ProtectedResourceMetadata,
    RegisteredClient,
    ServerOAuthMetadata,
    authorization_server_metadata_urls,
    default_resource_metadata_urls,
    discover_authorization_server,
    exchange_authorization_code,
    get_server_oauth_metadata,
    parse_authorization_server_metadata,
    parse_protected_resource_metadata,
    parse_resource_metadata_challenge,
    refresh_access_token,
    register_client,
    revoke_token,
    validate_endpoint_url,
)

MCP_URL = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"


def _mock_httpx(handler):
    """Route every httpx client built inside mcp_oauth through ``handler``."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    return patch.object(mcp_oauth.httpx, "AsyncClient", factory)


def _as_metadata(**overrides):
    base = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "revocation_endpoint": f"{ISSUER}/revoke",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
    base.update(overrides)
    return base


def _server_metadata(**overrides):
    return ServerOAuthMetadata(
        mcp_url=MCP_URL,
        authorization_server=parse_authorization_server_metadata(
            ISSUER, _as_metadata(**overrides)
        ),
        protected_resource=ProtectedResourceMetadata(
            resource="https://mcp.example.com",
            authorization_servers=[ISSUER],
            scopes_supported=["read", "write"],
        ),
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    mcp_oauth.clear_metadata_cache()
    yield
    mcp_oauth.clear_metadata_cache()


# --- WWW-Authenticate challenge parsing ----------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ('Bearer resource_metadata="https://a.example/.well-known/x"',
         "https://a.example/.well-known/x"),
        ('Bearer realm="mcp", resource_metadata="https://a.example/m", error="x"',
         "https://a.example/m"),
        ("Bearer resource_metadata=https://a.example/bare", "https://a.example/bare"),
        ('bearer RESOURCE_METADATA="https://a.example/case"', "https://a.example/case"),
        ('Bearer error="missing_token"', None),
        (None, None),
        ("", None),
    ],
)
def test_parse_resource_metadata_challenge(header, expected):
    assert parse_resource_metadata_challenge(header) == expected


def test_default_resource_metadata_urls_uses_path_insertion():
    urls = default_resource_metadata_urls("https://mcp.example.com/mcp")
    assert urls[0] == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    assert "https://mcp.example.com/.well-known/oauth-protected-resource" in urls


def test_authorization_server_metadata_urls_prefers_oauth_document():
    urls = authorization_server_metadata_urls("https://auth.example.com")
    assert urls[0] == "https://auth.example.com/.well-known/oauth-authorization-server"
    assert "https://auth.example.com/.well-known/openid-configuration" in urls


# --- Transport requirements ----------------------------------------------


def test_validate_endpoint_url_accepts_https():
    assert validate_endpoint_url("https://a.example/x", what="test") == "https://a.example/x"


def test_validate_endpoint_url_allows_http_on_loopback():
    assert validate_endpoint_url("http://127.0.0.1:9000/x", what="test")
    assert validate_endpoint_url("http://localhost:9000/x", what="test")


def test_validate_endpoint_url_rejects_plaintext_remote():
    with pytest.raises(MCPOAuthError, match="https"):
        validate_endpoint_url("http://evil.example/x", what="test")


def test_discovered_plaintext_endpoint_is_rejected():
    """A compromised provider cannot downgrade the flow to cleartext."""
    with pytest.raises(MCPOAuthError, match="https"):
        parse_authorization_server_metadata(
            ISSUER, _as_metadata(token_endpoint="http://evil.example/token")
        )


# --- Metadata validation --------------------------------------------------


def test_parse_protected_resource_metadata():
    metadata = parse_protected_resource_metadata(
        {
            "resource": "https://mcp.example.com",
            "authorization_servers": [ISSUER],
            "scopes_supported": ["read", "write"],
        }
    )
    assert metadata.authorization_servers == [ISSUER]
    assert metadata.scopes_supported == ["read", "write"]


def test_parse_protected_resource_metadata_requires_authorization_servers():
    with pytest.raises(MCPOAuthError, match="authorization_servers"):
        parse_protected_resource_metadata({"resource": "https://mcp.example.com"})


def test_parse_protected_resource_metadata_rejects_non_object():
    with pytest.raises(MCPOAuthError):
        parse_protected_resource_metadata(["not", "an", "object"])


def test_authorization_server_issuer_must_match():
    """RFC 8414 3.3: a redirect must not be able to substitute a provider."""
    document = _as_metadata(issuer="https://attacker.example")
    with pytest.raises(MCPOAuthError, match="issuer"):
        parse_authorization_server_metadata(ISSUER, document)


def test_authorization_server_requires_core_endpoints():
    document = _as_metadata()
    del document["token_endpoint"]
    with pytest.raises(MCPOAuthError, match="token_endpoint"):
        parse_authorization_server_metadata(ISSUER, document)


def test_trailing_slash_issuer_matches():
    metadata = parse_authorization_server_metadata(
        ISSUER + "/", _as_metadata(issuer=ISSUER)
    )
    assert metadata.issuer == ISSUER


def test_supports_pkce_s256_defaults_to_true_when_unadvertised():
    metadata = parse_authorization_server_metadata(
        ISSUER, _as_metadata(code_challenge_methods_supported=[])
    )
    assert metadata.supports_pkce_s256() is True


def test_supports_pkce_s256_false_when_only_plain():
    metadata = parse_authorization_server_metadata(
        ISSUER, _as_metadata(code_challenge_methods_supported=["plain"])
    )
    assert metadata.supports_pkce_s256() is False


# --- Full discovery chain -------------------------------------------------


def _discovery_handler(*, challenge=True, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        url = str(request.url)
        if url == MCP_URL:
            if not challenge:
                return httpx.Response(200, json={})
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata='
                        '"https://mcp.example.com/.well-known/oauth-protected-resource"'
                    )
                },
                json={"error": "missing_token"},
            )
        if url.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com",
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["read", "write"],
                },
            )
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=_as_metadata())
        return httpx.Response(404, json={"error": "not_found"})

    return handler


@pytest.mark.asyncio
async def test_discovery_follows_the_401_challenge():
    calls = []
    with _mock_httpx(_discovery_handler(calls=calls)):
        metadata = await get_server_oauth_metadata(MCP_URL)

    assert metadata.authorization_server.issuer == ISSUER
    assert metadata.authorization_server.registration_endpoint == f"{ISSUER}/register"
    assert metadata.resource == "https://mcp.example.com"
    assert metadata.default_scopes() == ["read", "write"]
    # The challenge probe really happened, and it drove the metadata fetch.
    assert MCP_URL in calls
    assert any("oauth-protected-resource" in call for call in calls)


@pytest.mark.asyncio
async def test_discovery_falls_back_to_well_known_without_a_challenge():
    """A server that does not answer 401 still resolves via the well-known path."""
    with _mock_httpx(_discovery_handler(challenge=False)):
        metadata = await get_server_oauth_metadata(MCP_URL)
    assert metadata.authorization_server.issuer == ISSUER


@pytest.mark.asyncio
async def test_discovery_is_cached_per_url():
    calls = []
    with _mock_httpx(_discovery_handler(calls=calls)):
        await get_server_oauth_metadata(MCP_URL)
        first = len(calls)
        await get_server_oauth_metadata(MCP_URL)
        assert len(calls) == first


@pytest.mark.asyncio
async def test_discovery_error_when_nothing_is_published():
    def handler(request):
        return httpx.Response(404, json={"error": "not_found"})

    with _mock_httpx(handler):
        with pytest.raises(MCPOAuthError, match="authorization server"):
            await get_server_oauth_metadata(MCP_URL)


@pytest.mark.asyncio
async def test_discover_authorization_server_tries_openid_configuration():
    def handler(request):
        if str(request.url).endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_as_metadata())
        return httpx.Response(404, json={})

    with _mock_httpx(handler):
        metadata = await discover_authorization_server(ISSUER)
    assert metadata.token_endpoint == f"{ISSUER}/token"


# --- Dynamic client registration -----------------------------------------


@pytest.mark.asyncio
async def test_register_client_sends_rfc7591_body():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            201, json={"client_id": "dcr-client-1", "client_secret_expires_at": 0}
        )

    metadata = parse_authorization_server_metadata(ISSUER, _as_metadata())
    with _mock_httpx(handler):
        client = await register_client(
            metadata,
            redirect_uri="https://atlas.example/api/mcp/auth/s/oauth/callback",
            client_name="Atlas UI",
            scopes="read write",
        )

    assert seen["url"] == f"{ISSUER}/register"
    assert seen["body"]["redirect_uris"] == [
        "https://atlas.example/api/mcp/auth/s/oauth/callback"
    ]
    assert seen["body"]["grant_types"] == ["authorization_code", "refresh_token"]
    assert seen["body"]["token_endpoint_auth_method"] == "none"
    assert seen["body"]["client_name"] == "Atlas UI"
    assert client.client_id == "dcr-client-1"
    # 0 means "never expires" per RFC 7591, not "already expired".
    assert client.is_expired() is False


@pytest.mark.asyncio
async def test_register_client_without_registration_endpoint():
    metadata = parse_authorization_server_metadata(
        ISSUER, _as_metadata(registration_endpoint=None)
    )
    with pytest.raises(MCPOAuthError, match="dynamic client registration"):
        await register_client(
            metadata, redirect_uri="https://atlas.example/cb", client_name="Atlas UI"
        )


@pytest.mark.asyncio
async def test_register_client_surfaces_provider_error_code_only():
    def handler(request):
        # A provider echoing a credential back in the body must not be logged
        # or surfaced; only the `error` member is.
        return httpx.Response(
            400, json={"error": "invalid_redirect_uri", "secret_echo": "s3cr3t"}
        )

    metadata = parse_authorization_server_metadata(ISSUER, _as_metadata())
    with _mock_httpx(handler):
        with pytest.raises(MCPOAuthError) as exc:
            await register_client(
                metadata, redirect_uri="https://atlas.example/cb", client_name="Atlas UI"
            )
    assert "invalid_redirect_uri" in str(exc.value)
    assert "s3cr3t" not in str(exc.value)


# --- Token endpoint -------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_authorization_code_sends_pkce_and_resource():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "scope": "read write",
            },
        )

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        response = await exchange_authorization_code(
            metadata=_server_metadata(),
            client=client,
            code="the-code",
            redirect_uri="https://a/cb",
            code_verifier="verifier-value",
        )

    assert seen["url"] == f"{ISSUER}/token"
    assert seen["form"]["grant_type"] == ["authorization_code"]
    assert seen["form"]["code_verifier"] == ["verifier-value"]
    assert seen["form"]["client_id"] == ["c1"]
    # RFC 8707: the token is bound to the MCP resource.
    assert seen["form"]["resource"] == ["https://mcp.example.com"]
    # A public client sends no Basic credentials.
    assert seen["authorization"] is None
    assert response.access_token == "at-1"
    assert response.refresh_token == "rt-1"
    assert response.expires_at and response.expires_at > time.time()


@pytest.mark.asyncio
async def test_confidential_client_authenticates_with_its_secret():
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"access_token": "at-1"})

    client = RegisteredClient(
        client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb", client_secret="sec"
    )
    with _mock_httpx(handler):
        await exchange_authorization_code(
            metadata=_server_metadata(),
            client=client,
            code="c",
            redirect_uri="https://a/cb",
            code_verifier="v",
        )
    assert seen["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_token_endpoint_error_raises_with_code():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        with pytest.raises(MCPOAuthError, match="invalid_grant"):
            await exchange_authorization_code(
                metadata=_server_metadata(),
                client=client,
                code="c",
                redirect_uri="https://a/cb",
                code_verifier="v",
            )


@pytest.mark.asyncio
async def test_token_response_without_access_token_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"token_type": "Bearer"})

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        with pytest.raises(MCPOAuthError, match="access_token"):
            await exchange_authorization_code(
                metadata=_server_metadata(),
                client=client,
                code="c",
                redirect_uri="https://a/cb",
                code_verifier="v",
            )


@pytest.mark.asyncio
async def test_refresh_carries_forward_an_omitted_refresh_token():
    """RFC 6749 6: an omitted refresh_token means the old one stays valid."""

    def handler(request):
        return httpx.Response(200, json={"access_token": "at-2", "expires_in": 60})

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        response = await refresh_access_token(
            metadata=_server_metadata(), client=client, refresh_token="rt-original"
        )
    assert response.access_token == "at-2"
    assert response.refresh_token == "rt-original"


@pytest.mark.asyncio
async def test_refresh_prefers_a_rotated_refresh_token():
    def handler(request):
        return httpx.Response(
            200, json={"access_token": "at-2", "refresh_token": "rt-rotated"}
        )

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        response = await refresh_access_token(
            metadata=_server_metadata(), client=client, refresh_token="rt-original"
        )
    assert response.refresh_token == "rt-rotated"


# --- Revocation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_token_posts_to_revocation_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200)

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        assert await revoke_token(
            metadata=_server_metadata(),
            client=client,
            token_value="rt-1",
            token_type_hint="refresh_token",
        )
    assert seen["url"] == f"{ISSUER}/revoke"
    assert seen["form"]["token_type_hint"] == ["refresh_token"]


@pytest.mark.asyncio
async def test_revoke_token_returns_false_when_unsupported():
    metadata = _server_metadata(revocation_endpoint=None)
    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    assert await revoke_token(metadata=metadata, client=client, token_value="x") is False


@pytest.mark.asyncio
async def test_revoke_token_swallows_provider_failure():
    """Disconnect must succeed locally even when the provider is down."""

    def handler(request):
        raise httpx.ConnectError("provider down")

    client = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri="https://a/cb")
    with _mock_httpx(handler):
        assert await revoke_token(
            metadata=_server_metadata(), client=client, token_value="x"
        ) is False


# --- Client registration store -------------------------------------------


class TestOAuthClientStore:
    """The DCR registration is per-server, encrypted, and survives a restart."""

    KEY = "test-encryption-key-12345-at-least-32-chars"

    def _store(self, tmp_path):
        from atlas.modules.mcp_tools.oauth_client_store import MCPOAuthClientStore

        return MCPOAuthClientStore(storage_dir=tmp_path, encryption_key=self.KEY)

    def _client(self, **overrides):
        values = {
            "client_id": "dcr-1",
            "issuer": ISSUER,
            "redirect_uri": "https://atlas.example/cb",
            "client_secret": "s3cr3t",
            "registered_at": time.time(),
        }
        values.update(overrides)
        return RegisteredClient(**values)

    def test_round_trip(self, tmp_path):
        store = self._store(tmp_path)
        store.put("srv", self._client())
        loaded = store.get("srv", ISSUER)
        assert loaded is not None
        assert loaded.client_id == "dcr-1"
        assert loaded.client_secret == "s3cr3t"

    def test_persists_across_instances(self, tmp_path):
        self._store(tmp_path).put("srv", self._client())
        assert self._store(tmp_path).get("srv", ISSUER).client_id == "dcr-1"

    def test_written_file_is_encrypted(self, tmp_path):
        self._store(tmp_path).put("srv", self._client())
        raw = (tmp_path / "mcp_oauth_clients.enc").read_bytes()
        assert b"s3cr3t" not in raw
        assert b"dcr-1" not in raw

    def test_registrations_are_keyed_by_issuer(self, tmp_path):
        """Repointing a server at another provider must not reuse the client_id."""
        store = self._store(tmp_path)
        store.put("srv", self._client())
        assert store.get("srv", "https://other-auth.example") is None

    def test_registrations_are_keyed_by_server(self, tmp_path):
        store = self._store(tmp_path)
        store.put("srv", self._client())
        assert store.get("another-srv", ISSUER) is None

    def test_expired_secret_is_dropped(self, tmp_path):
        store = self._store(tmp_path)
        store.put("srv", self._client(client_secret_expires_at=time.time() - 10))
        assert store.get("srv", ISSUER) is None

    def test_zero_expiry_means_never_expires(self, tmp_path):
        store = self._store(tmp_path)
        store.put("srv", self._client(client_secret_expires_at=0))
        assert store.get("srv", ISSUER) is not None

    def test_remove(self, tmp_path):
        store = self._store(tmp_path)
        store.put("srv", self._client())
        assert store.remove("srv", ISSUER) is True
        assert store.get("srv", ISSUER) is None
        assert store.remove("srv", ISSUER) is False

    def test_rotated_key_resets_rather_than_crashing(self, tmp_path):
        from atlas.modules.mcp_tools.oauth_client_store import MCPOAuthClientStore

        self._store(tmp_path).put("srv", self._client())
        other = MCPOAuthClientStore(
            storage_dir=tmp_path, encryption_key="a-completely-different-key-32-chars!!"
        )
        assert other.get("srv", ISSUER) is None
