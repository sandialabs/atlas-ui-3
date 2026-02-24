"""Unit tests for Globus OAuth authentication.

Tests for:
- globus_auth.py helper functions (token extraction, storage, scope building)
- globus_auth_routes.py API endpoints (status, token removal)
- LiteLLM caller Globus token resolution

Updated: 2026-02-24
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.core.globus_auth import (
    build_authorize_url,
    build_scopes,
    extract_scope_tokens,
    generate_oauth_state,
    get_globus_auth_status,
    remove_globus_tokens,
    store_globus_tokens,
)
from atlas.core.log_sanitizer import get_current_user
from atlas.routes.globus_auth_routes import api_router

# -- Helper function tests --


class TestBuildScopes:
    """Test scope string construction."""

    def test_base_scopes_only(self):
        result = build_scopes("")
        assert result == "openid profile email"

    def test_base_scopes_with_extra(self):
        alcf_scope = "https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all"
        result = build_scopes(alcf_scope)
        assert result.startswith("openid profile email ")
        assert alcf_scope in result

    def test_no_duplicate_base_scopes(self):
        result = build_scopes("openid profile email")
        assert result == "openid profile email"

    def test_whitespace_handling(self):
        result = build_scopes("  ")
        assert result == "openid profile email"


class TestBuildAuthorizeUrl:
    """Test OAuth authorize URL construction."""

    def test_contains_required_params(self):
        url = build_authorize_url(
            client_id="test-client-id",
            redirect_uri="http://localhost:8000/auth/globus/callback",
            scopes="openid profile email",
            state="random-state-value",
        )
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url
        assert "state=random-state-value" in url
        assert "/v2/oauth2/authorize" in url or "/authorize" in url

    def test_redirect_uri_encoded(self):
        url = build_authorize_url(
            client_id="cid",
            redirect_uri="http://localhost:8000/auth/globus/callback",
            scopes="openid",
            state="s",
        )
        assert "redirect_uri=" in url


class TestGenerateOauthState:
    """Test CSRF state generation."""

    def test_returns_string(self):
        state = generate_oauth_state()
        assert isinstance(state, str)
        assert len(state) > 20

    def test_unique_each_call(self):
        s1 = generate_oauth_state()
        s2 = generate_oauth_state()
        assert s1 != s2


class TestExtractScopeTokens:
    """Test extraction of service-specific tokens from Globus response."""

    def test_extracts_other_tokens(self):
        token_data = {
            "access_token": "main-token",
            "other_tokens": [
                {
                    "resource_server": "681c10cc-f684-4540-bcd7-0b4df3bc26ef",
                    "access_token": "alcf-token",
                    "expires_in": 3600,
                    "scope": "https://auth.globus.org/scopes/681c10cc/action_all",
                    "token_type": "Bearer",
                },
            ],
        }
        result = extract_scope_tokens(token_data)
        assert len(result) == 1
        assert result[0]["resource_server"] == "681c10cc-f684-4540-bcd7-0b4df3bc26ef"
        assert result[0]["access_token"] == "alcf-token"

    def test_empty_other_tokens(self):
        assert extract_scope_tokens({"access_token": "t"}) == []

    def test_non_list_other_tokens(self):
        assert extract_scope_tokens({"other_tokens": "invalid"}) == []

    def test_multiple_scope_tokens(self):
        token_data = {
            "other_tokens": [
                {"resource_server": "rs1", "access_token": "t1"},
                {"resource_server": "rs2", "access_token": "t2"},
            ],
        }
        result = extract_scope_tokens(token_data)
        assert len(result) == 2


class TestStoreGlobusTokens:
    """Test storing Globus tokens in MCPTokenStorage."""

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_stores_main_and_other_tokens(self, mock_get_storage):
        mock_storage = MagicMock()
        mock_get_storage.return_value = mock_storage

        token_data = {
            "access_token": "main-token",
            "resource_server": "auth.globus.org",
            "expires_in": 3600,
            "scope": "openid profile email",
            "refresh_token": "refresh-123",
            "other_tokens": [
                {
                    "resource_server": "alcf-uuid",
                    "access_token": "alcf-token",
                    "expires_in": 7200,
                    "scope": "action_all",
                    "token_type": "Bearer",
                },
            ],
        }

        count = store_globus_tokens("user@example.com", token_data)
        assert count == 2
        assert mock_storage.store_token.call_count == 2

        # Check main token call
        main_call = mock_storage.store_token.call_args_list[0]
        assert main_call.kwargs["user_email"] == "user@example.com"
        assert main_call.kwargs["server_name"] == "globus:auth.globus.org"
        assert main_call.kwargs["token_value"] == "main-token"
        assert main_call.kwargs["token_type"] == "oauth_access"

        # Check ALCF token call
        alcf_call = mock_storage.store_token.call_args_list[1]
        assert alcf_call.kwargs["server_name"] == "globus:alcf-uuid"
        assert alcf_call.kwargs["token_value"] == "alcf-token"

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_skips_entries_without_access_token(self, mock_get_storage):
        mock_storage = MagicMock()
        mock_get_storage.return_value = mock_storage

        token_data = {
            "other_tokens": [
                {"resource_server": "rs1"},  # Missing access_token
            ],
        }

        count = store_globus_tokens("user@example.com", token_data)
        assert count == 0


class TestRemoveGlobusTokens:
    """Test removal of Globus tokens."""

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_removes_globus_prefixed_tokens(self, mock_get_storage):
        mock_storage = MagicMock()
        mock_storage._lock = MagicMock()
        mock_storage._tokens = {
            "user@example.com:globus:auth.globus.org": MagicMock(),
            "user@example.com:globus:alcf-uuid": MagicMock(),
            "user@example.com:llm:gpt-4": MagicMock(),  # Should NOT be removed
        }
        mock_get_storage.return_value = mock_storage

        count = remove_globus_tokens("user@example.com")
        assert count == 2
        assert "user@example.com:llm:gpt-4" in mock_storage._tokens


class TestGetGlobusAuthStatus:
    """Test Globus auth status retrieval."""

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_returns_status_with_resource_servers(self, mock_get_storage):
        mock_storage = MagicMock()
        mock_storage._lock = MagicMock()

        mock_token = MagicMock()
        mock_token.server_name = "globus:alcf-uuid"
        mock_token.is_expired.return_value = False
        mock_token.expires_at = time.time() + 3600
        mock_token.scopes = "action_all"

        mock_storage._tokens = {
            "user@example.com:globus:alcf-uuid": mock_token,
        }
        mock_get_storage.return_value = mock_storage

        status = get_globus_auth_status("user@example.com")
        assert status["authenticated"] is True
        assert len(status["resource_servers"]) == 1
        assert status["resource_servers"][0]["resource_server"] == "alcf-uuid"

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_unauthenticated_when_no_tokens(self, mock_get_storage):
        mock_storage = MagicMock()
        mock_storage._lock = MagicMock()
        mock_storage._tokens = {}
        mock_get_storage.return_value = mock_storage

        status = get_globus_auth_status("user@example.com")
        assert status["authenticated"] is False
        assert len(status["resource_servers"]) == 0


# -- API route tests --


def create_test_app(user_override: str = "test@example.com"):
    """Create a FastAPI test app with Globus auth routes."""
    app = FastAPI()
    app.include_router(api_router)

    async def override_get_current_user():
        return user_override

    app.dependency_overrides[get_current_user] = override_get_current_user
    return app


class TestGlobusStatusRoute:
    """Test GET /api/globus/status endpoint."""

    @pytest.fixture
    def client(self):
        app = create_test_app()
        return TestClient(app)

    @patch("atlas.routes.globus_auth_routes.get_globus_auth_status")
    @patch("atlas.routes.globus_auth_routes.app_factory")
    def test_returns_disabled_when_feature_off(self, mock_factory, mock_status, client):
        mock_settings = MagicMock()
        mock_settings.feature_globus_auth_enabled = False
        mock_cm = MagicMock()
        mock_cm.app_settings = mock_settings
        mock_factory.get_config_manager.return_value = mock_cm

        response = client.get("/api/globus/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["authenticated"] is False

    @patch("atlas.routes.globus_auth_routes.get_globus_auth_status")
    @patch("atlas.routes.globus_auth_routes.app_factory")
    def test_returns_status_when_enabled(self, mock_factory, mock_status, client):
        mock_settings = MagicMock()
        mock_settings.feature_globus_auth_enabled = True
        mock_cm = MagicMock()
        mock_cm.app_settings = mock_settings
        mock_factory.get_config_manager.return_value = mock_cm

        mock_status.return_value = {
            "authenticated": True,
            "resource_servers": [
                {"resource_server": "alcf-uuid", "is_expired": False}
            ],
            "user_email": "test@example.com",
        }

        response = client.get("/api/globus/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["authenticated"] is True
        assert len(data["resource_servers"]) == 1


class TestGlobusRemoveTokensRoute:
    """Test DELETE /api/globus/tokens endpoint."""

    @pytest.fixture
    def client(self):
        app = create_test_app()
        return TestClient(app)

    @patch("atlas.routes.globus_auth_routes.remove_globus_tokens")
    def test_removes_tokens(self, mock_remove, client):
        mock_remove.return_value = 3

        response = client.delete("/api/globus/tokens")
        assert response.status_code == 200
        data = response.json()
        assert data["removed_count"] == 3
        mock_remove.assert_called_once_with("test@example.com")


# -- LLM caller Globus key resolution tests --


class TestLiteLLMCallerGlobusKey:
    """Test _resolve_globus_api_key in LiteLLMCaller."""

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_resolve_valid_globus_key(self, mock_get_storage):
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        mock_storage = MagicMock()
        mock_token = MagicMock()
        mock_token.token_value = "globus-alcf-token"
        mock_storage.get_valid_token.return_value = mock_token
        mock_get_storage.return_value = mock_storage

        result = LiteLLMCaller._resolve_globus_api_key(
            model_name="alcf-model",
            globus_scope="alcf-uuid",
            user_email="user@example.com",
        )
        assert result == "globus-alcf-token"
        mock_storage.get_valid_token.assert_called_once_with(
            "user@example.com", "globus:alcf-uuid"
        )

    def test_raises_when_no_user_email(self):
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        with pytest.raises(ValueError, match="requires Globus authentication"):
            LiteLLMCaller._resolve_globus_api_key(
                model_name="alcf-model",
                globus_scope="alcf-uuid",
                user_email=None,
            )

    def test_raises_when_no_globus_scope(self):
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        with pytest.raises(ValueError, match="no globus_scope configured"):
            LiteLLMCaller._resolve_globus_api_key(
                model_name="alcf-model",
                globus_scope=None,
                user_email="user@example.com",
            )

    @patch("atlas.modules.mcp_tools.token_storage.get_token_storage")
    def test_raises_when_no_token_stored(self, mock_get_storage):
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        mock_storage = MagicMock()
        mock_storage.get_valid_token.return_value = None
        mock_get_storage.return_value = mock_storage

        with pytest.raises(ValueError, match="Please log in via Globus"):
            LiteLLMCaller._resolve_globus_api_key(
                model_name="alcf-model",
                globus_scope="alcf-uuid",
                user_email="user@example.com",
            )
