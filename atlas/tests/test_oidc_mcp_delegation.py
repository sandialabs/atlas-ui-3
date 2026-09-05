"""Tests for delegated downstream credentials on MCP servers.

The central guarantee here is negative: Atlas must never hand an MCP server
the user's own inbound token, and every failure to obtain a delegated one must
leave the caller on its existing "not authenticated" path rather than raising
into a tool call.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from atlas.core.oidc.delegation import DelegatedToken, DelegationError
from atlas.core.oidc.mcp_delegation import (
    is_delegated_server,
    mint_delegated_token_for_server,
)
from atlas.core.oidc.session import get_session_store

DELEGATED_CONFIG = {
    "url": "https://tools.example.gov/mcp",
    "auth_type": "delegated",
    "delegation": {"audience": "api://tools", "scope": "tools.read"},
}


@pytest.fixture
def user_session():
    store = get_session_store()
    store.clear()
    store.create(user_id="user@example.gov", access_token="USER-INBOUND-TOKEN")
    yield store
    store.clear()


class _StubManager:
    def __init__(self, token=None, error=None):
        self.token = token
        self.error = error
        self.requests = []

    async def get_token(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.token


def _patch_manager(manager):
    return patch(
        "atlas.core.oidc.mcp_delegation.get_delegation_manager_async",
        AsyncMock(return_value=manager),
    )


class TestIsDelegatedServer:
    def test_true_for_delegated_auth_type(self):
        assert is_delegated_server(DELEGATED_CONFIG)

    def test_false_for_other_auth_types(self):
        assert not is_delegated_server({"auth_type": "bearer"})
        assert not is_delegated_server({})
        assert not is_delegated_server(None)


class TestMintDelegatedToken:
    @pytest.mark.asyncio
    async def test_exchanges_the_session_token_for_an_audience_bound_one(self, user_session):
        manager = _StubManager(
            DelegatedToken(access_token="DOWNSTREAM", expires_at=time.time() + 300)
        )
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "user@example.gov", "tools", DELEGATED_CONFIG
            )

        assert token.access_token == "DOWNSTREAM"
        request = manager.requests[0]
        assert request.subject_token == "USER-INBOUND-TOKEN"
        assert request.audience == "api://tools"
        assert request.scope == "tools.read"
        assert request.actor == "mcp:tools"
        # The user's own token is exchanged, never returned for forwarding.
        assert token.access_token != "USER-INBOUND-TOKEN"

    @pytest.mark.asyncio
    async def test_audience_defaults_to_the_server_url(self, user_session):
        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        config = {"url": "https://tools.example.gov/mcp", "auth_type": "delegated"}
        with _patch_manager(manager):
            await mint_delegated_token_for_server("user@example.gov", "tools", config)
        assert manager.requests[0].audience == "https://tools.example.gov/mcp"

    @pytest.mark.asyncio
    async def test_non_delegated_server_is_skipped(self, user_session):
        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "user@example.gov", "tools", {"auth_type": "bearer"}
            )
        assert token is None
        assert manager.requests == []

    @pytest.mark.asyncio
    async def test_none_when_delegation_is_not_configured(self, user_session):
        with _patch_manager(None):
            token = await mint_delegated_token_for_server(
                "user@example.gov", "tools", DELEGATED_CONFIG
            )
        assert token is None

    @pytest.mark.asyncio
    async def test_none_when_the_user_has_no_oidc_session(self):
        get_session_store().clear()
        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "stranger@example.gov", "tools", DELEGATED_CONFIG
            )
        assert token is None
        assert manager.requests == []

    @pytest.mark.asyncio
    async def test_a_failed_exchange_degrades_instead_of_raising(self, user_session):
        manager = _StubManager(error=DelegationError("invalid_grant"))
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "user@example.gov", "tools", DELEGATED_CONFIG
            )
        assert token is None

    @pytest.mark.asyncio
    async def test_session_lookup_is_case_insensitive(self, user_session):
        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "USER@EXAMPLE.GOV", "tools", DELEGATED_CONFIG
            )
        assert token is not None


class TestLoggableServerName:
    """Names must not be able to forge extra log records."""

    def test_plain_name_passes_through(self):
        from atlas.core.oidc.mcp_delegation import loggable_server_name

        assert loggable_server_name("protected-tools") == "protected-tools"

    def test_line_breaks_are_removed(self):
        from atlas.core.oidc.mcp_delegation import loggable_server_name

        result = loggable_server_name("tools\r\nERROR forged record")
        assert "\n" not in result and "\r" not in result

    def test_out_of_allowlist_names_are_replaced(self):
        from atlas.core.oidc.mcp_delegation import loggable_server_name

        assert loggable_server_name("tools <script>") == "<invalid-name>"
        assert loggable_server_name("") == "<invalid-name>"
        assert loggable_server_name("a" * 200) == "<invalid-name>"


class TestSubjectTokenRefresh:
    """An Atlas session outlives the IdP access token; delegation must notice."""

    @pytest.mark.asyncio
    async def test_expiring_token_is_refreshed_before_the_exchange(self, user_session):
        store = get_session_store()
        session = store.iter_sessions()[0]
        session.access_token_expires_at = time.time() - 1

        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager), patch(
            "atlas.core.oidc.session_refresh.ensure_fresh_access_token",
            AsyncMock(return_value="REFRESHED-TOKEN"),
        ):
            await mint_delegated_token_for_server(
                "user@example.gov", "tools", DELEGATED_CONFIG
            )

        assert manager.requests[0].subject_token == "REFRESHED-TOKEN"

    @pytest.mark.asyncio
    async def test_a_dead_unrefreshable_token_is_not_presented_downstream(self, user_session):
        store = get_session_store()
        session = store.iter_sessions()[0]
        session.access_token_expires_at = time.time() - 1
        session.refresh_token = None

        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager):
            token = await mint_delegated_token_for_server(
                "user@example.gov", "tools", DELEGATED_CONFIG
            )

        assert token is None
        assert manager.requests == []


class TestRevokeDelegatedCredentials:
    """Revocation must reach every place a delegated credential is held."""

    @pytest.mark.asyncio
    async def test_stored_delegated_tokens_are_removed(self, user_session):
        from atlas.core.oidc.mcp_delegation import revoke_delegated_credentials
        from atlas.modules.mcp_tools.token_storage import get_token_storage

        storage = get_token_storage()
        storage.store_token(
            user_email="user@example.gov",
            server_name="tools",
            token_value="DOWNSTREAM",
            token_type="oauth_access",
            expires_at=time.time() + 300,
            metadata={"source": "delegation"},
        )
        # A token the user uploaded themselves must survive.
        storage.store_token(
            user_email="user@example.gov",
            server_name="manual",
            token_value="USER-UPLOADED",
            token_type="bearer",
        )
        try:
            removed = await revoke_delegated_credentials("user@example.gov")
            assert removed == 1
            assert storage.get_token("user@example.gov", "tools") is None
            assert storage.get_token("user@example.gov", "manual") is not None
        finally:
            storage.remove_token("user@example.gov", "manual")
            storage.remove_token("user@example.gov", "tools")


class TestDelegationConfigSurvivesModelDump:
    """The documented `delegation` block must reach the exchange."""

    def test_delegation_is_a_declared_field(self):
        from atlas.modules.config.models import MCPServerConfig

        config = MCPServerConfig(**{
            "url": "https://tools.example.gov/mcp",
            "auth_type": "delegated",
            "delegation": {"audience": "api://tools", "scope": "tools.read"},
        })
        dumped = config.model_dump()
        assert dumped["delegation"]["audience"] == "api://tools"
        assert dumped["delegation"]["scope"] == "tools.read"

    @pytest.mark.asyncio
    async def test_a_dumped_config_still_carries_audience_and_scope(self, user_session):
        from atlas.modules.config.models import MCPServerConfig

        config = MCPServerConfig(**{
            "url": "https://tools.example.gov/mcp",
            "auth_type": "delegated",
            "delegation": {"audience": "api://tools", "scope": "tools.read"},
        }).model_dump()

        manager = _StubManager(DelegatedToken(access_token="DOWNSTREAM"))
        with _patch_manager(manager):
            await mint_delegated_token_for_server("user@example.gov", "tools", config)

        assert manager.requests[0].audience == "api://tools"
        assert manager.requests[0].scope == "tools.read"
