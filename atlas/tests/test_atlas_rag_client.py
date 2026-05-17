"""Unit tests for AtlasRAGClient (newest ATLAS-RAG OpenAPI spec)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from atlas.modules.rag.atlas_rag_client import AtlasRAGClient
from atlas.modules.rag.client import DataSource, RAGResponse, Section


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_async_client_with_response(json_payload, *, method="post"):
    """Helper that wires up an httpx.AsyncClient mock returning ``json_payload``."""
    mock_response = MagicMock()
    mock_response.json.return_value = json_payload
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    patcher = patch("httpx.AsyncClient")
    mock_client = patcher.start()
    mock_instance = AsyncMock()
    getattr(mock_instance, method).return_value = mock_response
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None
    mock_client.return_value = mock_instance
    return patcher, mock_instance, mock_response


class TestAtlasRAGClientInit:
    """Tests for AtlasRAGClient initialization."""

    def test_init_with_all_params(self, client):
        assert client.base_url == "https://rag-api.example.com"
        assert client.bearer_token == "test-token"
        assert client.default_model == "test-model"
        assert client.top_k == 4
        assert client.timeout == 30.0

    def test_init_strips_trailing_slash(self):
        c = AtlasRAGClient(base_url="https://rag-api.example.com/")
        assert c.base_url == "https://rag-api.example.com"

    def test_init_defaults(self):
        c = AtlasRAGClient(base_url="https://rag-api.example.com")
        assert c.bearer_token is None
        assert c.default_model == "openai/gpt-oss-120b"
        assert c.top_k == 4
        assert c.timeout == 60.0


class TestGetHeaders:
    def test_headers_with_auth(self, client):
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-token"

    def test_headers_without_auth(self, client_no_auth):
        headers = client_no_auth._get_headers()
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# discover_data_sources
# ---------------------------------------------------------------------------

class TestDiscoverDataSources:
    """Tests for discover_data_sources method."""

    @pytest.mark.asyncio
    async def test_discover_success_bare_list(self, client):
        """Newest spec: GET returns a bare list of data sources."""
        patcher, mock_instance, _ = _mock_async_client_with_response(
            [
                {"id": "corpus1", "label": "Corpus One", "compliance_level": "CUI", "description": "First"},
                {"id": "corpus2", "label": "Corpus Two", "compliance_level": "Public", "description": "Second"},
            ],
            method="get",
        )
        try:
            result = await client.discover_data_sources("test-user")
        finally:
            patcher.stop()

        assert len(result) == 2
        assert isinstance(result[0], DataSource)
        assert result[0].id == "corpus1"
        assert result[1].id == "corpus2"

        # Verify URL and params
        call_args = mock_instance.get.call_args
        assert call_args[0][0] == "https://rag-api.example.com/api/v1/discover/datasources"
        assert call_args[1]["params"] == {"role": "read", "as_user": "test-user"}
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_discover_legacy_envelope(self, client):
        """Legacy compat: GET returns ``{data_sources: [...]}`` envelope."""
        patcher, _, _ = _mock_async_client_with_response(
            {"data_sources": [{"id": "c", "label": "C", "compliance_level": "CUI", "description": ""}]},
            method="get",
        )
        try:
            result = await client.discover_data_sources("test-user")
        finally:
            patcher.stop()
        assert len(result) == 1
        assert result[0].id == "c"

    @pytest.mark.asyncio
    async def test_discover_empty_response(self, client):
        patcher, _, _ = _mock_async_client_with_response([], method="get")
        try:
            result = await client.discover_data_sources("test-user")
        finally:
            patcher.stop()
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_http_error(self, client):
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
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_request_error(self, client):
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
        assert result == []


# ---------------------------------------------------------------------------
# query_rag — newest spec response shape
# ---------------------------------------------------------------------------

NEWEST_RESPONSE = {
    "message": {
        "role": "assistant",
        "content": "This is the answer.",
    },
    "metadata": {
        "response_time": 2,
        "references": [
            {
                "citation": "[1] doc1.pdf",
                "document_ref": 1,
                "filename": "doc1.pdf",
                "sections": [
                    {"section_ref": 1, "text": "Snippet one.", "relevance": 0.95},
                    {"section_ref": 2, "text": "Snippet two.", "relevance": 0.80},
                ],
            },
            {
                "citation": "[2] doc2.pdf",
                "document_ref": 2,
                "filename": "doc2.pdf",
                "sections": [
                    {"section_ref": 1, "text": "Other snippet.", "relevance": 0.72},
                ],
            },
        ],
    },
}


class TestQueryRagNewestSpec:
    """Tests for query_rag against the newest ATLAS-RAG response shape."""

    @pytest.mark.asyncio
    async def test_query_request_shape(self, client):
        """Newest spec: payload is {messages, stream, corpora} — no model, no hybrid_search_kwargs."""
        patcher, mock_instance, _ = _mock_async_client_with_response(NEWEST_RESPONSE)
        try:
            messages = [{"role": "user", "content": "What is the answer?"}]
            await client.query_rag("test-user", "corpus1", messages)
        finally:
            patcher.stop()

        call_args = mock_instance.post.call_args
        assert call_args[0][0] == "https://rag-api.example.com/api/v1/rag/completions"
        assert call_args[1]["params"] == {"as_user": "test-user"}
        payload = call_args[1]["json"]
        assert payload["messages"] == messages
        assert payload["stream"] is False
        assert payload["corpora"] == "corpus1"
        assert "model" not in payload
        assert "hybrid_search_kwargs" not in payload
        assert "top_k" not in payload

    @pytest.mark.asyncio
    async def test_query_parses_message_content(self, client):
        patcher, _, _ = _mock_async_client_with_response(NEWEST_RESPONSE)
        try:
            result = await client.query_rag(
                "test-user", "corpus1", [{"role": "user", "content": "q"}],
            )
        finally:
            patcher.stop()

        assert isinstance(result, RAGResponse)
        assert result.content == "This is the answer."
        assert result.is_completion is True

    @pytest.mark.asyncio
    async def test_query_parses_references_with_sections(self, client):
        patcher, _, _ = _mock_async_client_with_response(NEWEST_RESPONSE)
        try:
            result = await client.query_rag(
                "test-user", "corpus1", [{"role": "user", "content": "q"}],
            )
        finally:
            patcher.stop()

        assert result.metadata is not None
        # response_time (seconds) is surfaced as ms in the existing footer
        assert result.metadata.query_processing_time_ms == 2000
        assert result.metadata.data_source_name == "corpus1"
        assert len(result.metadata.documents_found) == 2

        doc1 = result.metadata.documents_found[0]
        assert doc1.title == "doc1.pdf"
        assert doc1.citation == "[1] doc1.pdf"
        assert doc1.document_ref == 1
        # confidence_score = max section relevance
        assert doc1.confidence_score == 0.95
        assert len(doc1.sections) == 2
        assert isinstance(doc1.sections[0], Section)
        assert doc1.sections[0].text == "Snippet one."
        assert doc1.sections[0].relevance == 0.95
        assert doc1.sections[1].section_ref == 2

        doc2 = result.metadata.documents_found[1]
        assert doc2.document_ref == 2
        assert doc2.title == "doc2.pdf"
        assert doc2.confidence_score == 0.72
        assert len(doc2.sections) == 1

    @pytest.mark.asyncio
    async def test_query_with_list_corpora(self, client):
        """When data_sources is provided, payload.corpora is a list."""
        patcher, mock_instance, _ = _mock_async_client_with_response(NEWEST_RESPONSE)
        try:
            await client.query_rag(
                "test-user", "ignored", [{"role": "user", "content": "q"}],
                data_sources=["corpus1", "corpus2"],
            )
        finally:
            patcher.stop()
        payload = mock_instance.post.call_args[1]["json"]
        assert payload["corpora"] == ["corpus1", "corpus2"]

    @pytest.mark.asyncio
    async def test_query_empty_references_returns_no_documents(self, client):
        patcher, _, _ = _mock_async_client_with_response(
            {
                "message": {"role": "assistant", "content": "nothing found"},
                "metadata": {"response_time": 1, "references": []},
            }
        )
        try:
            result = await client.query_rag(
                "test-user", "corpus1", [{"role": "user", "content": "q"}],
            )
        finally:
            patcher.stop()

        assert result.metadata is not None
        assert result.metadata.documents_found == []

    @pytest.mark.asyncio
    async def test_query_null_references_returns_no_documents(self, client):
        """Spec allows ``references: null``; treat as empty."""
        patcher, _, _ = _mock_async_client_with_response(
            {
                "message": {"role": "assistant", "content": "no refs"},
                "metadata": {"response_time": 1, "references": None},
            }
        )
        try:
            result = await client.query_rag(
                "test-user", "corpus1", [{"role": "user", "content": "q"}],
            )
        finally:
            patcher.stop()
        assert result.metadata is not None
        assert result.metadata.documents_found == []

    @pytest.mark.asyncio
    async def test_query_ignores_hybrid_search_kwargs(self, client):
        """Newest spec drops hybrid_search_kwargs; client should not send them."""
        patcher, mock_instance, _ = _mock_async_client_with_response(NEWEST_RESPONSE)
        try:
            await client.query_rag(
                "test-user", "corpus1", [{"role": "user", "content": "q"}],
                hybrid_search_kwargs={"top_k": 9, "extra": "ignored"},
            )
        finally:
            patcher.stop()
        payload = mock_instance.post.call_args[1]["json"]
        assert "hybrid_search_kwargs" not in payload
        assert "top_k" not in payload


# ---------------------------------------------------------------------------
# query_rag — error handling
# ---------------------------------------------------------------------------

class TestQueryRagErrors:
    @pytest.mark.asyncio
    async def test_query_403_forbidden(self, client):
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

            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("user", "c", [{"role": "user", "content": "q"}])
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_query_404_not_found(self, client):
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

            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("user", "c", [{"role": "user", "content": "q"}])
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_query_500_error(self, client):
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

            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("user", "c", [{"role": "user", "content": "q"}])
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_query_connection_error(self, client):
        import httpx
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.RequestError(
                "Connection failed", request=MagicMock()
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(HTTPException) as exc_info:
                await client.query_rag("user", "c", [{"role": "user", "content": "q"}])
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Metadata parsing (newest and legacy)
# ---------------------------------------------------------------------------

class TestParseResponseMetadata:
    """Tests for parsing the metadata block."""

    def test_parses_newest_metadata_with_sections(self, client):
        data = {
            "message": {"role": "assistant", "content": "x"},
            "metadata": {
                "response_time": 3,
                "references": [
                    {
                        "citation": "[1] thing.pdf",
                        "document_ref": 1,
                        "filename": "thing.pdf",
                        "sections": [
                            {"section_ref": 1, "text": "first snippet", "relevance": 0.9},
                            {"section_ref": 2, "text": "second snippet", "relevance": 0.7},
                        ],
                    }
                ],
            },
        }
        result = client._parse_response_metadata(data, "fallback-corpus")
        assert result is not None
        assert result.query_processing_time_ms == 3000
        assert len(result.documents_found) == 1
        doc = result.documents_found[0]
        assert doc.title == "thing.pdf"
        assert doc.citation == "[1] thing.pdf"
        assert doc.document_ref == 1
        assert doc.confidence_score == 0.9
        assert [s.text for s in doc.sections] == ["first snippet", "second snippet"]

    def test_parses_metadata_no_references(self, client):
        data = {
            "message": {"role": "assistant", "content": "x"},
            "metadata": {"response_time": 1, "references": []},
        }
        result = client._parse_response_metadata(data, "fallback")
        assert result is not None
        assert result.documents_found == []

    def test_no_metadata_returns_none(self, client):
        result = client._parse_response_metadata({"choices": []}, "corpus")
        assert result is None

    def test_legacy_metadata_still_parsed(self, client):
        """Backwards-compat path: legacy ``rag_metadata`` shape still parsed."""
        data = {
            "rag_metadata": {
                "query_processing_time_ms": 200,
                "documents_found": [
                    {
                        "data_source": {"id": "corp", "label": "Corp"},
                        "text": "snippet",
                        "content_type": "atlas-search",
                        "confidence_score": 0.85,
                        "id": "doc-1",
                    }
                ],
                "data_sources": [{"id": "corp", "label": "Corp"}],
                "retrieval_method": "hybrid",
            }
        }
        result = client._parse_response_metadata(data, "fallback")
        assert result is not None
        assert result.query_processing_time_ms == 200
        assert result.documents_found[0].title == "Corp"

    def test_malformed_section_is_skipped(self, client):
        data = {
            "message": {"role": "assistant", "content": "x"},
            "metadata": {
                "response_time": 1,
                "references": [
                    {
                        "document_ref": 1,
                        "filename": "d.pdf",
                        "sections": [
                            {"section_ref": 1, "text": "ok", "relevance": 0.5},
                            "not-a-dict",
                            {"text": "missing fields"},
                        ],
                    }
                ],
            },
        }
        result = client._parse_response_metadata(data, "src")
        # Only well-formed sections retained
        assert result is not None
        assert len(result.documents_found[0].sections) == 1
        assert result.documents_found[0].sections[0].text == "ok"


# ---------------------------------------------------------------------------
# Factory + username resolution (unchanged behavior)
# ---------------------------------------------------------------------------

class TestFactoryFunction:
    def test_factory_creates_client_from_config(self):
        from atlas.modules.rag.atlas_rag_client import create_atlas_rag_client_from_config

        mock_settings = MagicMock()
        mock_settings.external_rag_url = "https://test-api.example.com"
        mock_settings.external_rag_bearer_token = "factory-token"
        mock_settings.external_rag_default_model = "factory-model"
        mock_settings.external_rag_top_k = 8

        mock_config_manager = MagicMock()
        mock_config_manager.app_settings = mock_settings

        c = create_atlas_rag_client_from_config(mock_config_manager)
        assert isinstance(c, AtlasRAGClient)
        assert c.base_url == "https://test-api.example.com"
        assert c.bearer_token == "factory-token"
        assert c.default_model == "factory-model"
        assert c.top_k == 8


class TestResolveUsername:
    def test_strip_domain_disabled_by_default(self, client):
        assert client.strip_domain is False
        assert client._resolve_username("user@corp.com") == "user@corp.com"

    def test_strip_domain_enabled(self):
        c = AtlasRAGClient(base_url="https://rag-api.example.com", strip_domain=True)
        assert c._resolve_username("user@corp.com") == "user"

    def test_strip_domain_no_at_sign(self):
        c = AtlasRAGClient(base_url="https://rag-api.example.com", strip_domain=True)
        assert c._resolve_username("plainuser") == "plainuser"

    def test_strip_domain_multiple_at_signs(self):
        c = AtlasRAGClient(base_url="https://rag-api.example.com", strip_domain=True)
        assert c._resolve_username("user@sub@corp.com") == "user"
