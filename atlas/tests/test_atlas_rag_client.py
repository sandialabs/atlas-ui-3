"""Unit tests for AtlasRAGClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from atlas.modules.rag.atlas_rag_client import AtlasRAGClient
from atlas.modules.rag.client import DataSource, RAGResponse


@pytest.fixture
def client():
    """Create an AtlasRAGClient instance for testing."""
    return AtlasRAGClient(
        base_url="https://rag-api.example.com",
        bearer_token="test-token",
        default_model="test-model",
        top_k=4,
        timeout=30.0,
    )


@pytest.fixture
def client_no_auth():
    """Create an AtlasRAGClient without authentication."""
    return AtlasRAGClient(
        base_url="https://rag-api.example.com",
        bearer_token=None,
    )


class TestAtlasRAGClientInit:
    """Tests for AtlasRAGClient initialization."""

    def test_init_with_all_params(self, client):
        """Test initialization with all parameters."""
        assert client.base_url == "https://rag-api.example.com"
        assert client.bearer_token == "test-token"
        assert client.default_model == "test-model"
        assert client.top_k == 4
        assert client.timeout == 30.0

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base_url."""
        client = AtlasRAGClient(base_url="https://rag-api.example.com/")
        assert client.base_url == "https://rag-api.example.com"

    def test_init_defaults(self):
        """Test initialization with default values."""
        client = AtlasRAGClient(base_url="https://rag-api.example.com")
        assert client.bearer_token is None
        assert client.default_model == "openai/gpt-oss-120b"
        assert client.top_k == 4
        assert client.timeout == 60.0


class TestGetHeaders:
    """Tests for header generation."""

    def test_headers_with_auth(self, client):
        """Test headers include Bearer token when provided."""
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-token"

    def test_headers_without_auth(self, client_no_auth):
        """Test headers without Bearer token when not provided."""
        headers = client_no_auth._get_headers()
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers


class TestDiscoverDataSources:
    """Tests for discover_data_sources method."""

    @pytest.mark.asyncio
    async def test_discover_success(self, client):
        """Test successful data source discovery."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data_sources": [
                {"id": "corpus1", "label": "Corpus One", "compliance_level": "CUI", "description": "First corpus"},
                {"id": "corpus2", "label": "Corpus Two", "compliance_level": "Public", "description": "Second corpus"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.discover_data_sources("test-user")

        assert len(result) == 2
        assert isinstance(result[0], DataSource)
        assert result[0].id == "corpus1"
        assert result[0].label == "Corpus One"
        assert result[0].compliance_level == "CUI"
        assert result[0].description == "First corpus"
        assert result[1].id == "corpus2"
        assert result[1].label == "Corpus Two"
        assert result[1].compliance_level == "Public"

        # Verify correct URL and params
        mock_instance.get.assert_called_once()
        call_args = mock_instance.get.call_args
        assert call_args[0][0] == "https://rag-api.example.com/discover/datasources"
        assert call_args[1]["params"] == {"as_user": "test-user"}
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_discover_empty_response(self, client):
        """Test discovery with no accessible data sources."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data_sources": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.discover_data_sources("test-user")

        assert result == []

    @pytest.mark.asyncio
    async def test_discover_http_error(self, client):
        """Test discovery handles HTTP errors gracefully."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_instance.get.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=mock_response
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.discover_data_sources("test-user")

        # Should return empty list on error
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_request_error(self, client):
        """Test discovery handles network/request errors gracefully."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError(
                "Connection failed", request=MagicMock()
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await client.discover_data_sources("test-user")

        # Should return empty list on error
        assert result == []


class TestQueryRag:
    """Tests for query_rag method."""

    @pytest.mark.asyncio
    async def test_query_success(self, client):
        """Test successful RAG query."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-xxx",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "This is the answer."},
                    "finish_reason": "stop",
                }
            ],
            "rag_metadata": {
                "query_processing_time_ms": 150,
                "documents_found": [
                    {
                        "corpus_id": "corpus1",
                        "text": "Some text",
                        "confidence_score": 0.95,
                        "content_type": "atlas-search",
                        "id": "doc-123",
                    }
                ],
                "data_sources": ["corpus1"],
                "retrieval_method": "similarity",
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "What is the answer?"}]
            result = await client.query_rag("test-user", "corpus1", messages)

        assert isinstance(result, RAGResponse)
        assert result.content == "This is the answer."
        assert result.is_completion is True  # Should detect chat.completion format
        assert result.metadata is not None
        assert result.metadata.query_processing_time_ms == 150
        assert result.metadata.data_source_name == "corpus1"
        assert result.metadata.retrieval_method == "similarity"
        assert len(result.metadata.documents_found) == 1
        assert result.metadata.documents_found[0].source == "corpus1"
        assert result.metadata.documents_found[0].confidence_score == 0.95

        # Verify correct URL, params, and payload
        mock_instance.post.assert_called_once()
        call_args = mock_instance.post.call_args
        assert call_args[0][0] == "https://rag-api.example.com/rag/completions"
        assert call_args[1]["params"] == {"as_user": "test-user"}
        payload = call_args[1]["json"]
        assert payload["messages"] == messages
        assert payload["stream"] is False
        assert payload["model"] == "test-model"
        assert payload["top_k"] == 4
        assert payload["corpora"] == ["corpus1"]

    @pytest.mark.asyncio
    async def test_query_without_metadata(self, client):
        """Test RAG query without metadata in response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Simple answer."},
                }
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            result = await client.query_rag("test-user", "corpus1", messages)

        assert result.content == "Simple answer."
        assert result.metadata is None
        assert result.is_completion is False  # No 'object' field in response

    @pytest.mark.asyncio
    async def test_query_403_forbidden(self, client):
        """Test RAG query raises HTTPException on 403."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.text = "Access denied"
            mock_instance.post.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=mock_response
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("test-user", "corpus1", messages)

            assert exc_info.value.status_code == 403
            assert "Access denied" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_query_404_not_found(self, client):
        """Test RAG query raises HTTPException on 404."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "Not found"
            mock_instance.post.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=mock_response
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("test-user", "corpus1", messages)

            assert exc_info.value.status_code == 404
            assert "not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_query_500_error(self, client):
        """Test RAG query raises HTTPException on 500."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal error"
            mock_instance.post.side_effect = httpx.HTTPStatusError(
                "Error", request=MagicMock(), response=mock_response
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("test-user", "corpus1", messages)

            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_query_connection_error(self, client):
        """Test RAG query raises HTTPException on connection error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.RequestError(
                "Connection failed", request=MagicMock()
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("test-user", "corpus1", messages)

            assert exc_info.value.status_code == 500
            assert "connect" in exc_info.value.detail.lower()


class TestParseRagMetadata:
    """Tests for _parse_rag_metadata method."""

    def test_parse_full_metadata(self, client):
        """Test parsing complete RAG metadata."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 200,
                "documents_found": [
                    {
                        "corpus_id": "test-corpus",
                        "content_type": "document",
                        "confidence_score": 0.85,
                        "id": "doc-456",
                        "last_modified": "2025-01-01T00:00:00Z",
                    }
                ],
                "data_sources": ["test-corpus"],
                "retrieval_method": "hybrid",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result is not None
        assert result.query_processing_time_ms == 200
        assert result.data_source_name == "test-corpus"
        assert result.retrieval_method == "hybrid"
        assert len(result.documents_found) == 1
        assert result.documents_found[0].source == "test-corpus"
        assert result.documents_found[0].chunk_id == "doc-456"
        assert result.documents_found[0].last_modified == "2025-01-01T00:00:00Z"

    def test_parse_metadata_with_fallback_datasource(self, client):
        """Test metadata parsing uses fallback when data_sources empty."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 100,
                "documents_found": [],
                "data_sources": [],
                "retrieval_method": "similarity",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result.data_source_name == "fallback-corpus"

    def test_parse_no_metadata(self, client):
        """Test parsing when no rag_metadata present."""
        data = {"choices": []}
        result = client._parse_rag_metadata(data, "corpus")
        assert result is None

    def test_parse_empty_metadata(self, client):
        """Test parsing when rag_metadata is empty."""
        data = {"rag_metadata": None}
        result = client._parse_rag_metadata(data, "corpus")
        assert result is None


class TestFactoryFunction:
    """Tests for create_atlas_rag_client_from_config factory."""

    def test_factory_creates_client_from_config(self):
        """Test factory function creates properly configured client."""
        from atlas.modules.rag.atlas_rag_client import create_atlas_rag_client_from_config

        mock_settings = MagicMock()
        mock_settings.external_rag_url = "https://test-api.example.com"
        mock_settings.external_rag_bearer_token = "factory-token"
        mock_settings.external_rag_default_model = "factory-model"
        mock_settings.external_rag_top_k = 8

        mock_config_manager = MagicMock()
        mock_config_manager.app_settings = mock_settings

        client = create_atlas_rag_client_from_config(mock_config_manager)

        assert isinstance(client, AtlasRAGClient)
        assert client.base_url == "https://test-api.example.com"
        assert client.bearer_token == "factory-token"
        assert client.default_model == "factory-model"
        assert client.top_k == 8
