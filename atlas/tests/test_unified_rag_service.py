"""Unit tests for UnifiedRAGService.

Tests the unified RAG service that aggregates HTTP and MCP RAG sources.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.config_manager import RAGSourceConfig, RAGSourcesConfig
from atlas.modules.rag.client import DataSource, RAGResponse


@pytest.fixture
def mock_config_manager():
    """Create a mock config manager with test RAG sources."""
    config_manager = MagicMock()

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
        groups=["admin"],
        compliance_level="SOC2",
        enabled=True,
    )

    disabled_source = RAGSourceConfig(
        type="http",
        display_name="Disabled RAG",
        url="http://disabled.example.com",
        enabled=False,
    )

    config_manager.rag_sources_config = RAGSourcesConfig(
        sources={
            "test_http": http_source,
            "test_mcp": mcp_source,
            "disabled": disabled_source,
        }
    )

    return config_manager


@pytest.fixture
def mock_auth_check():
    """Create a mock auth check function."""
    async def auth_check(username: str, group: str) -> bool:
        # test@test.com is in "users" group only
        if username == "test@test.com":
            return group == "users"
        # admin@test.com is in both "users" and "admin" groups
        if username == "admin@test.com":
            return group in ["users", "admin"]
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
        result = await unified_rag_service._is_user_authorized("test@test.com", ["users"])
        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_authorized_user_not_in_group(self, unified_rag_service):
        """Test authorization when user is not in required group."""
        result = await unified_rag_service._is_user_authorized("test@test.com", ["admin"])
        assert result is False

    @pytest.mark.asyncio
    async def test_is_user_authorized_no_auth_func(self, mock_config_manager):
        """Test authorization when no auth check function is provided."""
        service = UnifiedRAGService(config_manager=mock_config_manager)
        result = await service._is_user_authorized("anyone@test.com", ["admin"])
        # Should return True when no auth function (permissive by default)
        assert result is True


class TestDiscoverDataSources:
    """Tests for data source discovery."""

    @pytest.mark.asyncio
    async def test_discover_skips_disabled_sources(self, unified_rag_service):
        """Test that disabled sources are skipped during discovery."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            await unified_rag_service.discover_data_sources("test@test.com")

            # Should not be called for disabled source
            call_args = [call[0][0] for call in mock_discover.call_args_list]
            assert "disabled" not in call_args

    @pytest.mark.asyncio
    async def test_discover_filters_by_authorization(self, unified_rag_service):
        """Test that sources are filtered by user authorization."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            # test@test.com is only in "users" group, not "admin"
            await unified_rag_service.discover_data_sources("test@test.com")

            # Should only discover test_http (users group), not test_mcp (admin group)
            call_args = [call[0][0] for call in mock_discover.call_args_list]
            assert "test_http" in call_args
            # test_mcp requires admin group, which test@test.com doesn't have

    @pytest.mark.asyncio
    async def test_discover_includes_admin_sources_for_admin(self, unified_rag_service):
        """Test that admin user can see admin-only sources."""
        with patch.object(unified_rag_service, "_discover_http_source", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {"server": "test", "sources": []}

            # admin@test.com is in both "users" and "admin" groups
            await unified_rag_service.discover_data_sources("admin@test.com")

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
                "test_http", source_config, "test@test.com"
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
                "test_http", source_config, "test@test.com"
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
                "test_http", source_config, "test@test.com"
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
                username="test@test.com",
                qualified_data_source="test_http:corpus1",
                messages=[{"role": "user", "content": "test query"}],
            )

        assert result.content == "Test response"
        mock_client.query_rag.assert_called_once_with(
            "test@test.com",
            "corpus1",
            [{"role": "user", "content": "test query"}],
        )

    @pytest.mark.asyncio
    async def test_query_rag_unknown_server(self, unified_rag_service):
        """Test querying RAG with unknown server raises error."""
        with pytest.raises(ValueError, match="RAG source not found"):
            await unified_rag_service.query_rag(
                username="test@test.com",
                qualified_data_source="unknown_server:corpus1",
                messages=[],
            )

    @pytest.mark.asyncio
    async def test_query_rag_mcp_source_without_service_raises(self, unified_rag_service):
        """Test querying MCP source without RAGMCPService raises ValueError."""
        # The unified_rag_service fixture has no rag_mcp_service configured
        with pytest.raises(ValueError, match="RAGMCPService not configured"):
            await unified_rag_service.query_rag(
                username="admin@test.com",
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
            username="admin@test.com",
            qualified_data_source="test_mcp:corpus1",
            messages=messages,
        )

        # Verify RAGMCPService.synthesize was called
        mock_rag_mcp_service.synthesize.assert_called_once_with(
            username="admin@test.com",
            query="What is the fleet info?",
            sources=["test_mcp:corpus1"],
        )

        # Verify response format
        assert isinstance(result, RAGResponse)
        assert result.content == "Test answer from MCP RAG"
        assert result.metadata is not None
        assert result.metadata.data_source_name == "test_mcp"
        assert result.metadata.retrieval_method == "mcp_synthesis"


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
                username="test@test.com",
                qualified_data_source="corpus1",  # No server prefix
                messages=[],
            )
