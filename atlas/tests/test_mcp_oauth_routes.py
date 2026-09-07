"""Tests for the MCP OAuth browser routes and the orchestration service.

Covers the security properties the flow depends on: group authorization on
both routes, single-use session-bound state, rejection of a callback that
crosses users or servers, and the fact that a provider-supplied error string
is never reflected back to the browser verbatim.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from atlas.modules.mcp_tools import mcp_oauth_service
from atlas.modules.mcp_tools.mcp_oauth import (
    MCPOAuthError,
    ProtectedResourceMetadata,
    RegisteredClient,
    ServerOAuthMetadata,
    TokenResponse,
    parse_authorization_server_metadata,
)
from atlas.modules.mcp_tools.token_storage import StoredToken

USER = "user@example.gov"
OTHER_USER = "other@example.gov"
SERVER = "remote-mcp"
ISSUER = "https://auth.example.com"
BASE_URL = "https://atlas.example.gov"
CALLBACK = f"{BASE_URL}/api/mcp/auth/{SERVER}/oauth/callback"

SERVERS_CONFIG = {
    SERVER: {
        "url": "https://mcp.example.com/mcp",
        "auth_type": "oauth",
        "groups": ["users"],
    },
    "bearer-server": {
        "url": "https://other.example.com/mcp",
        "auth_type": "bearer",
        "groups": ["users"],
    },
    "forbidden-server": {
        "url": "https://secret.example.com/mcp",
        "auth_type": "oauth",
        "groups": ["admins"],
    },
}


def _metadata():
    return ServerOAuthMetadata(
        mcp_url="https://mcp.example.com/mcp",
        authorization_server=parse_authorization_server_metadata(
            ISSUER,
            {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "registration_endpoint": f"{ISSUER}/register",
                "revocation_endpoint": f"{ISSUER}/revoke",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
            },
        ),
        protected_resource=ProtectedResourceMetadata(
            resource="https://mcp.example.com",
            authorization_servers=[ISSUER],
            scopes_supported=["read", "write"],
        ),
    )


class _FakeTokenStorage:
    """In-memory stand-in for the encrypted store."""

    def __init__(self):
        self.tokens = {}

    def _key(self, user, server):
        return (user.lower(), server)

    def store_token(self, user_email, server_name, token_value, token_type="bearer",
                    expires_at=None, scopes=None, refresh_token=None, metadata=None):
        token = StoredToken(
            token_type=token_type,
            token_value=token_value,
            user_email=user_email.lower(),
            server_name=server_name,
            created_at=time.time(),
            expires_at=expires_at,
            scopes=scopes,
            refresh_token=refresh_token,
            metadata=metadata,
        )
        self.tokens[self._key(user_email, server_name)] = token
        return token

    def get_token(self, user_email, server_name):
        return self.tokens.get(self._key(user_email, server_name))

    def get_valid_token(self, user_email, server_name):
        token = self.get_token(user_email, server_name)
        return None if token is None or token.is_expired() else token

    def update_oauth_tokens(self, user_email, server_name, access_token,
                            expires_at=None, refresh_token=None, scopes=None):
        existing = self.get_token(user_email, server_name)
        return self.store_token(
            user_email=user_email,
            server_name=server_name,
            token_value=access_token,
            token_type="oauth_access",
            expires_at=expires_at,
            scopes=scopes or (existing.scopes if existing else None),
            refresh_token=refresh_token or (existing.refresh_token if existing else None),
            metadata=existing.metadata if existing else None,
        )


class _FakeManager:
    def __init__(self, authorized=None):
        self.servers_config = SERVERS_CONFIG
        self._authorized = authorized if authorized is not None else [SERVER, "bearer-server"]
        self.invalidated = []

    async def get_authorized_servers(self, user, is_user_in_group):
        return list(self._authorized)

    async def _invalidate_user_client(self, user, server):
        self.invalidated.append((user, server))


@pytest.fixture
def storage():
    store = _FakeTokenStorage()
    with patch.object(mcp_oauth_service, "get_token_storage", return_value=store):
        yield store


@pytest.fixture
def manager():
    return _FakeManager()


@pytest.fixture
def app(manager):
    """A tiny app carrying only the MCP auth router plus session middleware."""
    from atlas.core.log_sanitizer import get_current_user
    from atlas.routes import mcp_auth_routes

    application = FastAPI()
    application.include_router(mcp_auth_routes.router)
    application.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    application.dependency_overrides[get_current_user] = lambda: USER
    application.state.manager = manager
    return application


def _patch_app_factory(manager, base_url=BASE_URL):
    settings = type(
        "S", (), {"mcp_oauth_redirect_base_url": base_url, "backend_public_url": None}
    )()
    return (
        patch(
            "atlas.routes.mcp_auth_routes.app_factory.get_mcp_manager",
            return_value=manager,
        ),
        patch(
            "atlas.routes.mcp_auth_routes.app_factory.get_config_manager",
            return_value=type("CM", (), {"app_settings": settings})(),
        ),
    )


def _patch_flow(registered=None, metadata=None):
    """Patch discovery and registration; the rest of the service runs for real."""
    return (
        patch.object(
            mcp_oauth_service, "get_server_oauth_metadata",
            AsyncMock(return_value=metadata or _metadata()),
        ),
        patch.object(
            mcp_oauth_service, "_resolve_client",
            AsyncMock(
                return_value=registered
                or RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri=CALLBACK)
            ),
        ),
    )


def _start(client, manager, server=SERVER):
    p1, p2 = _patch_app_factory(manager)
    f1, f2 = _patch_flow()
    with p1, p2, f1, f2:
        return client.get(f"/api/mcp/auth/{server}/oauth/start", follow_redirects=False)


# --- /oauth/start ---------------------------------------------------------


class TestOAuthStart:
    def test_redirects_to_provider_with_pkce(self, app, manager, storage):
        response = _start(TestClient(app), manager)
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith(f"{ISSUER}/authorize?")
        assert "code_challenge_method=S256" in location
        assert "client_id=c1" in location
        assert "response_type=code" in location
        # RFC 8707 resource indicator is carried into the authorize request.
        assert "resource=" in location
        # No nonce or empty scope parameter is emitted.
        assert "nonce=" not in location

    def test_requests_configured_scopes(self, app, manager, storage):
        response = _start(TestClient(app), manager)
        # Falls back to the scopes the protected resource advertises.
        assert "scope=read+write" in response.headers["location"]

    def test_rejects_server_the_user_cannot_access(self, app, manager, storage):
        response = _start(TestClient(app), manager, server="forbidden-server")
        assert response.status_code == 403

    def test_rejects_non_oauth_server(self, app, manager, storage):
        response = _start(TestClient(app), manager, server="bearer-server")
        assert response.status_code == 400

    def test_discovery_failure_redirects_with_an_error_code(self, app, manager, storage):
        p1, p2 = _patch_app_factory(manager)
        with p1, p2, patch.object(
            mcp_oauth_service, "prepare_authorization",
            AsyncMock(side_effect=MCPOAuthError("nothing published")),
        ):
            response = TestClient(app).get(
                f"/api/mcp/auth/{SERVER}/oauth/start", follow_redirects=False
            )
        assert response.status_code == 302
        assert "mcp_auth_error=discovery_failed" in response.headers["location"]

    def test_missing_base_url_is_reported_not_guessed(self, app, manager, storage):
        """The redirect_uri must never be derived from the inbound Host header."""
        p1, p2 = _patch_app_factory(manager, base_url=None)
        f1, f2 = _patch_flow()
        with p1, p2, f1, f2:
            response = TestClient(app).get(
                f"/api/mcp/auth/{SERVER}/oauth/start", follow_redirects=False
            )
        assert "mcp_auth_error=discovery_failed" in response.headers["location"]


# --- /oauth/callback ------------------------------------------------------


def _state_from(response):
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


class TestOAuthCallback:
    def _complete(self, client, manager, state, server=SERVER, code="the-code"):
        p1, p2 = _patch_app_factory(manager)
        f1, f2 = _patch_flow()
        with p1, p2, f1, f2, patch.object(
            mcp_oauth_service, "exchange_authorization_code",
            AsyncMock(
                return_value=TokenResponse(
                    access_token="at-1",
                    refresh_token="rt-1",
                    expires_at=time.time() + 3600,
                    scopes="read write",
                )
            ),
        ):
            return client.get(
                f"/api/mcp/auth/{server}/oauth/callback",
                params={"code": code, "state": state},
                follow_redirects=False,
            )

    def test_successful_callback_stores_the_token(self, app, manager, storage):
        client = TestClient(app)
        state = _state_from(_start(client, manager))
        response = self._complete(client, manager, state)

        assert response.status_code == 302
        assert "mcp_auth_success=1" in response.headers["location"]

        stored = storage.get_token(USER, SERVER)
        assert stored is not None
        assert stored.token_value == "at-1"
        assert stored.refresh_token == "rt-1"
        assert stored.token_type == "oauth_access"
        assert stored.metadata["source"] == mcp_oauth_service.OAUTH_METADATA_SOURCE
        # The cached client built while unauthenticated is dropped.
        assert (USER, SERVER) in manager.invalidated

    def test_state_is_single_use(self, app, manager, storage):
        client = TestClient(app)
        state = _state_from(_start(client, manager))
        assert "mcp_auth_success=1" in self._complete(client, manager, state).headers["location"]

        replay = self._complete(client, manager, state)
        assert "mcp_auth_error=invalid_state" in replay.headers["location"]

    def test_unknown_state_is_rejected(self, app, manager, storage):
        response = self._complete(TestClient(app), manager, "never-issued")
        assert "mcp_auth_error=invalid_state" in response.headers["location"]
        assert storage.get_token(USER, SERVER) is None

    def test_state_issued_for_another_server_is_rejected(self, app, manager, storage):
        client = TestClient(app)
        state = _state_from(_start(client, manager))
        manager._authorized.append("forbidden-server")
        response = self._complete(client, manager, state, server="forbidden-server")
        assert "mcp_auth_error=invalid_state" in response.headers["location"]

    def test_state_belonging_to_another_user_is_rejected(self, app, manager, storage):
        """A callback replayed in a different account must not mint a token."""
        from atlas.core.log_sanitizer import get_current_user

        client = TestClient(app)
        state = _state_from(_start(client, manager))

        app.dependency_overrides[get_current_user] = lambda: OTHER_USER
        response = self._complete(client, manager, state)

        assert "mcp_auth_error=invalid_state" in response.headers["location"]
        assert storage.get_token(OTHER_USER, SERVER) is None

    def test_missing_parameters_are_rejected(self, app, manager, storage):
        p1, p2 = _patch_app_factory(manager)
        with p1, p2:
            response = TestClient(app).get(
                f"/api/mcp/auth/{SERVER}/oauth/callback", follow_redirects=False
            )
        assert "mcp_auth_error=missing_params" in response.headers["location"]

    def test_provider_error_is_allowlisted(self, app, manager, storage):
        p1, p2 = _patch_app_factory(manager)
        with p1, p2:
            response = TestClient(app).get(
                f"/api/mcp/auth/{SERVER}/oauth/callback",
                params={"error": "access_denied"},
                follow_redirects=False,
            )
        assert "mcp_auth_error=access_denied" in response.headers["location"]

    def test_unknown_provider_error_is_not_reflected(self, app, manager, storage):
        """An attacker-crafted error string must not reach the redirect."""
        p1, p2 = _patch_app_factory(manager)
        with p1, p2:
            response = TestClient(app).get(
                f"/api/mcp/auth/{SERVER}/oauth/callback",
                params={"error": "<script>alert(1)</script>"},
                follow_redirects=False,
            )
        location = response.headers["location"]
        assert "mcp_auth_error=unknown_error" in location
        assert "script" not in location

    def test_token_exchange_failure_redirects_with_an_error(self, app, manager, storage):
        client = TestClient(app)
        state = _state_from(_start(client, manager))

        p1, p2 = _patch_app_factory(manager)
        f1, f2 = _patch_flow()
        with p1, p2, f1, f2, patch.object(
            mcp_oauth_service, "exchange_authorization_code",
            AsyncMock(side_effect=MCPOAuthError("invalid_grant")),
        ):
            response = client.get(
                f"/api/mcp/auth/{SERVER}/oauth/callback",
                params={"code": "c", "state": state},
                follow_redirects=False,
            )
        assert "mcp_auth_error=token_exchange_failed" in response.headers["location"]
        assert storage.get_token(USER, SERVER) is None

    def test_expired_pending_state_is_rejected(self, app, manager, storage):
        client = TestClient(app)
        state = _state_from(_start(client, manager))
        with patch(
            "atlas.routes.mcp_auth_routes.time.time",
            return_value=time.time() + 10_000,
        ):
            response = self._complete(client, manager, state)
        assert "mcp_auth_error=invalid_state" in response.headers["location"]


# --- /status --------------------------------------------------------------


class TestStatusAdvertisesStartUrl:
    def test_oauth_server_carries_a_start_url(self, app, manager, storage):
        class _Storage:
            def get_user_auth_status(self, user):
                return {}

        p1, p2 = _patch_app_factory(manager)
        with p1, p2, patch(
            "atlas.routes.mcp_auth_routes.get_token_storage", return_value=_Storage()
        ):
            body = TestClient(app).get("/api/mcp/auth/status").json()

        by_name = {entry["server_name"]: entry for entry in body["servers"]}
        assert by_name[SERVER]["oauth_start_url"] == f"/api/mcp/auth/{SERVER}/oauth/start"
        # A bearer server is still connected by uploading a token.
        assert "oauth_start_url" not in by_name["bearer-server"]


# --- Service-level behaviour ---------------------------------------------


class TestRedirectUri:
    def test_built_from_the_configured_base_url(self):
        assert mcp_oauth_service.redirect_uri_for(SERVER, BASE_URL) == CALLBACK

    def test_requires_a_base_url(self):
        with pytest.raises(MCPOAuthError, match="base URL"):
            mcp_oauth_service.redirect_uri_for(SERVER, "")

    def test_rejects_a_plaintext_remote_base_url(self):
        with pytest.raises(MCPOAuthError, match="https"):
            mcp_oauth_service.redirect_uri_for(SERVER, "http://atlas.example.gov")

    def test_prefers_the_explicit_setting_over_backend_public_url(self):
        settings = type(
            "S", (), {
                "mcp_oauth_redirect_base_url": "https://explicit.example",
                "backend_public_url": "https://fallback.example",
            },
        )()
        assert mcp_oauth_service.resolve_base_url(settings) == "https://explicit.example"

    def test_falls_back_to_backend_public_url(self):
        settings = type(
            "S", (), {
                "mcp_oauth_redirect_base_url": None,
                "backend_public_url": "https://fallback.example",
            },
        )()
        assert mcp_oauth_service.resolve_base_url(settings) == "https://fallback.example"


class TestConfigReading:
    def test_scopes_from_a_list(self):
        assert mcp_oauth_service.configured_scopes(
            {"oauth_config": {"scopes": ["read", "write"]}}
        ) == "read write"

    def test_scopes_from_a_string(self):
        assert mcp_oauth_service.configured_scopes(
            {"oauth_config": {"scopes": "read write"}}
        ) == "read write"

    def test_scopes_absent(self):
        assert mcp_oauth_service.configured_scopes({}) is None

    def test_client_name_defaults(self):
        assert mcp_oauth_service.client_name({}) == "Atlas UI"
        assert mcp_oauth_service.client_name(
            {"oauth_config": {"client_name": "Custom"}}
        ) == "Custom"

    def test_is_oauth_server(self):
        assert mcp_oauth_service.is_oauth_server({"auth_type": "oauth"}) is True
        assert mcp_oauth_service.is_oauth_server({"auth_type": "bearer"}) is False
        assert mcp_oauth_service.is_oauth_server({}) is False


class TestPreRegisteredClient:
    @pytest.mark.asyncio
    async def test_configured_client_id_skips_dynamic_registration(self):
        config = {
            "url": "https://mcp.example.com/mcp",
            "auth_type": "oauth",
            "oauth_config": {"client_id": "preset", "client_secret": "s"},
        }
        with patch.object(
            mcp_oauth_service, "register_client",
            AsyncMock(side_effect=AssertionError("must not register")),
        ):
            client = await mcp_oauth_service._resolve_client(
                SERVER, config, _metadata(), CALLBACK
            )
        assert client.client_id == "preset"
        assert client.client_secret == "s"


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_updates_the_stored_token(self, storage):
        storage.store_token(
            user_email=USER, server_name=SERVER, token_value="old",
            token_type="oauth_access", expires_at=time.time() - 10,
            refresh_token="rt-1", scopes="read",
        )
        f1, f2 = _patch_flow()
        with f1, f2, patch.object(
            mcp_oauth_service, "_base_url_from_settings", return_value=BASE_URL
        ), patch.object(
            mcp_oauth_service, "refresh_access_token",
            AsyncMock(
                return_value=TokenResponse(
                    access_token="new", refresh_token="rt-2",
                    expires_at=time.time() + 3600, scopes="read",
                )
            ),
        ):
            refreshed = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, SERVERS_CONFIG[SERVER]
            )
        assert refreshed.token_value == "new"
        assert storage.get_token(USER, SERVER).refresh_token == "rt-2"

    @pytest.mark.asyncio
    async def test_no_refresh_token_returns_none(self, storage):
        storage.store_token(
            user_email=USER, server_name=SERVER, token_value="old",
            token_type="oauth_access", expires_at=time.time() - 10,
        )
        assert await mcp_oauth_service.refresh_stored_token(
            USER, SERVER, SERVERS_CONFIG[SERVER]
        ) is None

    @pytest.mark.asyncio
    async def test_nothing_stored_returns_none(self, storage):
        assert await mcp_oauth_service.refresh_stored_token(
            USER, SERVER, SERVERS_CONFIG[SERVER]
        ) is None

    @pytest.mark.asyncio
    async def test_provider_refusal_returns_none_rather_than_raising(self, storage):
        """A dead refresh token must surface as "re-authorize", not a tool error."""
        storage.store_token(
            user_email=USER, server_name=SERVER, token_value="old",
            token_type="oauth_access", expires_at=time.time() - 10,
            refresh_token="rt-1",
        )
        f1, f2 = _patch_flow()
        with f1, f2, patch.object(
            mcp_oauth_service, "_base_url_from_settings", return_value=BASE_URL
        ), patch.object(
            mcp_oauth_service, "refresh_access_token",
            AsyncMock(side_effect=MCPOAuthError("invalid_grant")),
        ):
            assert await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, SERVERS_CONFIG[SERVER]
            ) is None


# --- Refresh integration in the client factory ---------------------------


class TestClientFactoryRefresh:
    """An expired oauth token is renewed before the user is asked to re-auth."""

    def _manager(self):
        import asyncio
        from unittest.mock import MagicMock

        from atlas.modules.mcp_tools.client import MCPToolManager

        manager = MCPToolManager.__new__(MCPToolManager)
        manager.servers_config = {SERVER: dict(SERVERS_CONFIG[SERVER])}
        manager._user_clients = {}
        manager._user_clients_lock = asyncio.Lock()
        manager._create_log_handler = MagicMock(return_value=None)
        manager._create_elicitation_handler = MagicMock(return_value=None)
        manager._create_sampling_handler = MagicMock(return_value=None)
        return manager

    @pytest.mark.asyncio
    async def test_refresh_is_attempted_for_an_oauth_server(self):
        manager = self._manager()
        refreshed = StoredToken(
            token_type="oauth_access", token_value="new", user_email=USER,
            server_name=SERVER, created_at=time.time(),
        )
        with patch.object(
            mcp_oauth_service, "refresh_stored_token", AsyncMock(return_value=refreshed)
        ) as refresh:
            result = await manager._refresh_oauth_token(
                USER, SERVER, SERVERS_CONFIG[SERVER]
            )
        assert result is refreshed
        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_is_skipped_for_non_oauth_servers(self):
        manager = self._manager()
        with patch.object(
            mcp_oauth_service, "refresh_stored_token",
            AsyncMock(side_effect=AssertionError("must not refresh")),
        ):
            assert await manager._refresh_oauth_token(
                USER, "bearer-server", SERVERS_CONFIG["bearer-server"]
            ) is None

    @pytest.mark.asyncio
    async def test_refresh_failure_never_breaks_the_tool_call(self):
        """An unexpected error degrades to "unauthenticated", not an exception."""
        manager = self._manager()
        with patch.object(
            mcp_oauth_service, "refresh_stored_token",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            assert await manager._refresh_oauth_token(
                USER, SERVER, SERVERS_CONFIG[SERVER]
            ) is None


# --- Disconnect -----------------------------------------------------------


class TestDisconnectRevocation:
    """Disconnect revokes at the provider, but only what this flow issued."""

    def _delete(self, app, manager, stored, revoke_mock):
        class _Storage:
            def get_token(self, user, server):
                return stored

            def remove_token(self, user, server):
                return True

        p1, p2 = _patch_app_factory(manager)
        with p1, p2, patch(
            "atlas.routes.mcp_auth_routes.get_token_storage", return_value=_Storage()
        ), patch.object(mcp_oauth_service, "revoke_stored_token", revoke_mock):
            return TestClient(app).delete(f"/api/mcp/auth/{SERVER}/token")

    def _token(self, token_type):
        return StoredToken(
            token_type=token_type, token_value="at-1", user_email=USER,
            server_name=SERVER, created_at=time.time(), refresh_token="rt-1",
        )

    def test_oauth_token_is_revoked_at_the_provider(self, app, manager):
        revoke = AsyncMock(return_value=True)
        response = self._delete(app, manager, self._token("oauth_access"), revoke)
        assert response.status_code == 200
        assert response.json()["revoked_at_provider"] is True
        revoke.assert_awaited_once()
        assert (USER, SERVER) in manager.invalidated

    def test_hand_uploaded_bearer_token_is_not_sent_to_a_revocation_endpoint(
        self, app, manager
    ):
        revoke = AsyncMock(side_effect=AssertionError("must not revoke"))
        response = self._delete(app, manager, self._token("bearer"), revoke)
        assert response.status_code == 200
        assert response.json()["revoked_at_provider"] is False

    def test_provider_failure_still_disconnects_locally(self, app, manager):
        revoke = AsyncMock(side_effect=MCPOAuthError("provider down"))
        response = self._delete(app, manager, self._token("oauth_access"), revoke)
        assert response.status_code == 200
        assert response.json()["revoked_at_provider"] is False


class TestRevocationUsesExistingCredentialsOnly:
    """Revoking must never create a fresh registration at the provider."""

    @pytest.mark.asyncio
    async def test_no_registration_means_no_revocation_attempt(self):
        stored = StoredToken(
            token_type="oauth_access", token_value="at-1", user_email=USER,
            server_name=SERVER, created_at=time.time(), refresh_token="rt-1",
        )

        class _EmptyStore:
            def get(self, server_name, issuer):
                return None

        with patch.object(
            mcp_oauth_service, "get_server_oauth_metadata",
            AsyncMock(return_value=_metadata()),
        ), patch.object(
            mcp_oauth_service, "_base_url_from_settings", return_value=BASE_URL
        ), patch.object(
            mcp_oauth_service, "get_oauth_client_store", return_value=_EmptyStore()
        ), patch.object(
            mcp_oauth_service, "register_client",
            AsyncMock(side_effect=AssertionError("must not register to revoke")),
        ), patch.object(
            mcp_oauth_service, "revoke_token",
            AsyncMock(side_effect=AssertionError("must not call revoke")),
        ):
            assert await mcp_oauth_service.revoke_stored_token(
                SERVER, SERVERS_CONFIG[SERVER], stored
            ) is False

    @pytest.mark.asyncio
    async def test_stored_registration_is_used(self):
        stored = StoredToken(
            token_type="oauth_access", token_value="at-1", user_email=USER,
            server_name=SERVER, created_at=time.time(), refresh_token="rt-1",
        )
        registered = RegisteredClient(client_id="c1", issuer=ISSUER, redirect_uri=CALLBACK)

        class _Store:
            def get(self, server_name, issuer):
                return registered

        revoke = AsyncMock(return_value=True)
        with patch.object(
            mcp_oauth_service, "get_server_oauth_metadata",
            AsyncMock(return_value=_metadata()),
        ), patch.object(
            mcp_oauth_service, "_base_url_from_settings", return_value=BASE_URL
        ), patch.object(
            mcp_oauth_service, "get_oauth_client_store", return_value=_Store()
        ), patch.object(mcp_oauth_service, "revoke_token", revoke):
            assert await mcp_oauth_service.revoke_stored_token(
                SERVER, SERVERS_CONFIG[SERVER], stored
            ) is True
        # Both the refresh token and the access token are offered.
        assert revoke.await_count == 2


class TestConcurrency:
    """Registration and refresh are read-modify-write cycles on shared state."""

    @pytest.mark.asyncio
    async def test_concurrent_refresh_calls_the_provider_once(self, storage):
        """A rotating provider rejects a replayed refresh token, so only one call may run."""
        import asyncio

        storage.store_token(
            user_email=USER, server_name=SERVER, token_value="old",
            token_type="oauth_access", expires_at=time.time() - 10,
            refresh_token="rt-1", scopes="read",
        )

        calls = []

        async def _refresh(**kwargs):
            calls.append(kwargs["refresh_token"])
            await asyncio.sleep(0)
            return TokenResponse(
                access_token="new", refresh_token="rt-2",
                expires_at=time.time() + 3600, scopes="read",
            )

        f1, f2 = _patch_flow()
        with f1, f2, patch.object(
            mcp_oauth_service, "_base_url_from_settings", return_value=BASE_URL
        ), patch.object(mcp_oauth_service, "refresh_access_token", _refresh):
            results = await asyncio.gather(*[
                mcp_oauth_service.refresh_stored_token(
                    USER, SERVER, SERVERS_CONFIG[SERVER]
                )
                for _ in range(5)
            ])

        assert len(calls) == 1, f"provider was called {len(calls)} times"
        # Every caller gets a usable token, not a "re-authorize" None.
        assert all(result is not None for result in results)
        assert all(result.token_value == "new" for result in results)

    @pytest.mark.asyncio
    async def test_concurrent_registration_registers_once(self):
        """Two users starting at once must not create two client registrations."""
        import asyncio

        stored = {}

        class _Store:
            def get(self, server_name, issuer):
                return stored.get((server_name, issuer))

            def put(self, server_name, client):
                stored[(server_name, client.issuer)] = client
                return client

        registrations = []

        async def _register(metadata, **kwargs):
            registrations.append(kwargs["redirect_uri"])
            await asyncio.sleep(0)
            return RegisteredClient(
                client_id=f"dcr-{len(registrations)}",
                issuer=metadata.issuer,
                redirect_uri=kwargs["redirect_uri"],
            )

        with patch.object(
            mcp_oauth_service, "get_oauth_client_store", return_value=_Store()
        ), patch.object(mcp_oauth_service, "register_client", _register):
            clients = await asyncio.gather(*[
                mcp_oauth_service._resolve_client(
                    SERVER, SERVERS_CONFIG[SERVER], _metadata(), CALLBACK
                )
                for _ in range(5)
            ])

        assert len(registrations) == 1, f"registered {len(registrations)} times"
        assert {client.client_id for client in clients} == {"dcr-1"}


class TestRefreshShortCircuit:
    @pytest.mark.asyncio
    async def test_a_still_valid_token_is_returned_without_calling_the_provider(
        self, storage
    ):
        """The refresh path is reached only when the stored token is unusable."""
        storage.store_token(
            user_email=USER, server_name=SERVER, token_value="still-good",
            token_type="oauth_access", expires_at=time.time() + 3600,
            refresh_token="rt-1",
        )
        with patch.object(
            mcp_oauth_service, "refresh_access_token",
            AsyncMock(side_effect=AssertionError("must not refresh a valid token")),
        ):
            result = await mcp_oauth_service.refresh_stored_token(
                USER, SERVER, SERVERS_CONFIG[SERVER]
            )
        assert result.token_value == "still-good"


class TestRedirectEncoding:
    """A server name is operator-supplied and must not split the query string."""

    def test_server_name_is_encoded_in_the_outcome_redirect(self, app, manager, storage):
        weird = "odd&name=x"
        manager.servers_config = dict(SERVERS_CONFIG)
        manager.servers_config[weird] = {
            "url": "https://mcp.example.com/mcp", "auth_type": "oauth", "groups": ["users"],
        }
        manager._authorized.append(weird)

        p1, p2 = _patch_app_factory(manager)
        with p1, p2:
            response = TestClient(app).get(
                f"/api/mcp/auth/{weird}/oauth/callback",
                params={"error": "access_denied"},
                follow_redirects=False,
            )

        location = response.headers["location"]
        from urllib.parse import parse_qs, urlsplit

        params = parse_qs(urlsplit(location).query)
        assert params["mcp_auth_server"] == [weird]
        assert params["mcp_auth_error"] == ["access_denied"]
