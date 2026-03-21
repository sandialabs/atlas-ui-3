"""Integration tests for RAG citation output from LiteLLMCaller."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.rag.client import DocumentMetadata, RAGMetadata, RAGResponse
from atlas.modules.rag.citation_formatter import extract_references_json


def _make_caller():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller._rag_service = MagicMock()
    caller.llm_config = MagicMock()
    caller.llm_config.models = {}
    return caller


def _rag_response_with_metadata(content="Retrieved docs", is_completion=False):
    meta = RAGMetadata(
        query_processing_time_ms=100,
        total_documents_searched=50,
        documents_found=[
            DocumentMetadata(source="manual.pdf", content_type="pdf", confidence_score=0.9),
            DocumentMetadata(source="guide.docx", content_type="docx", confidence_score=0.75),
        ],
        data_source_name="test-source",
        retrieval_method="similarity",
    )
    return RAGResponse(content=content, metadata=meta, is_completion=is_completion)


@pytest.mark.asyncio
async def test_call_with_rag_appends_references_marker():
    """call_with_rag should append a RAG_REFERENCES_JSON marker when metadata exists."""
    caller = _make_caller()
    rag_resp = _rag_response_with_metadata()

    caller._query_all_rag_sources = AsyncMock(return_value=[("test-source", rag_resp)])
    caller.call_plain = AsyncMock(return_value="LLM synthesized answer with citations [1].")

    result = await caller.call_with_rag(
        model_name="test-model",
        messages=[{"role": "user", "content": "How does X work?"}],
        data_sources=["test:test-source"],
        user_email="user@example.com",
    )

    assert "RAG_REFERENCES_JSON" in result
    clean, refs = extract_references_json(result)
    assert refs is not None
    assert len(refs["sources"]) == 2
    assert refs["sources"][0]["source"] == "manual.pdf"


@pytest.mark.asyncio
async def test_call_with_rag_citation_instructions_in_system_message():
    """The RAG context message should include citation instructions."""
    caller = _make_caller()
    rag_resp = _rag_response_with_metadata()

    caller._query_all_rag_sources = AsyncMock(return_value=[("test-source", rag_resp)])

    captured_messages = []

    async def capture_call_plain(model, messages, **kwargs):
        captured_messages.extend(messages)
        return "Answer"

    caller.call_plain = AsyncMock(side_effect=capture_call_plain)

    await caller.call_with_rag(
        model_name="test-model",
        messages=[{"role": "user", "content": "question"}],
        data_sources=["test:test-source"],
        user_email="user@example.com",
    )

    # Find the RAG context system message
    rag_msgs = [
        m
        for m in captured_messages
        if m["role"] == "system" and "Retrieved context" in m.get("content", "")
    ]
    assert len(rag_msgs) == 1
    assert "[1]" in rag_msgs[0]["content"]
    assert "cite" in rag_msgs[0]["content"].lower()


@pytest.mark.asyncio
async def test_call_with_rag_completion_includes_marker():
    """Direct RAG completions (is_completion=True) should also include the marker."""
    caller = _make_caller()
    rag_resp = _rag_response_with_metadata(content="Direct answer", is_completion=True)

    caller._query_all_rag_sources = AsyncMock(return_value=[("test-source", rag_resp)])

    result = await caller.call_with_rag(
        model_name="test-model",
        messages=[{"role": "user", "content": "question"}],
        data_sources=["test:test-source"],
        user_email="user@example.com",
    )

    assert "RAG_REFERENCES_JSON" in result
    clean, refs = extract_references_json(result)
    assert refs is not None


@pytest.mark.asyncio
async def test_call_with_rag_no_metadata_no_marker():
    """When RAG has no metadata, no references marker should be appended."""
    caller = _make_caller()
    rag_resp = RAGResponse(content="Answer without metadata", metadata=None, is_completion=False)

    caller._query_all_rag_sources = AsyncMock(return_value=[("test-source", rag_resp)])
    caller.call_plain = AsyncMock(return_value="LLM answer")

    result = await caller.call_with_rag(
        model_name="test-model",
        messages=[{"role": "user", "content": "question"}],
        data_sources=["test:test-source"],
        user_email="user@example.com",
    )

    assert "RAG_REFERENCES_JSON" not in result
