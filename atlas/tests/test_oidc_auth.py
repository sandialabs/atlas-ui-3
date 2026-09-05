"""Unit tests for OIDC login, confidential-client auth, and delegation.

Covers:
- provider metadata discovery and its validation rules
- PKCE / state / nonce generation and the authorize URL
- confidential-client authentication (secret basic/post, private_key_jwt)
- the server-side OIDC session store
- RFC 8693 token exchange and Entra OBO delegation, including caching
- the login/callback/logout/status routes
- AuthMiddleware resolving identity from an OIDC session
"""

import base64
import hashlib
import time
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from atlas.core.oidc.client_authentication import (
    ClientAuthenticationError,
    build_client_assertion,
    build_client_credentials,
    clear_private_key_cache,
)
from atlas.core.oidc.delegation import (
    DelegatedToken,
    DelegationError,
    DelegationManager,
    DelegationRequest,
    EntraOboProvider,
    TokenExchangeProvider,
    build_provider,
)
from atlas.core.oidc.discovery import (
    OIDCDiscoveryError,
    ProviderMetadata,
    discovery_url,
    parse_provider_metadata,
)
from atlas.core.oidc.oidc_client import (
    build_authorize_url,
    extract_user_identifier,
    generate_pkce_pair,
    normalize_scopes,
)
from atlas.core.oidc.session import OIDCSessionStore

DISCOVERY_DOC = {
    "issuer": "https://idp.example.gov",
    "authorization_endpoint": "https://idp.example.gov/authorize",
    "token_endpoint": "https://idp.example.gov/token",
    "jwks_uri": "https://idp.example.gov/jwks",
    "userinfo_endpoint": "https://idp.example.gov/userinfo",
    "end_session_endpoint": "https://idp.example.gov/logout",
    "code_challenge_methods_supported": ["S256"],
}


@pytest.fixture
def rsa_key_file(tmp_path):
    """A PEM private key on disk, for private_key_jwt tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "client.pem"
    path.write_bytes(pem)
    clear_private_key_cache()
    yield path, key.public_key()
    clear_private_key_cache()


# -- Discovery -------------------------------------------------------------


class TestDiscovery:
    def test_discovery_url_strips_trailing_slash(self):
        assert discovery_url("https://idp.example.gov/") == (
            "https://idp.example.gov/.well-known/openid-configuration"
        )

    def test_parse_returns_metadata(self):
        metadata = parse_provider_metadata("https://idp.example.gov", DISCOVERY_DOC)
        assert metadata.token_endpoint == "https://idp.example.gov/token"
        assert metadata.end_session_endpoint == "https://idp.example.gov/logout"
        assert metadata.supports_pkce_s256()

    def test_issuer_mismatch_is_rejected(self):
        document = {**DISCOVERY_DOC, "issuer": "https://evil.example"}
        with pytest.raises(OIDCDiscoveryError):
            parse_provider_metadata("https://idp.example.gov", document)

    def test_missing_endpoint_is_rejected(self):
        document = {k: v for k, v in DISCOVERY_DOC.items() if k != "token_endpoint"}
        with pytest.raises(OIDCDiscoveryError):
            parse_provider_metadata("https://idp.example.gov", document)

    def test_pkce_unsupported_when_only_plain_advertised(self):
        document = {**DISCOVERY_DOC, "code_challenge_methods_supported": ["plain"]}
        metadata = parse_provider_metadata("https://idp.example.gov", document)
        assert not metadata.supports_pkce_s256()

    def test_pkce_assumed_when_not_advertised(self):
        document = {k: v for k, v in DISCOVERY_DOC.items()
                    if k != "code_challenge_methods_supported"}
        metadata = parse_provider_metadata("https://idp.example.gov", document)
        assert metadata.supports_pkce_s256()


# -- PKCE and the authorization request ------------------------------------


class TestAuthorizationRequest:
    def test_pkce_challenge_is_s256_of_verifier(self):
        verifier, challenge = generate_pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        assert challenge == expected
        assert "=" not in challenge
        assert 43 <= len(verifier) <= 128

    def test_pkce_pairs_are_unique(self):
        assert generate_pkce_pair()[0] != generate_pkce_pair()[0]

    def test_authorize_url_carries_pkce_and_nonce(self):
        url = build_authorize_url(
            authorization_endpoint="https://idp.example.gov/authorize",
            client_id="atlas",
            redirect_uri="https://atlas.example.gov/auth/oidc/callback",
            scope="openid email",
            state="state-value",
            code_challenge="challenge-value",
            nonce="nonce-value",
        )
        assert url.startswith("https://idp.example.gov/authorize?")
        assert "code_challenge=challenge-value" in url
        assert "code_challenge_method=S256" in url
        assert "response_type=code" in url
        assert "nonce=nonce-value" in url

    def test_authorize_url_appends_to_existing_query(self):
        url = build_authorize_url(
            authorization_endpoint="https://idp.example.gov/authorize?tenant=a",
            client_id="atlas",
            redirect_uri="https://atlas.example.gov/cb",
            scope="openid",
            state="s",
            code_challenge="c",
            nonce="n",
        )
        assert "?tenant=a&" in url

    def test_normalize_scopes_inserts_openid_and_dedupes(self):
        assert normalize_scopes("email email profile") == "openid email profile"
        assert normalize_scopes(None) == "openid"

    def test_extract_user_identifier_falls_back(self):
        assert extract_user_identifier({"email": "a@b.gov"}) == "a@b.gov"
        assert extract_user_identifier({"preferred_username": "abc"}) == "abc"
        assert extract_user_identifier({"sub": "xyz"}) == "xyz"
        assert extract_user_identifier({}) is None


# -- Confidential client authentication ------------------------------------


class TestClientAuthentication:
    def test_client_secret_basic_uses_http_basic(self):
        credentials = build_client_credentials(
            client_id="atlas",
            token_endpoint="https://idp.example.gov/token",
            auth_method="client_secret_basic",
            client_secret="shhh",
        )
        assert credentials.basic_auth == ("atlas", "shhh")
        assert credentials.form_fields == {}

    def test_client_secret_post_uses_form_body(self):
        credentials = build_client_credentials(
            client_id="atlas",
            token_endpoint="https://idp.example.gov/token",
            auth_method="client_secret_post",
            client_secret="shhh",
        )
        assert credentials.basic_auth is None
        assert credentials.form_fields["client_secret"] == "shhh"

    def test_missing_secret_is_rejected(self):
        with pytest.raises(ClientAuthenticationError):
            build_client_credentials(
                client_id="atlas",
                token_endpoint="https://idp.example.gov/token",
                auth_method="client_secret_basic",
            )

    def test_unknown_method_is_rejected(self):
        with pytest.raises(ClientAuthenticationError):
            build_client_credentials(
                client_id="atlas",
                token_endpoint="https://idp.example.gov/token",
                auth_method="magic",
            )

    def test_private_key_jwt_produces_verifiable_assertion(self, rsa_key_file):
        path, public_key = rsa_key_file
        credentials = build_client_credentials(
            client_id="atlas",
            token_endpoint="https://idp.example.gov/token",
            auth_method="private_key_jwt",
            private_key_path=str(path),
            private_key_id="key-1",
        )
        assertion = credentials.form_fields["client_assertion"]
        assert credentials.basic_auth is None
        assert credentials.form_fields["client_assertion_type"] == (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )
        assert jwt.get_unverified_header(assertion)["kid"] == "key-1"

        claims = jwt.decode(
            assertion,
            public_key,
            algorithms=["RS256"],
            audience="https://idp.example.gov/token",
        )
        assert claims["iss"] == "atlas"
        assert claims["sub"] == "atlas"
        assert claims["exp"] > claims["iat"]

    def test_private_key_jwt_requires_a_key_path(self):
        with pytest.raises(ClientAuthenticationError):
            build_client_credentials(
                client_id="atlas",
                token_endpoint="https://idp.example.gov/token",
                auth_method="private_key_jwt",
            )

    def test_assertions_have_unique_jti(self, rsa_key_file):
        path, _ = rsa_key_file
        first = build_client_assertion(
            client_id="atlas",
            token_endpoint="https://idp.example.gov/token",
            private_key_path=str(path),
        )
        second = build_client_assertion(
            client_id="atlas",
            token_endpoint="https://idp.example.gov/token",
            private_key_path=str(path),
        )
        decode = lambda token: jwt.decode(token, options={"verify_signature": False})  # noqa: E731
        assert decode(first)["jti"] != decode(second)["jti"]

    def test_unsupported_algorithm_is_rejected(self, rsa_key_file):
        path, _ = rsa_key_file
        with pytest.raises(ClientAuthenticationError):
            build_client_assertion(
                client_id="atlas",
                token_endpoint="https://idp.example.gov/token",
                private_key_path=str(path),
                algorithm="HS256",
            )


# -- Session store ---------------------------------------------------------


class TestSessionStore:
    def test_create_and_get(self):
        store = OIDCSessionStore()
        session = store.create(user_id="a@b.gov", access_token="at")
        assert store.get(session.session_id).user_id == "a@b.gov"

    def test_expired_session_is_dropped(self):
        store = OIDCSessionStore()
        session = store.create(user_id="a@b.gov", max_age_seconds=60)
        session.expires_at = time.time() - 1
        assert store.get(session.session_id) is None
        assert store.count() == 0

    def test_remove(self):
        store = OIDCSessionStore()
        session = store.create(user_id="a@b.gov")
        assert store.remove(session.session_id) is True
        assert store.remove(session.session_id) is False

    def test_capacity_evicts_oldest(self):
        store = OIDCSessionStore(max_sessions=2)
        first = store.create(user_id="one@b.gov")
        first.created_at = 0
        store.create(user_id="two@b.gov")
        store.create(user_id="three@b.gov")
        assert store.get(first.session_id) is None
        assert store.count() <= 2

    def test_refresh_token_is_kept_when_response_omits_it(self):
        store = OIDCSessionStore()
        session = store.create(user_id="a@b.gov", refresh_token="rt")
        store.update_tokens(session.session_id, access_token="new", refresh_token=None)
        assert store.get(session.session_id).refresh_token == "rt"

    def test_public_dict_hides_token_material(self):
        store = OIDCSessionStore()
        session = store.create(
            user_id="a@b.gov",
            access_token="SECRET-ACCESS-TOKEN",
            refresh_token="SECRET-REFRESH-TOKEN",
        )
        public = session.to_public_dict()
        assert "SECRET-ACCESS-TOKEN" not in str(public)
        assert "SECRET-REFRESH-TOKEN" not in str(public)
        assert public["has_refresh_token"] is True

    def test_iter_sessions_skips_expired(self):
        store = OIDCSessionStore()
        live = store.create(user_id="live@b.gov")
        dead = store.create(user_id="dead@b.gov")
        dead.expires_at = time.time() - 1
        assert [s.session_id for s in store.iter_sessions()] == [live.session_id]


# -- Delegation ------------------------------------------------------------


def _credentials_factory():
    from atlas.core.oidc.client_authentication import ClientCredentials

    return lambda: ClientCredentials(basic_auth=("atlas", "shhh"))


class TestDelegation:
    @pytest.mark.asyncio
    async def test_token_exchange_sends_rfc8693_grant(self):
        provider = TokenExchangeProvider("https://idp.example.gov/token", _credentials_factory())
        captured = {}

        async def fake_post(endpoint, data, credentials):
            captured.update(data)
            return {"access_token": "downstream", "expires_in": 300, "scope": "read"}

        with patch("atlas.core.oidc.delegation._post_delegation_request", side_effect=fake_post):
            token = await provider.exchange(
                DelegationRequest(
                    user_id="a@b.gov",
                    subject_token="user-token",
                    audience="api://downstream",
                    scope="read",
                )
            )

        assert captured["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert captured["subject_token"] == "user-token"
        assert captured["audience"] == "api://downstream"
        assert token.access_token == "downstream"
        assert token.expires_at is not None

    @pytest.mark.asyncio
    async def test_token_exchange_requires_an_audience(self):
        provider = TokenExchangeProvider("https://idp.example.gov/token", _credentials_factory())
        with pytest.raises(DelegationError):
            await provider.exchange(
                DelegationRequest(user_id="a@b.gov", subject_token="user-token")
            )

    @pytest.mark.asyncio
    async def test_entra_obo_derives_default_scope_from_audience(self):
        provider = EntraOboProvider("https://login.microsoftonline.com/t/token", _credentials_factory())
        captured = {}

        async def fake_post(endpoint, data, credentials):
            captured.update(data)
            return {"access_token": "downstream", "expires_in": 300}

        with patch("atlas.core.oidc.delegation._post_delegation_request", side_effect=fake_post):
            await provider.exchange(
                DelegationRequest(
                    user_id="a@b.gov", subject_token="user-token", audience="api://downstream"
                )
            )

        assert captured["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        assert captured["requested_token_use"] == "on_behalf_of"
        assert captured["scope"] == "api://downstream/.default"
        assert captured["assertion"] == "user-token"

    def test_build_provider_rejects_unknown_name(self):
        with pytest.raises(DelegationError):
            build_provider("nope", "https://idp.example.gov/token", _credentials_factory())

    @pytest.mark.asyncio
    async def test_manager_caches_and_invalidates(self):
        calls = []

        class CountingProvider(TokenExchangeProvider):
            async def exchange(self, request):
                calls.append(request)
                return DelegatedToken(
                    access_token=f"token-{len(calls)}", expires_at=time.time() + 600
                )

        manager = DelegationManager(
            CountingProvider("https://idp.example.gov/token", _credentials_factory())
        )
        request = DelegationRequest(
            user_id="a@b.gov", subject_token="user-token", audience="api://downstream"
        )
        first = await manager.get_token(request)
        second = await manager.get_token(request)
        assert first.access_token == second.access_token == "token-1"
        assert len(calls) == 1

        assert manager.invalidate_user("A@B.GOV") == 1
        third = await manager.get_token(request)
        assert third.access_token == "token-2"

    @pytest.mark.asyncio
    async def test_manager_does_not_cache_tokens_without_expiry(self):
        class NoExpiryProvider(TokenExchangeProvider):
            async def exchange(self, request):
                return DelegatedToken(access_token="token", expires_at=None)

        manager = DelegationManager(
            NoExpiryProvider("https://idp.example.gov/token", _credentials_factory())
        )
        request = DelegationRequest(
            user_id="a@b.gov", subject_token="t", audience="api://downstream"
        )
        await manager.get_token(request)
        assert manager._cache == {}

    def test_different_audiences_get_different_cache_keys(self):
        base = dict(user_id="a@b.gov", subject_token="t")
        one = DelegationRequest(audience="api://one", **base).cache_key()
        two = DelegationRequest(audience="api://two", **base).cache_key()
        assert one != two


# -- Routes ----------------------------------------------------------------


class _Settings:
    """Minimal stand-in for AppSettings covering the OIDC fields."""

    def __init__(self, **overrides):
        self.feature_oidc_auth_enabled = True
        self.oidc_issuer = "https://idp.example.gov"
        self.oidc_client_id = "atlas"
        self.oidc_client_secret = "shhh"
        self.oidc_client_auth_method = "client_secret_basic"
        self.oidc_private_key_path = None
        self.oidc_private_key_id = None
        self.oidc_private_key_algorithm = "RS256"
        self.oidc_redirect_uri = "https://atlas.example.gov/auth/oidc/callback"
        self.oidc_scopes = "openid profile email"
        self.oidc_session_secret = "test-session-secret"
        self.oidc_username_claim = "email"
        self.oidc_session_max_age_seconds = 3600
        self.oidc_post_logout_redirect_uri = None
        self.feature_oidc_delegation_enabled = False
        self.oidc_delegation_provider = "token_exchange"
        self.oidc_delegation_token_endpoint = None
        self.oidc_delegation_min_ttl_seconds = 60
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.fixture
def oidc_app():
    """A tiny app carrying only the OIDC routers plus session middleware."""
    from atlas.routes import oidc_auth_routes

    app = FastAPI()
    app.include_router(oidc_auth_routes.browser_router)
    app.include_router(oidc_auth_routes.api_router)
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    return app


@pytest.fixture
def metadata():
    return ProviderMetadata(
        issuer="https://idp.example.gov",
        authorization_endpoint="https://idp.example.gov/authorize",
        token_endpoint="https://idp.example.gov/token",
        jwks_uri="https://idp.example.gov/jwks",
        end_session_endpoint="https://idp.example.gov/logout",
        code_challenge_methods_supported=["S256"],
    )


def _patch_settings(settings):
    return patch(
        "atlas.routes.oidc_auth_routes.app_factory.get_config_manager",
        return_value=type("CM", (), {"app_settings": settings})(),
    )


class TestOIDCRoutes:
    def test_login_redirects_to_the_idp_with_pkce(self, oidc_app, metadata):
        with _patch_settings(_Settings()), patch(
            "atlas.routes.oidc_auth_routes.get_provider_metadata",
            AsyncMock(return_value=metadata),
        ):
            client = TestClient(oidc_app)
            response = client.get("/auth/oidc/login", follow_redirects=False)

        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("https://idp.example.gov/authorize?")
        assert "code_challenge_method=S256" in location
        assert "client_id=atlas" in location

    def test_login_is_404_when_disabled(self, oidc_app):
        with _patch_settings(_Settings(feature_oidc_auth_enabled=False)):
            client = TestClient(oidc_app)
            assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 404

    def test_callback_rejects_state_mismatch(self, oidc_app):
        with _patch_settings(_Settings()):
            client = TestClient(oidc_app)
            response = client.get(
                "/auth/oidc/callback?code=abc&state=not-the-one", follow_redirects=False
            )
        assert response.status_code == 302
        assert response.headers["location"] == "/?oidc_error=invalid_state"

    def test_callback_maps_unknown_idp_error_to_a_fixed_code(self, oidc_app):
        with _patch_settings(_Settings()):
            client = TestClient(oidc_app)
            response = client.get(
                "/auth/oidc/callback?error=%3Cscript%3E", follow_redirects=False
            )
        assert response.headers["location"] == "/?oidc_error=unknown_error"

    def test_callback_passes_through_known_error(self, oidc_app):
        with _patch_settings(_Settings()):
            client = TestClient(oidc_app)
            response = client.get(
                "/auth/oidc/callback?error=access_denied", follow_redirects=False
            )
        assert response.headers["location"] == "/?oidc_error=access_denied"

    def test_full_login_establishes_a_session(self, oidc_app, metadata):
        settings = _Settings()
        claims = {"email": "user@example.gov", "sub": "subject-1"}

        with _patch_settings(settings), patch(
            "atlas.routes.oidc_auth_routes.get_provider_metadata",
            AsyncMock(return_value=metadata),
        ), patch(
            "atlas.routes.oidc_auth_routes.exchange_code_for_tokens",
            AsyncMock(return_value={
                "access_token": "user-access-token",
                "refresh_token": "user-refresh-token",
                "id_token": "id-token",
                "expires_in": 3600,
                "scope": "openid email",
            }),
        ), patch(
            "atlas.routes.oidc_auth_routes.validate_id_token",
            AsyncMock(return_value=claims),
        ):
            client = TestClient(oidc_app)
            login = client.get("/auth/oidc/login", follow_redirects=False)
            state = login.headers["location"].split("state=")[1].split("&")[0]

            callback = client.get(
                f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
            )
            assert callback.status_code == 302
            assert callback.headers["location"] == "/"

            status = client.get("/api/auth/oidc/status").json()

        assert status["enabled"] is True
        assert status["authenticated"] is True
        assert status["session"]["user"] == "user@example.gov"
        # Token material must never reach the browser.
        assert "user-refresh-token" not in str(status)
        assert "user-access-token" not in str(status)

    def test_login_next_parameter_cannot_be_an_open_redirect(self, oidc_app, metadata):
        settings = _Settings()
        with _patch_settings(settings), patch(
            "atlas.routes.oidc_auth_routes.get_provider_metadata",
            AsyncMock(return_value=metadata),
        ), patch(
            "atlas.routes.oidc_auth_routes.exchange_code_for_tokens",
            AsyncMock(return_value={"id_token": "id-token", "access_token": "at"}),
        ), patch(
            "atlas.routes.oidc_auth_routes.validate_id_token",
            AsyncMock(return_value={"email": "user@example.gov"}),
        ):
            client = TestClient(oidc_app)
            login = client.get(
                "/auth/oidc/login?next=https://evil.example/steal", follow_redirects=False
            )
            state = login.headers["location"].split("state=")[1].split("&")[0]
            callback = client.get(
                f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
            )
        assert callback.headers["location"] == "/"

    def test_login_next_parameter_keeps_a_relative_path(self, oidc_app, metadata):
        with _patch_settings(_Settings()), patch(
            "atlas.routes.oidc_auth_routes.get_provider_metadata",
            AsyncMock(return_value=metadata),
        ), patch(
            "atlas.routes.oidc_auth_routes.exchange_code_for_tokens",
            AsyncMock(return_value={"id_token": "id-token", "access_token": "at"}),
        ), patch(
            "atlas.routes.oidc_auth_routes.validate_id_token",
            AsyncMock(return_value={"email": "user@example.gov"}),
        ):
            client = TestClient(oidc_app)
            login = client.get("/auth/oidc/login?next=/workspace", follow_redirects=False)
            state = login.headers["location"].split("state=")[1].split("&")[0]
            callback = client.get(
                f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
            )
        assert callback.headers["location"] == "/workspace"

    def test_status_reports_disabled_without_touching_the_session(self, oidc_app):
        with _patch_settings(_Settings(feature_oidc_auth_enabled=False)):
            client = TestClient(oidc_app)
            body = client.get("/api/auth/oidc/status").json()
        assert body == {"enabled": False, "authenticated": False, "session": None}

    def test_logout_uses_the_provider_end_session_endpoint(self, oidc_app, metadata):
        with _patch_settings(_Settings()), patch(
            "atlas.routes.oidc_auth_routes.get_provider_metadata",
            AsyncMock(return_value=metadata),
        ):
            client = TestClient(oidc_app)
            response = client.get("/auth/oidc/logout", follow_redirects=False)
        assert response.headers["location"].startswith("https://idp.example.gov/logout?")


# -- Middleware integration -------------------------------------------------


class TestMiddlewareOIDCSession:
    """AuthMiddleware must accept an OIDC session as a first-class auth source."""

    def _build_app(self, *, oidc_enabled=True, proxy_secret_enabled=True):
        from fastapi import Request as FastAPIRequest

        from atlas.core.middleware import AuthMiddleware
        from atlas.core.oidc.session import SESSION_COOKIE_KEY

        app = FastAPI()

        @app.get("/api/whoami")
        async def whoami(request: FastAPIRequest):
            return {"user": request.state.user_email}

        # Lives under /auth/oidc/, which AuthMiddleware skips, so the test can
        # plant a session cookie the same way the real callback route does.
        @app.get("/auth/oidc/test-establish")
        async def establish(request: FastAPIRequest, sid: str):
            request.session[SESSION_COOKIE_KEY] = sid
            return {"ok": True}

        app.add_middleware(
            AuthMiddleware,
            debug_mode=False,
            proxy_secret_enabled=proxy_secret_enabled,
            proxy_secret="proxy-secret",
            oidc_enabled=oidc_enabled,
        )
        app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
        return app

    def test_oidc_session_authenticates_without_a_header_or_proxy_secret(self):
        from atlas.core.oidc.session import get_session_store

        store = get_session_store()
        store.clear()
        session = store.create(user_id="user@example.gov")
        try:
            client = TestClient(self._build_app())
            client.get(f"/auth/oidc/test-establish?sid={session.session_id}")
            response = client.get("/api/whoami")
            assert response.status_code == 200
            assert response.json() == {"user": "user@example.gov"}
        finally:
            store.clear()

    def test_session_is_ignored_when_oidc_is_disabled(self):
        from atlas.core.oidc.session import get_session_store

        store = get_session_store()
        store.clear()
        session = store.create(user_id="user@example.gov")
        try:
            client = TestClient(
                self._build_app(oidc_enabled=False, proxy_secret_enabled=False)
            )
            client.get(f"/auth/oidc/test-establish?sid={session.session_id}")
            assert client.get("/api/whoami").status_code == 401
        finally:
            store.clear()

    def test_without_a_session_the_header_path_still_applies(self):
        from atlas.core.oidc.session import get_session_store

        get_session_store().clear()
        client = TestClient(self._build_app(proxy_secret_enabled=False))
        response = client.get("/api/whoami", headers={"X-User-Email": "header@example.gov"})
        assert response.json() == {"user": "header@example.gov"}

    def test_missing_everything_is_still_rejected(self):
        from atlas.core.oidc.session import get_session_store

        get_session_store().clear()
        client = TestClient(self._build_app(proxy_secret_enabled=False))
        assert client.get("/api/whoami").status_code == 401

    def test_expired_session_does_not_authenticate(self):
        from atlas.core.oidc.session import get_session_store

        store = get_session_store()
        store.clear()
        session = store.create(user_id="user@example.gov", max_age_seconds=60)
        try:
            client = TestClient(self._build_app(proxy_secret_enabled=False))
            client.get(f"/auth/oidc/test-establish?sid={session.session_id}")
            session.expires_at = time.time() - 1
            assert client.get("/api/whoami").status_code == 401
        finally:
            store.clear()
