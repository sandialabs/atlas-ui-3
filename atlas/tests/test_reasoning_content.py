"""Tests for reasoning_content support."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.modules.llm.models import LLMResponse, ReasoningBlock


def test_llm_response_reasoning_content_default_none():
    resp = LLMResponse(content="Hello")
    assert resp.reasoning_content is None


def test_llm_response_reasoning_content_set():
    resp = LLMResponse(content="Hello", reasoning_content="thinking")
    assert resp.reasoning_content == "thinking"


def test_reasoning_block():
    block = ReasoningBlock(content="I considered X and Y")
    assert block.content == "I considered X and Y"


def _make_litellm_response(content="Hello", reasoning_content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning_content is not None:
        message.reasoning_content = reasoning_content
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.mark.asyncio
async def test_call_with_tools_extracts_reasoning():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    mock_resp = _make_litellm_response(content="answer", reasoning_content="thinking")
    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = MagicMock()
    caller.llm_config.models = {"m": MagicMock(
        model_name="m", model_url="https://openrouter.ai/api/v1",
        api_key="k", max_tokens=100, temperature=0.7, extra_headers=None,
    )}

    with patch.object(caller, '_acompletion_with_retry', new_callable=AsyncMock, return_value=mock_resp), \
         patch.object(caller, '_get_litellm_model_name', return_value='openrouter/m'), \
         patch.object(caller, '_get_model_kwargs', return_value={'max_tokens': 100, 'temperature': 0.7}), \
         patch.object(caller, '_sanitize_messages', side_effect=lambda m: m):
        result = await caller.call_with_tools("m", [{"role": "user", "content": "q"}],
                                               [{"type": "function", "function": {"name": "d"}}])
    assert result.reasoning_content == "thinking"


@pytest.mark.asyncio
async def test_call_with_tools_reasoning_none_when_absent():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    mock_resp = _make_litellm_response(content="answer")
    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = MagicMock()
    caller.llm_config.models = {"m": MagicMock(
        model_name="m", model_url="https://openrouter.ai/api/v1",
        api_key="k", max_tokens=100, temperature=0.7, extra_headers=None,
    )}

    with patch.object(caller, '_acompletion_with_retry', new_callable=AsyncMock, return_value=mock_resp), \
         patch.object(caller, '_get_litellm_model_name', return_value='openrouter/m'), \
         patch.object(caller, '_get_model_kwargs', return_value={'max_tokens': 100, 'temperature': 0.7}), \
         patch.object(caller, '_sanitize_messages', side_effect=lambda m: m):
        result = await caller.call_with_tools("m", [{"role": "user", "content": "q"}],
                                               [{"type": "function", "function": {"name": "d"}}])
    assert result.reasoning_content is None


@pytest.mark.asyncio
async def test_stream_plain_yields_reasoning_block():
    from atlas.modules.llm.litellm_streaming import LiteLLMStreamingMixin
    from atlas.modules.llm.models import ReasoningBlock

    chunks = []
    for text in ["Let me ", "think..."]:
        delta = SimpleNamespace(content=None)
        delta.reasoning_content = text
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))
    for text in ["The ", "answer"]:
        delta = SimpleNamespace(content=text)
        delta.reasoning_content = None
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))

    async def mock_acompletion(**kwargs):
        async def gen():
            for c in chunks:
                yield c
        return gen()

    mixin = LiteLLMStreamingMixin()
    mixin._get_litellm_model_name = lambda m: m
    mixin._get_model_kwargs = lambda m, t, user_email=None: {"max_tokens": 100, "temperature": 0.7}
    mixin._sanitize_messages = lambda m: m

    with patch('atlas.modules.llm.litellm_streaming.acompletion', side_effect=mock_acompletion):
        items = []
        async for item in mixin.stream_plain("test", [{"role": "user", "content": "q"}]):
            items.append(item)

    assert isinstance(items[0], ReasoningBlock)
    assert items[0].content == "Let me think..."
    assert items[1] == "The "
    assert items[2] == "answer"


@pytest.mark.asyncio
async def test_stream_with_tools_yields_reasoning_block():
    from atlas.modules.llm.litellm_streaming import LiteLLMStreamingMixin
    from atlas.modules.llm.models import ReasoningBlock, LLMResponse

    chunks = []
    for text in ["Think..."]:
        delta = SimpleNamespace(content=None, reasoning_content=text)
        delta.tool_calls = None
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))
    for text in ["ans"]:
        delta = SimpleNamespace(content=text, reasoning_content=None)
        delta.tool_calls = None
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))

    async def mock_acompletion(**kwargs):
        async def gen():
            for c in chunks:
                yield c
        return gen()

    mixin = LiteLLMStreamingMixin()
    mixin._get_litellm_model_name = lambda m: m
    mixin._get_model_kwargs = lambda m, t, user_email=None: {"max_tokens": 100, "temperature": 0.7}
    mixin._sanitize_messages = lambda m: m

    with patch('atlas.modules.llm.litellm_streaming.acompletion', side_effect=mock_acompletion):
        items = []
        async for item in mixin.stream_with_tools(
            "test", [{"role": "user", "content": "q"}],
            [{"type": "function", "function": {"name": "d"}}],
        ):
            items.append(item)

    assert isinstance(items[0], ReasoningBlock)
    assert items[0].content == "Think..."
    assert items[1] == "ans"
    assert isinstance(items[2], LLMResponse)
    assert items[2].reasoning_content == "Think..."


@pytest.mark.asyncio
async def test_notify_chat_response_includes_reasoning():
    from atlas.application.chat.utilities.event_notifier import notify_chat_response

    sent = []
    async def cb(payload): sent.append(payload)

    await notify_chat_response(message="ans", update_callback=cb, reasoning_content="thought")
    assert sent[0]["reasoning_content"] == "thought"


@pytest.mark.asyncio
async def test_notify_chat_response_omits_reasoning_when_none():
    from atlas.application.chat.utilities.event_notifier import notify_chat_response

    sent = []
    async def cb(payload): sent.append(payload)

    await notify_chat_response(message="ans", update_callback=cb)
    assert "reasoning_content" not in sent[0]
