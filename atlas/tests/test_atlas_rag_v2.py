"""Tests for the v2 query-oriented RAG interface (OpenAPI v0.8.0).

v2 sends an explicit ``query`` plus ``search_kwargs`` instead of the
conversation. The backend always returns a synthesized ``response`` string
and the ``references`` behind it; ``mode`` is a client-side knob that decides
whether ATLAS uses the response verbatim (``synthesized``) or rebuilds an
evidence block from the reference snippets (``raw``). These tests pin the
request body (what leaves the process), the response parsing, and the routing
decision that sends a source to v1 or v2.
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


# The v0.8.0 wire response is the same regardless of how the caller uses it --
# the backend always returns a synthesized ``response`` plus ``references``.
# ``mode`` (raw vs synthesized) is a client-side interpretation knob.
V2_RESPONSE = {
    "response": "Employees may carry over up to 240 hours. [1]",
    "metadata": {
        "response_time": 2,
        "references": [
            {
                "filename": "pto-policy.pdf",
                "sections": [
                    {"text": "Carry over up to 240 hours.", "relevance": 0.91},
                    {"text": "Excess hours are forfeited.", "relevance": 0.55},
                ],
                "reference": "PTO Policy, pto-policy.pdf",
            },
            {
                "filename": "handbook.pdf",
                "sections": [
                    {"text": "See the PTO policy.", "relevance": 0.42},
                ],
                "reference": "Handbook, handbook.pdf",
            },
        ],
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
    async def test_sends_query_and_search_kwargs_not_messages(self, v2_client):
        patcher, mock_instance = _mock_post(V2_RESPONSE)
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
        assert payload["query"] == "What is the PTO carryover limit?"
        assert payload["corpora"] == "company-policies"
        # top_k is mapped into search_kwargs.top_k_final, not sent at top level.
        assert payload["search_kwargs"] == {"top_k_final": 5}
        # mode is client-side only -- never on the wire.
        assert "mode" not in payload
        # The whole point of v2: no conversation history on the wire.
        assert "messages" not in payload

    @pytest.mark.asyncio
    async def test_top_k_defaults_to_client_config(self, v2_client):
        patcher, mock_instance = _mock_post(V2_RESPONSE)
        try:
            await v2_client.query_v2("u", query="q", corpora=["a", "b"])
        finally:
            patcher.stop()

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["search_kwargs"]["top_k_final"] == 4
        assert payload["corpora"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_explicit_search_kwargs_forwarded(self, v2_client):
        patcher, mock_instance = _mock_post(V2_RESPONSE)
        try:
            await v2_client.query_v2(
                "u",
                query="q",
                corpora="a",
                search_kwargs={"top_k_final": 3, "rerank": False, "threshold": 0.5},
            )
        finally:
            patcher.stop()

        payload = mock_instance.post.call_args[1]["json"]
        assert payload["search_kwargs"] == {"top_k_final": 3, "rerank": False, "threshold": 0.5}

    @pytest.mark.asyncio
    async def test_filters_and_synthesis_params_not_sent(self, v2_client):
        """The v0.8.0 schema has no filters/synthesis_params fields."""
        patcher, mock_instance = _mock_post(V2_RESPONSE)
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
        assert "filters" not in payload
        assert "synthesis_params" not in payload

    @pytest.mark.asyncio
    async def test_strip_domain_applies(self):
        client = AtlasRAGClient(
            base_url="https://rag-api.example.com", api_version="v2", strip_domain=True,
        )
        patcher, mock_instance = _mock_post(V2_RESPONSE)
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
    """``raw`` builds an evidence block from references, so it is not a completion."""

    @pytest.mark.asyncio
    async def test_parses_references_into_documents(self, v2_client):
        patcher, _ = _mock_post(V2_RESPONSE)
        try:
            result = await v2_client.query_v2("u", query="q", corpora="company-policies")
        finally:
            patcher.stop()

        assert isinstance(result, RAGResponse)
        assert result.is_completion is False
        assert result.metadata is not None
        assert len(result.metadata.documents_found) == 2
        # response_time is in seconds (2); surfaced as ms for the UI footer.
        assert result.metadata.query_processing_time_ms == 2000
        assert result.metadata.data_source_name == "company-policies"
        assert result.metadata.retrieval_method == "v2_raw"

        doc = result.metadata.documents_found[0]
        # The ``reference`` label becomes the title; ``filename`` is on source.
        assert doc.title == "PTO Policy, pto-policy.pdf"
        assert doc.source == "company-policies"
        assert doc.document_ref is None  # v0.8.0 has no document_ref
        assert len(doc.sections) == 2
        # Confidence comes from the best-scoring section.
        assert doc.confidence_score == pytest.approx(0.91)

    @pytest.mark.asyncio
    async def test_content_is_an_evidence_block_with_markers(self, v2_client):
        patcher, _ = _mock_post(V2_RESPONSE)
        try:
            result = await v2_client.query_v2("u", query="q", corpora="company-policies")
        finally:
            patcher.stop()

        assert "Retrieved 2 document(s)" in result.content
        # [N] markers are what the existing citation pipeline matches on.
        # The v0.8.0 schema has no document_ref, so references are numbered
        # sequentially.
        assert "[1] PTO Policy, pto-policy.pdf" in result.content
        assert "[2] Handbook, handbook.pdf" in result.content
        assert "Carry over up to 240 hours." in result.content
        assert "Excess hours are forfeited." in result.content

    @pytest.mark.asyncio
    async def test_no_references_says_so(self, v2_client):
        patcher, _ = _mock_post({
            "response": "No results found.",
            "metadata": {"response_time": 1, "references": []},
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
            "response": "ok",
            "metadata": {
                "response_time": 1,
                "references": [
                    "not-a-dict",
                    {
                        "filename": "ok.pdf",
                        "sections": ["not-a-dict", {"text": "t", "relevance": 0.5}],
                        "reference": "OK Doc, ok.pdf",
                    },
                ],
            },
        })
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a")
        finally:
            patcher.stop()

        assert len(result.metadata.documents_found) == 1
        assert len(result.metadata.documents_found[0].sections) == 1


class TestV2SynthesizedMode:
    """``synthesized`` uses the backend's response verbatim, so it is a completion."""

    @pytest.mark.asyncio
    async def test_answer_uses_response_field(self, v2_client):
        patcher, _ = _mock_post(V2_RESPONSE)
        try:
            result = await v2_client.query_v2(
                "u", query="q", corpora="company-policies", mode="synthesized",
            )
        finally:
            patcher.stop()

        assert result.is_completion is True
        assert result.content == "Employees may carry over up to 240 hours. [1]"
        assert len(result.metadata.documents_found) == 2
        assert result.metadata.documents_found[0].title == "PTO Policy, pto-policy.pdf"
        assert result.metadata.retrieval_method == "v2_synthesized"
        assert result.metadata.query_processing_time_ms == 2000

    @pytest.mark.asyncio
    async def test_missing_response_falls_back_to_placeholder(self, v2_client):
        patcher, _ = _mock_post({
            "response": "",
            "metadata": {"response_time": 0, "references": None},
        })
        try:
            result = await v2_client.query_v2("u", query="q", corpora="a", mode="synthesized")
        finally:
            patcher.stop()

        assert result.content == "No response from RAG system."


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
        assert kwargs["search_kwargs"] is None

    @pytest.mark.asyncio
    async def test_v2_search_kwargs_keep_the_configured_top_k_as_a_floor(self):
        """A caller tuning ``depth`` must not silently drop the source's top_k.

        ``query_v2`` treats an explicit ``search_kwargs`` as the whole block, so
        a depth-only dict would otherwise replace the configured ``top_k_final``
        with the backend default.
        """
        service = _service()
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            await service.query_rag(
                "alice@corp.com",
                "v2_rag:docs",
                [{"role": "user", "content": "ignored"}],
                query="the real question",
                search_kwargs={"rerank": False},
            )

        sent = client.query_v2.call_args[1]["search_kwargs"]
        assert sent == {"top_k_final": 7, "rerank": False}

    @pytest.mark.asyncio
    async def test_v2_search_kwargs_top_k_wins_over_the_source_config(self):
        service = _service()
        client = AsyncMock()
        client.query_v2.return_value = RAGResponse(content="v2", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            await service.query_rag(
                "alice@corp.com",
                "v2_rag:docs",
                [{"role": "user", "content": "ignored"}],
                query="the real question",
                search_kwargs={"top_k_final": 3},
            )

        assert client.query_v2.call_args[1]["search_kwargs"] == {"top_k_final": 3}

    @pytest.mark.asyncio
    async def test_v1_ignores_search_kwargs(self):
        """v1 has no ``search_kwargs`` on the wire; asking for more is a no-op."""
        service = _service()
        client = AsyncMock()
        client.query_rag.return_value = RAGResponse(content="v1", metadata=None)

        with patch.object(service, "_get_http_client", return_value=client):
            result = await service.query_rag(
                "alice@corp.com",
                "v1_rag:docs",
                [{"role": "user", "content": "the question"}],
                search_kwargs={"top_k_final": 25},
            )

        assert result.content == "v1"
        client.query_v2.assert_not_awaited()
        assert "search_kwargs" not in client.query_rag.call_args[1]

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


class TestToolResultReferences:
    """A RAG tool result must name its sources, not just carry prose.

    The agent loop forwards only ``ToolResult.content``, so document identity
    that lives on ``RAGResponse.metadata`` has to be folded into the payload
    or the model (and the references UI) never sees it.
    """

    def test_references_are_extracted_from_metadata(self):
        from atlas.modules.mcp_tools.mcp_execution import _tool_references
        from atlas.modules.rag.client import DocumentMetadata, RAGMetadata

        response = RAGResponse(
            content="answer",
            metadata=RAGMetadata(
                query_processing_time_ms=1,
                total_documents_searched=1,
                documents_found=[
                    DocumentMetadata(
                        source="corpus",
                        content_type="atlas-search",
                        confidence_score=0.9,
                        title="PTO Policy",
                        citation="[1] PTO Policy",
                        document_ref=1,
                        url="https://example.com/pto",
                    ),
                ],
                data_source_name="corpus",
                retrieval_method="v2_synthesized",
            ),
        )

        # Unregistered (no turn, so no register): the identity list comes back
        # as it always did, and there is nothing to renumber.
        references, renumbering = _tool_references(response)
        assert references == [
            {
                "document_ref": 1,
                "filename": "PTO Policy",
                "citation": "[1] PTO Policy",
                "url": "https://example.com/pto",
            }
        ]
        assert renumbering == []

    def test_no_metadata_yields_no_references(self):
        from atlas.modules.mcp_tools.mcp_execution import _tool_references

        assert _tool_references(RAGResponse(content="answer", metadata=None)) == ([], [])
        assert _tool_references(object()) == ([], [])

    def test_reference_count_is_capped(self):
        from atlas.modules.mcp_tools.mcp_execution import (
            _MAX_TOOL_REFERENCES,
            _tool_references,
        )
        from atlas.modules.rag.client import DocumentMetadata, RAGMetadata

        docs = [
            DocumentMetadata(
                source="corpus",
                content_type="atlas-search",
                confidence_score=0.5,
                title=f"doc-{i}",
                document_ref=i,
            )
            for i in range(_MAX_TOOL_REFERENCES + 10)
        ]
        response = RAGResponse(
            content="answer",
            metadata=RAGMetadata(
                query_processing_time_ms=1,
                total_documents_searched=len(docs),
                documents_found=docs,
                data_source_name="corpus",
                retrieval_method="v2_raw",
            ),
        )

        assert len(_tool_references(response)[0]) == _MAX_TOOL_REFERENCES
