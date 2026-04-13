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

    def test_parse_metadata_data_sources_as_objects(self, client):
        """Test parsing when data_sources entries are dicts with id/label."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 50,
                "documents_found": [],
                "data_sources": [
                    {
                        "id": "atlas_team_data",
                        "label": "Search Dataset | UUR: ATLAS Team Data",
                        "compliance_level": "UUR",
                        "description": "",
                    },
                    {
                        "id": "atlas_team_data_2",
                        "label": "Search Dataset | UUR: ATLAS Team Data 2",
                        "compliance_level": "UUR",
                        "description": "",
                    },
                ],
                "retrieval_method": "similarity",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result is not None
        assert result.data_source_name == "Search Dataset | UUR: ATLAS Team Data"

    def test_parse_metadata_data_source_dict_missing_label(self, client):
        """Test parsing when data_sources dict has no label but has id."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 50,
                "documents_found": [],
                "data_sources": [
                    {"id": "atlas_team_data", "compliance_level": "UUR"},
                ],
                "retrieval_method": "similarity",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result is not None
        assert result.data_source_name == "atlas_team_data"

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

    def test_parse_documents_with_nested_data_source(self, client):
        """Test documents with nested data_source dict populate source and title."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 2993,
                "documents_found": [
                    {
                        "data_source": {
                            "id": "atlas_team_data",
                            "label": "Search Dataset | UUR: ATLAS Team Data",
                            "compliance_level": "UUR",
                            "description": "",
                        },
                        "text": "Some document text.",
                        "id": 459716260075906100,
                        "content_type": "atlas-search",
                        "confidence_score": 0.48,
                        "last_modified": "20250724_144206",
                    },
                    {
                        "data_source": {
                            "id": "atlas_team_data",
                            "label": "Search Dataset | UUR: ATLAS Team Data",
                            "compliance_level": "UUR",
                            "description": "",
                        },
                        "text": "Another document text.",
                        "id": 459716260075906101,
                        "content_type": "atlas-search",
                        "confidence_score": 0.47,
                        "last_modified": "20250724_144206",
                    },
                ],
                "data_sources": [
                    {
                        "id": "atlas_team_data",
                        "label": "Search Dataset | UUR: ATLAS Team Data",
                        "compliance_level": "UUR",
                        "description": "",
                    }
                ],
                "retrieval_method": "similarity",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result is not None
        assert len(result.documents_found) == 2
        assert result.documents_found[0].source == "atlas_team_data"
        assert result.documents_found[0].title == "Search Dataset | UUR: ATLAS Team Data"
        assert result.documents_found[1].source == "atlas_team_data"
        assert result.documents_found[1].title == "Search Dataset | UUR: ATLAS Team Data"

    def test_parse_documents_flat_corpus_id_takes_precedence(self, client):
        """Flat corpus_id overrides nested data_source.id when both present."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 100,
                "documents_found": [
                    {
                        "corpus_id": "legacy-corpus",
                        "data_source": {"id": "nested-id", "label": "Nested Label"},
                        "content_type": "atlas-search",
                        "confidence_score": 0.9,
                    },
                ],
                "data_sources": [],
                "retrieval_method": "similarity",
            }
        }

        result = client._parse_rag_metadata(data, "fallback-corpus")

        assert result is not None
        assert result.documents_found[0].source == "legacy-corpus"


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


class TestResolveUsername:
    """Tests for _resolve_username with strip_domain."""

    def test_strip_domain_disabled_by_default(self, client):
        """Test that strip_domain defaults to False."""
        assert client.strip_domain is False
        assert client._resolve_username("user@corp.com") == "user@corp.com"

    def test_strip_domain_enabled(self):
        """Test strip_domain strips the @domain portion."""
        c = AtlasRAGClient(
            base_url="https://rag-api.example.com",
            strip_domain=True,
        )
        assert c._resolve_username("user@corp.com") == "user"

    def test_strip_domain_no_at_sign(self):
        """Test strip_domain is a no-op when no @ in username."""
        c = AtlasRAGClient(
            base_url="https://rag-api.example.com",
            strip_domain=True,
        )
        assert c._resolve_username("plainuser") == "plainuser"

    def test_strip_domain_multiple_at_signs(self):
        """Test strip_domain only strips at the first @."""
        c = AtlasRAGClient(
            base_url="https://rag-api.example.com",
            strip_domain=True,
        )
        assert c._resolve_username("user@sub@corp.com") == "user"

    def test_strip_domain_disabled_preserves_email(self):
        """Test strip_domain=False preserves email username."""
        c = AtlasRAGClient(
            base_url="https://rag-api.example.com",
            strip_domain=False,
        )
        assert c._resolve_username("user@corp.com") == "user@corp.com"


class TestQueryRagBatchCorpora:
    """Tests for query_rag with data_sources parameter (batch corpora)."""

    @pytest.mark.asyncio
    async def test_query_with_multiple_corpora(self, client):
        """Test query_rag sends multiple corpora in a single request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "object": "chat.completion",
            "choices": [
                {"message": {"role": "assistant", "content": "Batched answer."}}
            ],
            "rag_metadata": {
                "query_processing_time_ms": 200,
                "documents_found": [],
                "data_sources": ["corpus1", "corpus2"],
                "retrieval_method": "similarity",
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Question"}]
            result = await client.query_rag(
                "test-user", "corpus1", messages,
                data_sources=["corpus1", "corpus2"],
            )

        assert isinstance(result, RAGResponse)
        assert result.content == "Batched answer."

        # Verify the corpora list in the payload uses data_sources, not single source
        payload = mock_instance.post.call_args[1]["json"]
        assert payload["corpora"] == ["corpus1", "corpus2"]

    @pytest.mark.asyncio
    async def test_query_data_sources_overrides_single_source(self, client):
        """Test that data_sources parameter takes precedence over data_source."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"role": "assistant", "content": "Answer."}}
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Q"}]
            await client.query_rag(
                "test-user", "ignored-source", messages,
                data_sources=["real-a", "real-b"],
            )

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["corpora"] == ["real-a", "real-b"]

    @pytest.mark.asyncio
    async def test_query_single_source_fallback(self, client):
        """Test that without data_sources, single data_source is used."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"role": "assistant", "content": "Answer."}}
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            messages = [{"role": "user", "content": "Q"}]
            await client.query_rag("test-user", "single-corpus", messages)

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["corpora"] == ["single-corpus"]
