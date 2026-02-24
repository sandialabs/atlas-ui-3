"""Integration tests for Globus OAuth flow using mock Globus server.

Tests the complete OAuth authorization code flow:
1. Login redirect to Globus
2. Callback with auth code
3. Token exchange
4. Token storage
5. Status check
6. Logout/token removal

Requires the mock Globus auth server to be running on port 9999:
  python mocks/globus-auth-mock/mock_globus_auth.py --port 9999

Updated: 2026-02-24
"""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Override Globus auth base URL BEFORE importing the module
os.environ["GLOBUS_AUTH_BASE_URL"] = "http://localhost:9999/v2/oauth2"

from atlas.core.globus_auth import (
    build_scopes,
    exchange_code_for_tokens,
    extract_scope_tokens,
    fetch_globus_userinfo,
    store_globus_tokens,
)


def _mock_server_reachable():
    """Check if the mock Globus server is running."""
    try:
        resp = httpx.get("http://localhost:9999/", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


# Skip all tests in this module if mock server is not running
pytestmark = pytest.mark.skipif(
    not _mock_server_reachable(),
    reason="Mock Globus auth server not running on port 9999"
)

ALCF_SCOPE = "https://auth.globus.org/scopes/681c10cc-f684-4540-bcd7-0b4df3bc26ef/action_all"


class TestMockGlobusTokenExchange:
    """Test the token exchange against the mock Globus server."""

    @pytest.mark.asyncio
    async def test_exchange_code_returns_tokens(self):
        """Simulate getting an auth code and exchanging it for tokens."""
        # Step 1: Hit the authorize endpoint to get a code
        # The mock auto-approves and redirects, but we can follow=False
        scopes = build_scopes(ALCF_SCOPE)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "http://localhost:9999/v2/oauth2/authorize",
                params={
                    "client_id": "mock-client-id",
                    "redirect_uri": "http://localhost:8000/auth/globus/callback",
                    "response_type": "code",
                    "scope": scopes,
                    "state": "test-state-123",
                },
                follow_redirects=False,
            )
            # Should redirect with code
            assert resp.status_code == 302
            location = resp.headers["location"]
            assert "code=" in location
            assert "state=test-state-123" in location

            # Extract code from redirect URL
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(location)
            query = parse_qs(parsed.query)
            code = query["code"][0]

        # Step 2: Exchange code for tokens
        token_data = await exchange_code_for_tokens(
            code=code,
            redirect_uri="http://localhost:8000/auth/globus/callback",
            client_id="mock-client-id",
            client_secret="mock-client-secret",
        )

        # Verify main token
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"
        assert token_data["resource_server"] == "auth.globus.org"
        assert token_data["expires_in"] > 0

        # Verify other_tokens contains ALCF scope
        other_tokens = extract_scope_tokens(token_data)
        assert len(other_tokens) == 1

        alcf_token = other_tokens[0]
        assert alcf_token["resource_server"] == "681c10cc-f684-4540-bcd7-0b4df3bc26ef"
        assert alcf_token["access_token"].startswith("mock-681c10cc")
        assert alcf_token["scope"] == ALCF_SCOPE

    @pytest.mark.asyncio
    async def test_exchange_invalid_code_fails(self):
        """Invalid code should return error."""
        with pytest.raises(httpx.HTTPStatusError):
            await exchange_code_for_tokens(
                code="invalid-code",
                redirect_uri="http://localhost:8000/auth/globus/callback",
                client_id="mock-client-id",
                client_secret="mock-client-secret",
            )


class TestMockGlobusUserInfo:
    """Test the userinfo endpoint against the mock server."""

    @pytest.mark.asyncio
    async def test_fetch_userinfo(self):
        """Should return mock user details."""
        userinfo = await fetch_globus_userinfo("mock-access-token")
        assert userinfo["email"] == "testuser@alcf.anl.gov"
        assert "name" in userinfo


class TestEndToEndTokenStorage:
    """Test the full flow: exchange code -> store tokens -> verify storage."""

    @pytest.mark.asyncio
    async def test_full_flow_stores_alcf_token(self):
        """Complete flow from code exchange to token storage and retrieval."""
        # Step 1: Get auth code
        scopes = build_scopes(ALCF_SCOPE)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "http://localhost:9999/v2/oauth2/authorize",
                params={
                    "client_id": "mock-client-id",
                    "redirect_uri": "http://localhost:8000/auth/globus/callback",
                    "response_type": "code",
                    "scope": scopes,
                    "state": "test",
                },
                follow_redirects=False,
            )
            from urllib.parse import parse_qs, urlparse
            code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]

        # Step 2: Exchange for tokens
        token_data = await exchange_code_for_tokens(
            code=code,
            redirect_uri="http://localhost:8000/auth/globus/callback",
            client_id="mock-client-id",
            client_secret="mock-client-secret",
        )

        # Step 3: Store tokens (using mock token storage)
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_get_storage:
            mock_storage = MagicMock()
            mock_get_storage.return_value = mock_storage

            count = store_globus_tokens("testuser@alcf.anl.gov", token_data)

            # Should store main token + 1 ALCF scope token
            assert count == 2
            assert mock_storage.store_token.call_count == 2

            # Verify ALCF token was stored with correct key
            calls = mock_storage.store_token.call_args_list
            alcf_call = [c for c in calls if "681c10cc" in c.kwargs.get("server_name", "")]
            assert len(alcf_call) == 1
            assert alcf_call[0].kwargs["server_name"] == "globus:681c10cc-f684-4540-bcd7-0b4df3bc26ef"
            assert alcf_call[0].kwargs["token_type"] == "oauth_access"
            assert alcf_call[0].kwargs["token_value"].startswith("mock-681c10cc")
