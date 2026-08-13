"""Tests for the v2 tool-oriented RAG interface (issue #791).

v2 sends an explicit ``query`` instead of the conversation and lets the caller
pick the shape of the answer with ``mode``. These tests pin the request body
(what leaves the process), both response shapes, and the routing decision that
sends a source to v1 or v2.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.models import RAGSourceConfig, RAGSourcesConfig
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient
from atlas.modules.rag.client import RAGResponse


def _mock_post(json_payload, status_code=200):
    """Patch httpx.AsyncClient so POST returns ``json_payload``."""
    mock_response = MagicMock()
    mock_response.json.return_value = json_payload
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()

    patcher = patch("httpx.AsyncClient")
    mock_client = patcher.start()
    mock_instance = AsyncMock()
    mock_instance.post.return_value = mock_response
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None
    mock_client.return_value = mock_instance
    return patcher, mock_instance


RAW_RESPONSE = {
    "query": "What is the PTO carryover limit?",
    "mode": "raw",
    "results": {
        "hits": [
            {
                "document_ref": 1,
                "filename": "pto-policy.pdf",
                "title": "PTO Policy",
                "citation": "[1] PTO Policy, pto-policy.pdf",
                "sections": [
                    {"section_ref": 1, "text": "Carry over up to 240 hours.", "relevance": 0.91},
                    {"section_ref": 2, "text": "Excess hours are forfeited.", "relevance": 0.55},
                ],
            },
            {
                "document_ref": 2,
                "filename": "handbook.pdf",
                "title": "Handbook",
                "citation": "[2] Handbook, handbook.pdf",
                "sections": [
                    {"section_ref": 1, "text": "See the PTO policy.", "relevance": 0.42},
                ],
            },
        ],
        "stats": {"total_found": 2, "top_k": 5},
    },
    "metadata": {"response_time_ms": 128, "corpora_searched": ["company-policies"]},
}

SYNTHESIZED_RESPONSE = {
    "query": "What is the PTO carryover limit?",
    "mode": "synthesized",
    "results": {
        "answer": "Employees may carry over up to 240 hours. [1]",
        "citations": [
            {
                "document_ref": 1,
                "filename": "pto-policy.pdf",
                "title": "PTO Policy",
                "citation": "[1] PTO Policy, pto-policy.pdf",
            },
        ],
    },
    "metadata": {
        "response_time_ms": 305,
        "corpora_searched": ["company-policies"],
        "fallback_used": False,
    },
}


@pytest.fixture
def v2_client():
    return AtlasRAGClient(
        base_url="https://rag-api.example.com",
        bearer_token="test-token",
        top_k=4,
        api_version="v2",
    )


class TestV2ClientDefaults:
    """The contract version picks the default paths, nothing else."""

    def test_v2_paths(self, v2_client):
        assert v2_client.api_version == "v2"
        assert v2_client.discovery_path == "/api/v2/discover/datasources"
        assert v2_client.query_path == "/api/v2/rag/query"

    def test_v1_is_the_default(self):
        client = AtlasRAGClient(base_url="https://rag-api.example.com")
        assert client.api_version == "v1"
        assert client.query_path == "/api/v1/rag/completions"

    def test_explicit_paths_win(self):
        client = AtlasRAGClient(
            base_url="https://rag-api.example.com",
            api_version="v2",
            query_path="/custom/query",
        )
        assert client.query_path == "/custom/query"


class TestV2RequestShape:
    """What actually leaves the process."""

    @pytest.mark.asyncio
    async def test_sends_query_not_messages(self, v2_client):
        patcher, mock_instance = _mock_post(RAW_RESPONSE)
        try:
            await v2_client.query_v2(
                "alice@corp.com",
                query="What is the PTO carryover limit?",
                corpora="company-policies",
                mode="raw",
                top_k=5,
            )
        finally:
            patcher.stop()

        call_args = mock_instance.post.call_args
        assert call_args[0][0] == "https://rag-api.example.com/api/v2/rag/query"
        assert call_args[1]["params"] == {"as_user": "alice@corp.com"}
        payload = call_args[1]["json"]
        assert payload == {
            "query": "What is the PTO carryover limit?",
            "corpora": "company-policies",
            "mode": "raw",
            "top_k": 5,
        }
        # The whole point of v2: no conversation history on the wire.
        assert "messages" not in payload

    @pytest.mark.asyncio
    async def test_top_k_defaults_to_client_config(self, v2_client):
        patcher, mock_instance = _mock_post(RAW_RESPONSE)
        try:
            await v2_client.query_v2("u", query="q", corpora=["a", "b"])
        finally:
            patcher.stop()

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["top_k"] == 4
        assert payload["corpora"] == ["a", "b"]
        assert payload["mode"] == "raw"

    @pytest.mark.asyncio
    async def test_filters_and_synthesis_params_forwarded(self, v2_client):
        patcher, mock_instance = _mock_post(SYNTHESIZED_RESPONSE)
        try:
            await v2_client.query_v2(
                "u",
                query="q",
                corpora="a",
                mode="synthesized",
                filters={"doc_type": "policy"},
                synthesis_params={"length": "short"},
            )
        finally:
            patcher.stop()

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["filters"] == {"doc_type": "policy"}
        assert payload["synthesis_params"] == {"length": "short"}

    @pytest.mark.asyncio
    async def test_synthesis_params_dropped_in_raw_mode(self, v2_client):
        """``synthesis_params`` has no meaning without synthesis; don't send it."""
        patcher, mock_instance = _mock_post(RAW_RESPONSE)
        try:
            await v2_client.query_v2(
                "u", query="q", corpora="a", mode="raw", synthesis_params={"length": "short"},
            )
        finally:
            patcher.stop()

        assert "synthesis_params" not in mock_instance.post.call_args[1]["json"]

    @pytest.mark.asyncio
    async def test_strip_domain_applies(self):
        client = AtlasRAGClient(
            base_url="https://rag-api.example.com", api_version="v2", strip_domain=True,
        )
        patcher, mock_instance = _mock_post(RAW_RESPONSE)
        try:
            await client.query_v2("alice@corp.com", query="q", corpora="a")
        finally:
            patcher.stop()

        assert mock_instance.post.call_args[1]["params"] == {"as_user": "alice"}


class TestV2CallerErrors:
    """Caller bugs raise ValueError -- they are not backend failures."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_query", ["", "   ", None])
    async def test_empty_query_rejected(self, v2_client, bad_query):
        with pytest.raises(ValueError, match="non-empty string"):
            await v2_client.query_v2("u", query=bad_query, corpora="a")

    @pytest.mark.asyncio
    async def test_unknown_mode_rejected(self, v2_client):
        with pytest.raises(ValueError, match="mode must be one of"):
            await v2_client.query_v2("u", query="q", corpora="a", mode="summary")

    @pytest.mark.asyncio
    async def test_empty_corpora_rejected(self, v2_client):
        with pytest.raises(ValueError, match="at least one corpus"):
            await v2_client.query_v2("u", query="q", corpora=[])


class TestV2RawMode:
    """``raw`` returns evidence for our own LLM, so it is not a completion."""

    @pytest.mark.asyncio
    async def test_parses_hits_into_documents(self, v2_client):
        patcher, _ = _mock_post(RAW_RESPONSE)
        try:
            result = await v2_client.query_v2("u", query="q", corpora="company-policies")
        finally:
            patcher.stop()

        assert isinstance(result, RAGResponse)
        assert result.is_completion is False
        assert result.metadata is not None
        assert len(result.metadata.documents_found) == 2
        assert result.metadata.query_processing_time_ms == 128
        assert result.metadata.data_source_name == "company-policies"
        assert result.metadata.retrieval_method == "v2_raw"

        doc = result.metadata.documents_found[0]
        assert doc.title == "PTO Policy"
        assert doc.document_ref == 1
        assert doc.citation == "[1] PTO Policy, pto-policy.pdf"
        assert len(doc.sections) == 2
        # Confidence comes from the best-scoring section.
        assert doc.confidence_score == pytest.approx(0.91)

    @pytest.mark.asyncio
    async def test_content_is_an_evidence_block_with_markers(self, v2_client):
        patcher, _ = _mock_post(RAW_RESPONSE)
        try:
            result = await v2_client.query_v2("u", query="q", corpora="company-policies")
        finally:
            patcher.stop()

        assert "Retrieved 2 document(s)" in result.content
        # [N] markers are what the existing citation pipeline matches on.
        assert "[1] PTO Policy" in result.content
        assert "[2] Handbook" in result.content
        assert "Carry over up to 240 hours." in result.content
        assert "Excess hours are forfeited." in result.content

    @pytest.mark.asyncio
    async def test_no_hits_says_so(self, v2_client):
        patcher, _ = _mock_post({
            "query": "q",
            "mode": "raw",
            "results": {"hits": [], "stats": {"total_found": 0, "top_k": 4}},
            "metadata": {"response_time_ms": 9, "corpora_searched": ["a"]},
        })
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a")
        finally:
            patcher.stop()

        assert "No relevant documents" in result.content
        assert result.metadata.documents_found == []

    @pytest.mark.asyncio
    async def test_malformed_entries_are_skipped_not_fatal(self, v2_client):
        patcher, _ = _mock_post({
            "query": "q",
            "mode": "raw",
            "results": {
                "hits": [
                    "not-a-dict",
                    {
                        "document_ref": 1,
                        "filename": "ok.pdf",
                        "sections": ["not-a-dict", {"section_ref": 1, "text": "t", "relevance": 0.5}],
                    },
                ],
            },
            "metadata": {},
        })
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a")
        finally:
            patcher.stop()

        assert len(result.metadata.documents_found) == 1
        assert len(result.metadata.documents_found[0].sections) == 1


class TestV2SynthesizedMode:
    """``synthesized`` returns an answer, so it *is* a completion."""

    @pytest.mark.asyncio
    async def test_answer_and_citations(self, v2_client):
        patcher, _ = _mock_post(SYNTHESIZED_RESPONSE)
        try:
            result = await v2_client.query_v2(
                "u", query="q", corpora="company-policies", mode="synthesized",
            )
        finally:
            patcher.stop()

        assert result.is_completion is True
        assert result.content == "Employees may carry over up to 240 hours. [1]"
        assert len(result.metadata.documents_found) == 1
        assert result.metadata.documents_found[0].document_ref == 1
        assert result.metadata.documents_found[0].title == "PTO Policy"
        assert result.metadata.retrieval_method == "v2_synthesized"
        assert result.metadata.query_processing_time_ms == 305

    @pytest.mark.asyncio
    async def test_missing_answer_falls_back_to_placeholder(self, v2_client):
        patcher, _ = _mock_post({
            "query": "q", "mode": "synthesized", "results": {}, "metadata": {},
        })
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a", mode="synthesized")
        finally:
            patcher.stop()

        assert result.content == "No response from RAG system."

    @pytest.mark.asyncio
    async def test_mode_is_the_caller_s_not_the_server_s(self, v2_client):
        """A server echoing a different mode cannot reshape the response."""
        echoed_raw = dict(SYNTHESIZED_RESPONSE, mode="raw")
        patcher, _ = _mock_post(echoed_raw)
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a", mode="synthesized")
        finally:
            patcher.stop()

        assert result.is_completion is True
        assert result.content == "Employees may carry over up to 240 hours. [1]"


class TestV2HTTPErrors:
    """Backend failures map to HTTPException like they do on v1."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status,expected",
        [(400, 400), (403, 403), (404, 404), (500, 500), (503, 500)],
    )
    async def test_status_mapping(self, v2_client, status, expected):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.text = "boom"

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.HTTPStatusError(
                "err", request=MagicMock(), response=mock_response,
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(HTTPException) as exc_info:
                await v2_client.query_v2("u", query="q", corpora="a")

        assert exc_info.value.status_code == expected

    @pytest.mark.asyncio
    async def test_connection_error(self, v2_client):
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = httpx.RequestError("no route")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            with pytest.raises(HTTPException) as exc_info:
                await v2_client.query_v2("u", query="q", corpora="a")

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_non_dict_body(self, v2_client):
        patcher, _ = _mock_post(["unexpected"])
        try:
            with pytest.raises(HTTPException) as exc_info:
                await v2_client.query_v2("u", query="q", corpora="a")
        finally:
            patcher.stop()

        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# UnifiedRAGService routing
# ---------------------------------------------------------------------------

def _service(api_version="v2", default_mode="synthesized"):
    config_manager = MagicMock()
    config_manager.rag_sources_config = RAGSourcesConfig(
        sources={
            "v2_rag": RAGSourceConfig(
                type="http",
                url="https://rag.example.com",
                api_version=api_version,
                default_mode=default_mode,
                top_k=7,
            ),
            "v1_rag": RAGSourceConfig(type="http", url="https://old.example.com"),
        }
    )
    return UnifiedRAGService(config_manager=config_manager)


class TestUnifiedRAGServiceV2Routing:
    """Which contract a source speaks decides the request, not the caller."""

    @pytest.mark.asyncio
    async def test_v1_source_still_posts_messages(self):
        service = _service()
        client = AsyncMock()
        client.query_rag.return_value = RAGResponse(content="v1", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            result = await service.query_rag(
                "alice@corp.com", "v1_rag:docs", [{"role": "user", "content": "hi"}],
            )

        assert result.content == "v1"
        client.query_rag.assert_awaited_once()
        client.query_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_v2_source_sends_explicit_query(self):
        service = _service()
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            result = await service.query_rag(
                "alice@corp.com",
                "v2_rag:docs",
                [{"role": "user", "content": "ignored"}],
                query="the real question",
                mode="raw",
            )

        assert result.content == "v2"
        client.query_rag.assert_not_awaited()
        kwargs = client.query_v2.call_args[1]
        assert kwargs["query"] == "the real question"
        assert kwargs["corpora"] == "docs"
        assert kwargs["mode"] == "raw"
        assert kwargs["top_k"] == 7

    @pytest.mark.asyncio
    async def test_v2_derives_query_from_messages_when_not_given(self):
        """Callers that have no explicit query keep working (v1 derivation)."""
        service = _service()
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            await service.query_rag(
                "alice@corp.com",
                "v2_rag:docs",
                [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "last question"},
                ],
            )

        kwargs = client.query_v2.call_args[1]
        assert kwargs["query"] == "last question"
        # No mode given -> the source's configured default.
        assert kwargs["mode"] == "synthesized"

    @pytest.mark.asyncio
    async def test_default_mode_is_configurable_per_source(self):
        service = _service(default_mode="raw")
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            await service.query_rag(
                "alice@corp.com", "v2_rag:docs", [{"role": "user", "content": "q"}],
            )

        assert client.query_v2.call_args[1]["mode"] == "raw"

    @pytest.mark.asyncio
    async def test_batch_sends_all_corpora_in_one_v2_request(self):
        service = _service()
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            await service.query_rag_batch(
                "alice@corp.com",
                ["v2_rag:alpha", "v2_rag:beta"],
                [{"role": "user", "content": "q"}],
                query="explicit",
                mode="raw",
            )

        kwargs = client.query_v2.call_args[1]
        assert kwargs["corpora"] == ["alpha", "beta"]
        assert kwargs["query"] == "explicit"
        assert kwargs["mode"] == "raw"

    @pytest.mark.asyncio
    async def test_client_is_built_with_the_configured_contract(self):
        service = _service()
        config = service.config_manager.rag_sources_config.sources["v2_rag"]
        client = service._get_http_client("v2_rag", config)

        assert client.api_version == "v2"
        assert client.query_path == "/api/v2/rag/query"
