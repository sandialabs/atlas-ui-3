"""Tests for RAG-mode ``is_completion`` handling.

A source that returns a pre-synthesized answer short-circuits the RAG-only
path: the answer is the response, so there is nothing left for the LLM to do.

The RAG+tools variants of these tests went away with the silent injection they
covered -- search is now an explicit ``atlas_search`` tool call, and a
pre-synthesized answer comes back as that call's tool result.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.rag.client import RAGResponse

# -- Helpers -----------------------------------------------------------------

def _make_caller():
    """Build a minimal LiteLLMCaller with mocked internals."""
    # Import here so the test file stays light on top-level deps
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller._rag_service = MagicMock()
    caller._llm_config = MagicMock()
    caller._model_configs = {}
    return caller


def _rag_response(content="RAG answer", is_completion=False):
    return RAGResponse(content=content, is_completion=is_completion)


# -- call_with_rag (RAG-only, no tools) -------------------------------------

@pytest.mark.asyncio
async def test_rag_only_is_completion_returns_directly():
    """RAG-only path should still return directly when is_completion=True."""
    caller = _make_caller()

    rag_resp = _rag_response("Direct RAG answer", is_completion=True)
    caller._query_all_rag_sources = AsyncMock(
        return_value=([("test-source", rag_resp)], [], [])
    )

    # Mock call_plain to track if LLM is called (it should NOT be)
    caller.call_plain = AsyncMock(return_value="should not be called")

    result = await caller.call_with_rag(
        model_name="test-model",
        messages=[{"role": "user", "content": "hello"}],
        data_sources=["source1"],
        user_email="test@example.com",
    )

    # RAG-only path should NOT call the LLM when is_completion=True
    caller.call_plain.assert_not_awaited()

    # Result should contain RAG content directly
    assert "Direct RAG answer" in result
