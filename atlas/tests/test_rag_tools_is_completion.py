"""Tests for RAG+tools is_completion handling (PR #389).

When both RAG and tools are active and RAG returns is_completion=True,
the pre-synthesized content must be injected as context so the LLM is
still called with tools available. It must NOT short-circuit.

The RAG-only path (call_with_rag, no tools) should still return directly.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.llm.models import LLMResponse
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


# -- call_with_rag_and_tools -------------------------------------------------

@pytest.mark.asyncio
async def test_is_completion_does_not_bypass_tools():
    """When is_completion=True in RAG+tools path, LLM must still be called."""
    caller = _make_caller()

    rag_resp = _rag_response("Pre-synthesized answer", is_completion=True)
    caller._query_all_rag_sources = AsyncMock(
        return_value=[("test-source", rag_resp)]
    )

    expected_llm_response = LLMResponse(content="LLM used tools and context")
    caller.call_with_tools = AsyncMock(return_value=expected_llm_response)

    result = await caller.call_with_rag_and_tools(
        model_name="test-model",
        messages=[{"role": "user", "content": "hello"}],
        data_sources=["source1"],
        tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
        user_email="test@example.com",
    )

    # LLM with tools must have been called (not short-circuited)
    caller.call_with_tools.assert_awaited_once()

    # The result should be from the LLM, not the raw RAG response
    assert result.content == "LLM used tools and context"

    # Verify RAG content was injected as context in the messages
    call_args = caller.call_with_tools.call_args
    messages_passed = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("messages")
    rag_context_msgs = [m for m in messages_passed if "Pre-synthesized answer from" in m.get("content", "")]
    assert len(rag_context_msgs) == 1, "RAG completion should be injected as context message"


@pytest.mark.asyncio
async def test_non_completion_rag_with_tools_uses_retrieved_context_label():
    """When is_completion=False in RAG+tools, label should say 'Retrieved context'."""
    caller = _make_caller()

    rag_resp = _rag_response("Some raw context", is_completion=False)
    caller._query_all_rag_sources = AsyncMock(
        return_value=[("test-source", rag_resp)]
    )

    expected_llm_response = LLMResponse(content="LLM response")
    caller.call_with_tools = AsyncMock(return_value=expected_llm_response)

    await caller.call_with_rag_and_tools(
        model_name="test-model",
        messages=[{"role": "user", "content": "hello"}],
        data_sources=["source1"],
        tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
        user_email="test@example.com",
    )

    call_args = caller.call_with_tools.call_args
    messages_passed = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("messages")
    context_msgs = [m for m in messages_passed if "Retrieved context from" in m.get("content", "")]
    assert len(context_msgs) == 1


# -- call_with_rag (RAG-only, no tools) -------------------------------------

@pytest.mark.asyncio
async def test_rag_only_is_completion_returns_directly():
    """RAG-only path should still return directly when is_completion=True."""
    caller = _make_caller()

    rag_resp = _rag_response("Direct RAG answer", is_completion=True)
    caller._query_all_rag_sources = AsyncMock(
        return_value=[("test-source", rag_resp)]
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


# -- stream_with_rag_and_tools ----------------------------------------------

@pytest.mark.asyncio
async def test_streaming_is_completion_does_not_bypass_tools():
    """Streaming: is_completion=True in RAG+tools must still call LLM with tools."""
    caller = _make_caller()

    rag_resp = _rag_response("Streamed pre-synth", is_completion=True)
    caller._query_all_rag_sources = AsyncMock(
        return_value=[("test-source", rag_resp)]
    )

    # Mock stream_with_tools as an async generator
    async def mock_stream_with_tools(*args, **kwargs):
        yield LLMResponse(content="streamed token")

    caller.stream_with_tools = mock_stream_with_tools

    chunks = []
    async for chunk in caller.stream_with_rag_and_tools(
        model_name="test-model",
        messages=[{"role": "user", "content": "hello"}],
        data_sources=["source1"],
        tools_schema=[{"type": "function", "function": {"name": "test_tool"}}],
        user_email="test@example.com",
    ):
        chunks.append(chunk)

    # Should have gotten chunks from stream_with_tools, not a single RAG response
    assert len(chunks) >= 1
    assert chunks[0].content == "streamed token"
