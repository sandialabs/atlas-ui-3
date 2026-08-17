"""Unit tests for UnifiedRAGService.

Tests the unified RAG service that aggregates HTTP and MCP RAG sources.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.domain.errors import DataSourcePermissionError
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.config_manager import RAGSourceConfig, RAGSourcesConfig, config_manager
from atlas.modules.rag.client import DataSource, RAGResponse


@pytest.fixture
def mock_config_manager(distinct_admin_group):
    """Create a mock config manager with test RAG sources.

    ``distinct_admin_group`` supplies the admin-only source's group and skips
    when it is one the non-admin ``test_user`` already holds -- otherwise the
    paired denial tests below (test_user must not reach ``test_mcp``) invert.
    """
    # Named ``mock_cm`` rather than ``config_manager`` so the real singleton
    # imported at module scope stays reachable inside this fixture -- the
    # admin-only source below must be tagged with the *configured* admin group,
    # not a hardcoded "admin".
    mock_cm = MagicMock()

    # Create test RAG sources config
    http_source = RAGSourceConfig(
        type="http",
        display_name="Test HTTP RAG",
        description="Test HTTP RAG source",
        url="http://test-rag.example.com",
        bearer_token="test-token",
        groups=["users"],
        compliance_level="Internal",
        enabled=True,
    )

    mcp_source = RAGSourceConfig(
        type="mcp",
        display_name="Test MCP RAG",
        description="Test MCP RAG source",
        command=["python", "test_mcp.py"],
        groups=[distinct_admin_group],
        compliance_level="SOC2",
        enabled=True,
    )

    disabled_source = RAGSourceConfig(
        type="http",
        display_name="Disabled RAG",
        url="http://disabled.example.com",
        enabled=False,
    )

    mock_cm.rag_sources_config = RAGSourcesConfig(
        sources={
            "test_http": http_source,
            "test_mcp": mcp_source,
            "disabled": disabled_source,
        }
    )

    return mock_cm


@pytest.fixture
def mock_auth_check():
    """Create a mock auth check function."""
    async def auth_check(username: str, group: str) -> bool:
        # The configured test user is in the "users" group only
        if username == config_manager.app_settings.test_user:
            return group == "users"
        # The configured admin test user is in both "users" and the configured
        # admin group
        if username == config_manager.app_settings.admin_test_user:
            return group in ["users", config_manager.app_settings.admin_group]
        return False

    return auth_check


@pytest.fixture
def unified_rag_service(mock_config_manager, mock_auth_check):
    """Create a UnifiedRAGService instance for testing."""
    return UnifiedRAGService(
        config_manager=mock_config_manager,
        mcp_manager=None,
        auth_check_func=mock_auth_check,
    )


class TestUnifiedRAGServiceInit:
    """Tests for UnifiedRAGService initialization."""

    def test_init_with_all_params(self, mock_config_manager, mock_auth_check):
        """Test initialization with all parameters."""
        service = UnifiedRAGService(
            config_manager=mock_config_manager,
            mcp_manager=MagicMock(),
            auth_check_func=mock_auth_check,
        )

        assert service.config_manager == mock_config_manager
        assert service.auth_check_func == mock_auth_check
        assert service._http_clients == {}

    def test_init_without_optional_params(self, mock_config_manager):
        """Test initialization without optional parameters."""
        service = UnifiedRAGService(config_manager=mock_config_manager)

        assert service.mcp_manager is None
        assert service.auth_check_func is None


class TestHTTPClientCaching:
    """Tests for HTTP client caching logic."""

    def test_get_http_client_creates_new_client(self, unified_rag_service, mock_config_manager):
        """Test that _get_http_client creates a new client when not cached."""
        source_config = mock_config_manager.rag_sources_config.sources["test_http"]

        with patch("atlas.domain.unified_rag_service.resolve_env_var", side_effect=lambda v, **kw: v):
            client = unified_rag_service._get_http_client("test_http", source_config)

        assert client is not None
        assert "test_http" in unified_rag_service._http_clients
        assert unified_rag_service._http_clients["test_http"] == client

    def test_get_http_client_returns_cached_client(self, unified_rag_service, mock_config_manager):
        """Test that _get_http_client returns cached client on second call."""
        source_config = mock_config_manager.rag_sources_config.sources["test_http"]

        with patch("atlas.domain.unified_rag_service.resolve_env_var", side_effect=lambda v, **kw: v):
            client1 = unified_rag_service._get_http_client("test_http", source_config)
            client2 = unified_rag_service._get_http_client("test_http", source_config)

        assert client1 is client2


class TestUserAuthorization:
    """Tests for user authorization logic."""

    @pytest.mark.asyncio
    async def test_is_user_authorized_no_groups(self, unified_rag_service):
        """Test authorization when no groups are required."""
        result = await unified_rag_service._is_user_authorized("anyone@test.com", [])
        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_authorized_user_in_group(self, unified_rag_service):
        """Test authorization when user is in required group."""
        result = await unified_rag_service._is_user_authorized(config_manager.app_settings.test_user, ["users"])
        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_authorized_user_not_in_group(self, unified_rag_service):
        """Test authorization when user is not in required group."""
        result = await unified_rag_service._is_user_authorized(
            config_manager.app_settings.test_user,
            [config_manager.app_settings.admin_group],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_is_user_authorized_no_auth_func(self, mock_config_manager):
        """Test authorization when no auth check function is provided."""
        service = UnifiedRAGService(config_manager=mock_config_manager)
        result = await service._is_user_authorized(
            "anyone@test.com", [config_manager.app_settings.admin_group]
        )
        # Should return True when no auth function (permissive by default)
        assert result is True


class TestDiscoverDataSources:
    """Tests for data source discovery."""

    @pytest.mark.asyncio
    async def test_discover_skips_disabled_sources(self, unified_rag_service):
        """Test that disabled sources are skipped during discovery."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            await unified_rag_service.discover_data_sources(config_manager.app_settings.test_user)

            # Should not be called for disabled source
            call_args = [call[0][0] for call in mock_discover.call_args_list]
            assert "disabled" not in call_args

    @pytest.mark.asyncio
    async def test_discover_filters_by_authorization(self, unified_rag_service):
        """Test that sources are filtered by user authorization."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            # The configured test user is only in "users" group, not "admin"
            await unified_rag_service.discover_data_sources(config_manager.app_settings.test_user)

            # Should only discover test_http (users group), not test_mcp (admin group)
            call_args = [call[0][0] for call in mock_discover.call_args_list]
            assert "test_http" in call_args
            # test_mcp requires admin group, which the configured test user does not have

    @pytest.mark.asyncio
    async def test_discover_includes_admin_sources_for_admin(self, unified_rag_service):
        """Test that admin user can see admin-only sources."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            # The configured admin test user is in both "users" and "admin" groups
            await unified_rag_service.discover_data_sources(config_manager.app_settings.admin_test_user)

            # Should discover test_http (users group)
            call_args = [call[0][0] for call in mock_discover.call_args_list]
            assert "test_http" in call_args


class TestDiscoverHTTPSource:
    """Tests for HTTP source discovery."""

    @pytest.mark.asyncio
    async def test_discover_http_source_success(self, unified_rag_service, mock_config_manager):
        """Test successful HTTP source discovery."""
        source_config = mock_config_manager.rag_sources_config.sources["test_http"]

        mock_client = AsyncMock()
        mock_client.discover_data_sources.return_value = [
            DataSource(id="corpus1", label="Corpus One", compliance_level="Internal", description="First corpus"),
            DataSource(id="corpus2", label="Corpus Two", compliance_level="Public", description="Second corpus"),
        ]

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            result = await unified_rag_service._discover_http_source(
                "test_http", source_config, config_manager.app_settings.test_user
            )

        assert result is not None
        assert result["server"] == "test_http"
        assert result["displayName"] == "Test HTTP RAG"
        assert len(result["sources"]) == 2
        assert result["sources"][0]["id"] == "corpus1"
        assert result["sources"][0]["name"] == "Corpus One"
        assert result["sources"][0]["label"] == "Corpus One"
        assert result["sources"][0]["description"] == "First corpus"
        assert result["sources"][1]["id"] == "corpus2"

    @pytest.mark.asyncio
    async def test_discover_http_source_empty(self, unified_rag_service, mock_config_manager):
        """Test HTTP source discovery with no data sources."""
        source_config = mock_config_manager.rag_sources_config.sources["test_http"]

        mock_client = AsyncMock()
        mock_client.discover_data_sources.return_value = []

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            result = await unified_rag_service._discover_http_source(
                "test_http", source_config, config_manager.app_settings.test_user
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_discover_http_source_error(self, unified_rag_service, mock_config_manager):
        """Test HTTP source discovery handles errors gracefully."""
        source_config = mock_config_manager.rag_sources_config.sources["test_http"]

        mock_client = AsyncMock()
        mock_client.discover_data_sources.side_effect = Exception("Connection failed")

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            result = await unified_rag_service._discover_http_source(
                "test_http", source_config, config_manager.app_settings.test_user
            )

        assert result is None


class TestQueryRAG:
    """Tests for RAG query routing."""

    @pytest.mark.asyncio
    async def test_query_rag_with_qualified_source(self, unified_rag_service, mock_config_manager):
        """Test querying RAG with qualified source (server:source_id)."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(
            content="Test response",
            metadata=None,
        )

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            result = await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "test query"}],
            )

        assert result.content == "Test response"
        mock_client.query_rag.assert_called_once_with(
            config_manager.app_settings.test_user,
            "corpus1",
            [{"role": "user", "content": "test query"}],
            data_sources=None,
        )

    @pytest.mark.asyncio
    async def test_query_rag_unknown_server(self, unified_rag_service):
        """Test querying RAG with unknown server raises error."""
        with pytest.raises(ValueError, match="RAG source not found"):
            await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="unknown_server:corpus1",
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_query_rag_mcp_source_without_service_raises(self, unified_rag_service):
        """Test querying MCP source without RAGMCPService raises ValueError."""
        # The unified_rag_service fixture has no rag_mcp_service configured
        with pytest.raises(ValueError, match="RAGMCPService not configured"):
            await unified_rag_service.query_rag(
                username=config_manager.app_settings.admin_test_user,
                qualified_data_source="test_mcp:corpus1",
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_query_rag_mcp_source_routes_to_mcp_service(self, mock_config_manager, mock_auth_check):
        """Test that MCP source queries are routed to RAGMCPService."""
        # Create a mock RAGMCPService
        mock_rag_mcp_service = MagicMock()
        mock_rag_mcp_service.synthesize = AsyncMock(return_value={
            "results": {
                "answer": "Test answer from MCP RAG",
                "citations": [],
            },
            "meta_data": {
                "providers": {
                    "test_mcp": {"used_synth": True, "error": None}
                },
                "fallback_used": False,
            },
        })

        # Create service with rag_mcp_service
        service = UnifiedRAGService(
            config_manager=mock_config_manager,
            mcp_manager=None,
            auth_check_func=mock_auth_check,
            rag_mcp_service=mock_rag_mcp_service,
        )

        messages = [{"role": "user", "content": "What is the fleet info?"}]
        result = await service.query_rag(
            username=config_manager.app_settings.admin_test_user,
            qualified_data_source="test_mcp:corpus1",
            messages=messages,
        )

        # Verify RAGMCPService.synthesize was called
        mock_rag_mcp_service.synthesize.assert_called_once_with(
            username=config_manager.app_settings.admin_test_user,
            query="What is the fleet info?",
            sources=["test_mcp:corpus1"],
        )

        # Verify response format
        assert isinstance(result, RAGResponse)
        assert result.content == "Test answer from MCP RAG"
        assert result.metadata is not None
        assert result.metadata.data_source_name == "test_mcp"
        assert result.metadata.retrieval_method == "mcp_synthesis"


class TestQueryRAGCompliance:
    """Tests for query-time compliance enforcement."""

    class _ComplianceManager:
        def is_accessible(self, user_level, resource_level):
            return user_level == resource_level

    @pytest.mark.asyncio
    async def test_query_rag_allows_matching_compliance_level(self, unified_rag_service):
        """A matching server-side compliance level should allow the query."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
        ):
            result = await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "q"}],
                enforced_compliance_level="Internal",
            )

        assert result.content == "ok"
        mock_client.query_rag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_rag_rejects_mismatched_compliance_level(self, unified_rag_service):
        """Query-time checks must reject sources hidden by discovery filtering."""
        mock_client = AsyncMock()

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
        ):
            with pytest.raises(DataSourcePermissionError, match="not accessible"):
                await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="test_http:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                    enforced_compliance_level="Public",
                )

        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_rag_batch_rejects_mismatched_compliance_level(self, unified_rag_service):
        """Batch queries must enforce compliance before contacting the backend."""
        mock_client = AsyncMock()

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
        ):
            with pytest.raises(DataSourcePermissionError, match="not accessible"):
                await unified_rag_service.query_rag_batch(
                    username=config_manager.app_settings.test_user,
                    qualified_data_sources=["test_http:corpus1", "test_http:corpus2"],
                    messages=[{"role": "user", "content": "q"}],
                    enforced_compliance_level="Public",
                )

        mock_client.query_rag.assert_not_called()


class TestQueryRAGProductionEnforcementPath:
    """Enforcement as production actually invokes it.

    Production callers never pass ``enforced_compliance_level``: ``ChatService``
    sets the ambient ContextVar once per turn and the RAG service reads it. These
    tests drive that path with no kwarg so a regression that only breaks the
    ambient read cannot hide behind the explicit-kwarg tests above.
    """

    class _ComplianceManager:
        def is_accessible(self, user_level, resource_level):
            return user_level == resource_level

    @staticmethod
    def _turn_context(level, enforce=True):
        """Set the context the way ChatService does, and undo it afterwards."""
        from contextlib import contextmanager

        from atlas.core.compliance import (
            reset_active_compliance_context,
            set_active_compliance_context,
        )

        @contextmanager
        def _ctx():
            token = set_active_compliance_context(level, enforce=enforce)
            try:
                yield
            finally:
                reset_active_compliance_context(token)

        return _ctx()

    @pytest.mark.asyncio
    async def test_ambient_context_rejects_out_of_level_source(self, unified_rag_service):
        """No kwarg: the level comes from the turn's ContextVar."""
        mock_client = AsyncMock()

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
            self._turn_context("Public"),
        ):
            with pytest.raises(DataSourcePermissionError) as excinfo:
                await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="test_http:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                )

        assert excinfo.value.code == "DATA_SOURCE_COMPLIANCE_MISMATCH"
        # The denial names the *corpus* the user selected, not the rag-sources
        # server key, which the UI never displays.
        assert "'corpus1'" in str(excinfo.value)
        assert "test_http" not in str(excinfo.value)
        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambient_context_allows_in_level_source(self, unified_rag_service):
        """The allowed case must still reach the backend with no kwarg."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
            self._turn_context("Internal"),
        ):
            result = await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "q"}],
            )

        assert result.content == "ok"
        mock_client.query_rag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enforce_with_no_level_rejects(self, unified_rag_service):
        """enforce=True with a None level is a denial, not a free pass."""
        mock_client = AsyncMock()

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            self._turn_context(None, enforce=True),
        ):
            with pytest.raises(DataSourcePermissionError) as excinfo:
                await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="test_http:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                )

        assert excinfo.value.code == "DATA_SOURCE_COMPLIANCE_MISMATCH"
        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_enforcement_leaves_tagged_sources_queryable(self, unified_rag_service):
        """enforce=False (no trusted level resolved) keeps prior behaviour."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            self._turn_context(None, enforce=False),
        ):
            result = await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "q"}],
            )

        assert result.content == "ok"
        mock_client.query_rag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_denial_raises_permission_error(self, unified_rag_service):
        """A group-authorization failure is a permission error, not a backend call."""
        mock_client = AsyncMock()

        # The configured test user is in "users" only; test_mcp requires "admin".
        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            with pytest.raises(DataSourcePermissionError) as excinfo:
                await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="test_mcp:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                )

        assert excinfo.value.code == "DATA_SOURCE_ACCESS_DENIED"
        assert "'corpus1'" in str(excinfo.value)
        assert "test_mcp" not in str(excinfo.value)
        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_source_raises_permission_error(self, unified_rag_service):
        """A disabled source must raise DataSourcePermissionError, not ValueError.

        A bare ValueError is swallowed by the generic handler in the LLM caller
        and degrades to the silent non-RAG answer this path exists to prevent.
        """
        mock_client = AsyncMock()

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            with pytest.raises(DataSourcePermissionError) as excinfo:
                await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="disabled:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                )

        assert excinfo.value.code == "DATA_SOURCE_DISABLED"
        assert "'corpus1'" in str(excinfo.value)
        assert "currently disabled" in str(excinfo.value)
        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_denial_names_every_offending_source(self, unified_rag_service):
        """In the batch path the message must identify what to deselect.

        Authorization is decided per server, so every corpus in the batch is
        rejected together and every one of them has to be named -- a message
        that names only one (or names the ``rag-sources.json`` server key, which
        the UI never shows) leaves the rest unexplained.
        """
        mock_client = AsyncMock()

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
            self._turn_context("Public"),
        ):
            with pytest.raises(DataSourcePermissionError) as excinfo:
                await unified_rag_service.query_rag_batch(
                    username=config_manager.app_settings.test_user,
                    qualified_data_sources=["test_http:corpus1", "test_http:corpus2"],
                    messages=[{"role": "user", "content": "q"}],
                )

        message = str(excinfo.value)
        assert "'corpus1'" in message
        assert "'corpus2'" in message
        assert "test_http" not in message
        # Plural remedy, since more than one source has to be deselected.
        assert "Deselect them" in message
        mock_client.query_rag.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_level_does_not_leak_past_the_call(self, unified_rag_service):
        """The kwarg override must restore the previous ambient context."""
        from atlas.core.compliance import get_active_compliance_context

        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        with (
            patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
            patch(
                "atlas.domain.unified_rag_service.get_compliance_manager",
                return_value=self._ComplianceManager(),
            ),
        ):
            await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "q"}],
                enforced_compliance_level="Internal",
            )

        assert get_active_compliance_context() == (None, False)


class TestGateAgainstTheRealComplianceManager:
    """The gate against the real ``ComplianceLevelManager``, not a stub.

    Every other compliance test here substitutes a stub whose ``is_accessible``
    is strict equality. That is the right shape for isolating the gate, but it
    means nothing exercises the real manager -- whose behaviour with no
    ``compliance-levels.json`` loaded is *permissive*, the opposite of the stub
    and the opposite of the frontend's ``isComplianceAccessible``. The picker
    mirrors this permissiveness deliberately (see
    ``rag-panel-model-compliance.test.jsx``), so it is pinned on both sides.
    """

    @staticmethod
    def _real_manager_with_no_config():
        from pathlib import Path

        from atlas.core.compliance import ComplianceLevelManager

        # A path that cannot exist, so no levels are loaded.
        return ComplianceLevelManager(config_path=Path("/nonexistent/compliance-levels.json"))

    @pytest.mark.asyncio
    async def test_gate_is_permissive_with_no_levels_configured(self, unified_rag_service):
        """No levels configured: the gate must be a no-op, not a blanket denial.

        A deployment that never wrote a compliance-levels.json must keep working
        exactly as it did before query-time enforcement existed.
        """
        from atlas.core.compliance import (
            reset_active_compliance_context,
            set_active_compliance_context,
        )

        manager = self._real_manager_with_no_config()
        assert manager.levels == {}

        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        # "Public" against the Internal-tagged test_http source: the stub used
        # elsewhere would reject this, the real manager must allow it.
        token = set_active_compliance_context("Public", enforce=True)
        try:
            with (
                patch.object(unified_rag_service, "_get_http_client", return_value=mock_client),
                patch(
                    "atlas.domain.unified_rag_service.get_compliance_manager",
                    return_value=manager,
                ),
            ):
                response = await unified_rag_service.query_rag(
                    username=config_manager.app_settings.test_user,
                    qualified_data_source="test_http:corpus1",
                    messages=[{"role": "user", "content": "q"}],
                )
        finally:
            reset_active_compliance_context(token)

        assert response.content == "ok"
        mock_client.query_rag.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_real_manager_denies_outside_the_allowlist(self, unified_rag_service):
        """With levels configured, the real manager's allowlist is enforced."""
        import json
        import tempfile
        from pathlib import Path

        from atlas.core.compliance import (
            ComplianceLevelManager,
            reset_active_compliance_context,
            set_active_compliance_context,
        )

        levels = {
            "mode": "explicit_allowlist",
            "levels": [
                {"name": "Public", "allowed_with": ["Public"]},
                {"name": "Internal", "allowed_with": ["Internal", "Public"]},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "compliance-levels.json"
            config_path.write_text(json.dumps(levels), encoding="utf-8")
            manager = ComplianceLevelManager(config_path=config_path)
            assert set(manager.levels) == {"Public", "Internal"}

            mock_client = AsyncMock()
            token = set_active_compliance_context("Public", enforce=True)
            try:
                with (
                    patch.object(
                        unified_rag_service, "_get_http_client", return_value=mock_client
                    ),
                    patch(
                        "atlas.domain.unified_rag_service.get_compliance_manager",
                        return_value=manager,
                    ),
                ):
                    # test_http is tagged Internal; Public is not cleared for it.
                    with pytest.raises(DataSourcePermissionError) as excinfo:
                        await unified_rag_service.query_rag(
                            username=config_manager.app_settings.test_user,
                            qualified_data_source="test_http:corpus1",
                            messages=[{"role": "user", "content": "q"}],
                        )
            finally:
                reset_active_compliance_context(token)

        assert excinfo.value.code == "DATA_SOURCE_COMPLIANCE_MISMATCH"
        assert "'corpus1'" in str(excinfo.value)
        mock_client.query_rag.assert_not_called()


class TestSourceFiltering:
    """Tests for source filtering methods."""

    def test_get_http_sources(self, unified_rag_service):
        """Test getting only HTTP sources."""
        sources = unified_rag_service.get_http_sources()

        assert "test_http" in sources
        assert "test_mcp" not in sources
        assert "disabled" not in sources  # Disabled sources are excluded

    def test_get_mcp_sources(self, unified_rag_service):
        """Test getting only MCP sources."""
        sources = unified_rag_service.get_mcp_sources()

        assert "test_mcp" in sources
        assert "test_http" not in sources
        assert "disabled" not in sources


class TestFindServerForSource:
    """Tests for server lookup by source ID."""

    def test_find_server_returns_none(self, unified_rag_service):
        """Test that _find_server_for_source returns None (unimplemented)."""
        result = unified_rag_service._find_server_for_source("corpus1")
        assert result is None


class TestQueryRAGWithoutQualification:
    """Tests for querying RAG without server prefix."""

    @pytest.mark.asyncio
    async def test_query_rag_without_prefix_raises(self, unified_rag_service):
        """Test querying without server prefix raises error."""
        with pytest.raises(ValueError, match="Could not find server"):
            await unified_rag_service.query_rag(
                username=config_manager.app_settings.test_user,
                qualified_data_source="corpus1",  # No server prefix
                messages=[],
            )


class TestQueryRAGBatch:
    """Tests for query_rag_batch method."""

    @pytest.mark.asyncio
    async def test_batch_same_server_http(self, unified_rag_service):
        """Test batching multiple sources on the same HTTP server."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(
            content="Batched response",
            metadata=None,
        )

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            result = await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=["test_http:corpus1", "test_http:corpus2"],
                messages=[{"role": "user", "content": "test"}],
            )

        assert result.content == "Batched response"
        # Verify data_sources kwarg was passed with both source IDs
        call_kwargs = mock_client.query_rag.call_args
        assert call_kwargs[1]["data_sources"] == ["corpus1", "corpus2"]

    @pytest.mark.asyncio
    async def test_batch_mixed_servers_raises(self, unified_rag_service):
        """Test that mixing servers in a batch raises ValueError."""
        with pytest.raises(ValueError, match="same server"):
            await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=["test_http:corpus1", "other_server:corpus2"],
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_batch_empty_sources_raises(self, unified_rag_service):
        """Test that empty sources list raises ValueError."""
        with pytest.raises(ValueError, match="No data sources"):
            await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=[],
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_batch_unqualified_source_raises(self, unified_rag_service):
        """Test that unqualified sources raise ValueError."""
        with pytest.raises(ValueError, match="Unqualified source"):
            await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=["corpus_without_server"],
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_batch_unknown_server_raises(self, unified_rag_service):
        """Test that unknown server raises ValueError."""
        with pytest.raises(ValueError, match="RAG source not found"):
            await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=["nonexistent:corpus1"],
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_batch_mcp_source(self, mock_config_manager, mock_auth_check):
        """Test batching MCP sources delegates to rag_mcp_service.synthesize."""
        mock_rag_mcp = MagicMock()
        mock_rag_mcp.synthesize = AsyncMock(return_value={
            "results": {"answer": "MCP batch answer"},
            "meta_data": {"providers": {}},
        })

        service = UnifiedRAGService(
            config_manager=mock_config_manager,
            rag_mcp_service=mock_rag_mcp,
        )

        result = await service.query_rag_batch(
            username=config_manager.app_settings.test_user,
            qualified_data_sources=["test_mcp:src1", "test_mcp:src2"],
            messages=[{"role": "user", "content": "query"}],
        )

        assert result.content == "MCP batch answer"
        mock_rag_mcp.synthesize.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_passes_first_source_as_fallback(self, unified_rag_service):
        """Test that batch passes first source_id (not empty string) to query_rag."""
        mock_client = AsyncMock()
        mock_client.query_rag.return_value = RAGResponse(content="ok", metadata=None)

        with patch.object(unified_rag_service, "_get_http_client", return_value=mock_client):
            await unified_rag_service.query_rag_batch(
                username=config_manager.app_settings.test_user,
                qualified_data_sources=["test_http:alpha", "test_http:beta"],
                messages=[{"role": "user", "content": "q"}],
            )

        # The positional data_source arg should be "alpha", not ""
        call_args = mock_client.query_rag.call_args
        assert call_args[0][1] == "alpha"
