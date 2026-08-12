"""Tests for where the RAG context message is inserted into the message list.

The RAG context belongs immediately before the user turn it was retrieved for.
The old ``insert(-1, ...)`` encoded that as "before the last message", which is
only the same thing when the conversation ends on that user message.

In a tool-calling continuation round the tail is
``assistant(tool_calls=[...]), tool, tool, ...``. Inserting before the last
message dropped a system message inside that block, orphaning a tool_call_id,
and OpenAI/Azure rejected the whole turn with "An assistant message with
'tool_calls' must be followed by tool messages responding to each
'tool_call_id'".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.models import LLMResponse
from atlas.modules.rag.client import RAGResponse


def _make_caller():
    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller._rag_service = MagicMock()
    caller._llm_config = MagicMock()
    caller._model_configs = {}
    caller._query_all_rag_sources = AsyncMock(
        return_value=([("test-source", RAGResponse(content="Some raw context"))], [])
    )
    return caller


def _continuation_messages():
    """Messages as tools mode builds them for a continuation round."""
    return [
        {"role": "user", "content": "find the policy and summarize it"},
        {
            "role": "assistant",
            "content": "looking it up",
            "tool_calls": [{
                "id": "call_slT5qS0Q05qPdumdHfhDZU4D",
                "type": "function",
                "function": {"name": "atlas_rag_query", "arguments": '{"query":"policy"}'},
            }],
        },
        {"role": "tool", "content": "policy text", "tool_call_id": "call_slT5qS0Q05qPdumdHfhDZU4D"},
    ]


def _assert_tool_calls_answered(messages):
    """Every assistant tool_calls block is immediately followed by its replies."""
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        answered = set()
        for follow in messages[i + 1:]:
            if follow.get("role") != "tool":
                break
            answered.add(follow.get("tool_call_id"))
        expected = {tc["id"] for tc in msg["tool_calls"]}
        assert expected <= answered, (
            f"tool_call_ids {expected - answered} have no adjacent tool response"
        )


# -- _rag_insert_index -------------------------------------------------------

def test_insert_index_targets_last_user_message():
    messages = _continuation_messages()
    assert LiteLLMCaller._rag_insert_index(messages) == 0


def test_insert_index_matches_old_behavior_on_a_plain_turn():
    """Round 0 ends on the user message: unchanged from the previous insert(-1)."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert LiteLLMCaller._rag_insert_index(messages) == len(messages) - 1


def test_insert_index_appends_when_there_is_no_user_message():
    messages = [{"role": "system", "content": "sys"}]
    assert LiteLLMCaller._rag_insert_index(messages) == 1


# -- Non-streaming RAG + tools ----------------------------------------------

@pytest.mark.asyncio
async def test_call_with_rag_and_tools_keeps_tool_block_contiguous():
    caller = _make_caller()
    caller.call_with_tools = AsyncMock(return_value=LLMResponse(content="ok"))

    await caller.call_with_rag_and_tools(
        model_name="test-model",
        messages=_continuation_messages(),
        data_sources=["source1"],
        tools_schema=[{"type": "function", "function": {"name": "atlas_rag_query"}}],
        user_email="test@example.com",
    )

    sent = caller.call_with_tools.call_args[0][1]
    _assert_tool_calls_answered(sent)
    # The context still precedes the user turn it was retrieved for.
    assert sent[0]["role"] == "system"
    assert "Retrieved context" in sent[0]["content"]
    assert sent[1]["role"] == "user"


# -- Streaming RAG + tools ---------------------------------------------------

@pytest.mark.asyncio
async def test_stream_with_rag_and_tools_keeps_tool_block_contiguous():
    caller = _make_caller()
    seen = {}

    async def _stream_with_tools(model_name, messages, tools_schema, tool_choice="auto",
                                 temperature=0.7, user_email=None):
        seen["messages"] = messages
        yield LLMResponse(content="ok")

    caller.stream_with_tools = _stream_with_tools

    stream = caller.stream_with_rag_and_tools(
        model_name="test-model",
        messages=_continuation_messages(),
        data_sources=["source1"],
        tools_schema=[{"type": "function", "function": {"name": "atlas_rag_query"}}],
        user_email="test@example.com",
    )
    async for _ in stream:
        pass

    _assert_tool_calls_answered(seen["messages"])
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][1]["role"] == "user"
