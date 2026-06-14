"""Tests for reasoning_content support."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    from atlas.modules.llm.models import ReasoningBlock, ReasoningToken

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
    mixin._prepare_messages = lambda model_name, messages: messages
    mixin._raise_llm_domain_error = lambda exc: (_ for _ in ()).throw(exc)

    with patch('atlas.modules.llm.litellm_streaming.acompletion', side_effect=mock_acompletion):
        items = []
        async for item in mixin.stream_plain("test", [{"role": "user", "content": "q"}]):
            items.append(item)

    # First two items are ReasoningToken (one per chunk)
    assert isinstance(items[0], ReasoningToken)
    assert items[0].token == "Let me "
    assert isinstance(items[1], ReasoningToken)
    assert items[1].token == "think..."
    # Then the full ReasoningBlock
    assert isinstance(items[2], ReasoningBlock)
    assert items[2].content == "Let me think..."
    # Then content tokens
    assert items[3] == "The "
    assert items[4] == "answer"


@pytest.mark.asyncio
async def test_stream_with_tools_yields_reasoning_block():
    from atlas.modules.llm.litellm_streaming import LiteLLMStreamingMixin
    from atlas.modules.llm.models import LLMResponse, ReasoningBlock, ReasoningToken

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
    mixin._prepare_messages = lambda model_name, messages: messages
    mixin._raise_llm_domain_error = lambda exc: (_ for _ in ()).throw(exc)

    with patch('atlas.modules.llm.litellm_streaming.acompletion', side_effect=mock_acompletion):
        items = []
        async for item in mixin.stream_with_tools(
            "test", [{"role": "user", "content": "q"}],
            [{"type": "function", "function": {"name": "d"}}],
        ):
            items.append(item)

    # First: ReasoningToken
    assert isinstance(items[0], ReasoningToken)
    assert items[0].token == "Think..."
    # Then: ReasoningBlock with full text
    assert isinstance(items[1], ReasoningBlock)
    assert items[1].content == "Think..."
    # Then: content
    assert items[2] == "ans"
    # Then: final LLMResponse
    assert isinstance(items[3], LLMResponse)
    assert items[3].reasoning_content == "Think..."


@pytest.mark.asyncio
async def test_stream_and_accumulate_captures_reasoning():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate
    from atlas.modules.llm.models import ReasoningBlock

    async def gen():
        yield ReasoningBlock(content="thinking hard")
        yield "Hello "
        yield "world"

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.send_json = AsyncMock()

    text, reasoning = await stream_and_accumulate(
        token_generator=gen(), event_publisher=pub,
    )

    assert text == "Hello world"
    assert reasoning == "thinking hard"
    pub.send_json.assert_awaited_once_with({
        "type": "reasoning_content",
        "content": "thinking hard",
    })


@pytest.mark.asyncio
async def test_stream_and_accumulate_no_reasoning():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    async def gen():
        yield "Hello"

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.send_json = AsyncMock()

    text, reasoning = await stream_and_accumulate(
        token_generator=gen(), event_publisher=pub,
    )

    assert text == "Hello"
    assert reasoning is None
    pub.send_json.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_agentic_loop_handles_reasoning_block():
    """AgenticLoop._call_llm_streaming should emit reasoning event for ReasoningBlock."""
    from atlas.application.chat.agent.agentic_loop import AgenticLoop
    from atlas.modules.llm.models import ReasoningBlock

    async def mock_stream(*args, **kwargs):
        yield ReasoningBlock(content="Deep thoughts")
        yield "The "
        yield "answer"
        yield LLMResponse(content="The answer", reasoning_content="Deep thoughts")

    llm = MagicMock()
    llm.stream_with_tools = mock_stream

    loop = AgenticLoop.__new__(AgenticLoop)
    loop.llm = llm

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.send_json = AsyncMock()

    context = MagicMock()
    context.user_email = "test@test.com"

    result = await loop._call_llm_streaming(
        model="test", messages=[], tools_schema=[], data_sources=None,
        context=context, temperature=0.7, event_publisher=pub,
    )

    assert result.reasoning_content == "Deep thoughts"
    # Verify reasoning event was sent
    pub.send_json.assert_awaited_once_with({
        "type": "reasoning_content",
        "content": "Deep thoughts",
    })


@pytest.mark.asyncio
async def test_agentic_loop_run_captures_reasoning():
    """AgenticLoop.run() should include reasoning_content in AgentResult."""
    from atlas.application.chat.agent.protocols import AgentResult

    result = AgentResult(
        final_answer="answer",
        steps=1,
        metadata={"agent_mode": True},
        reasoning_content="thinking hard",
    )
    assert result.reasoning_content == "thinking hard"


@pytest.mark.asyncio
async def test_agent_result_reasoning_default_none():
    """AgentResult.reasoning_content defaults to None."""
    from atlas.application.chat.agent.protocols import AgentResult

    result = AgentResult(final_answer="answer", steps=1, metadata={})
    assert result.reasoning_content is None


# ---------------------------------------------------------------------------
# Monkey-patch: _inject_reasoning_content (vLLM `reasoning` -> reasoning_content)
# ---------------------------------------------------------------------------

def _get_inject_fn():
    """Return the module-level _inject_reasoning_content, or skip if the patch
    couldn't be installed (e.g. LiteLLM internals changed)."""
    import atlas.modules.llm.litellm_caller as mod
    fn = getattr(mod, "_inject_reasoning_content", None)
    if fn is None:
        pytest.skip("reasoning monkey-patch not installed in this environment")
    return fn


class _Delta:
    """Minimal stand-in for a LiteLLM/OpenAI streaming delta."""

    def __init__(self, reasoning=None, reasoning_content=None, extra=None):
        if reasoning is not None:
            self.reasoning = reasoning
        if reasoning_content is not None:
            self.reasoning_content = reasoning_content
        if extra is not None:
            self.__pydantic_extra__ = extra


def _chunk(delta):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_inject_reasoning_from_direct_attribute():
    inject = _get_inject_fn()
    delta = _Delta(reasoning="thinking hard")
    inject(_chunk(delta))
    assert delta.reasoning_content == "thinking hard"


def test_inject_reasoning_from_pydantic_extra():
    inject = _get_inject_fn()
    delta = _Delta(extra={"reasoning": "from extra"})
    inject(_chunk(delta))
    assert delta.reasoning_content == "from extra"


def test_inject_does_not_overwrite_existing_reasoning_content():
    inject = _get_inject_fn()
    delta = _Delta(reasoning="new", reasoning_content="already set")
    inject(_chunk(delta))
    assert delta.reasoning_content == "already set"


def test_inject_noop_when_no_reasoning():
    inject = _get_inject_fn()
    delta = _Delta()
    inject(_chunk(delta))
    assert getattr(delta, "reasoning_content", None) is None


def test_inject_handles_empty_choices():
    inject = _get_inject_fn()
    # No choices and None choices must both be no-ops (no exception).
    inject(SimpleNamespace(choices=[]))
    inject(SimpleNamespace(choices=None))


def test_inject_swallows_assignment_error():
    inject = _get_inject_fn()

    class _RaisingDelta:
        reasoning = "thinking"

        @property
        def reasoning_content(self):
            return None

        @reasoning_content.setter
        def reasoning_content(self, value):
            raise RuntimeError("delta is frozen")

    # Should log+swallow, not raise.
    inject(_chunk(_RaisingDelta()))


# ---------------------------------------------------------------------------
# stream_and_accumulate: successful fallback must be a chat response, not error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_and_accumulate_publishes_successful_fallback_as_chat_response():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    async def failing_gen():
        raise RuntimeError("stream blew up")
        yield ""  # pragma: no cover - makes this an async generator

    publisher = MagicMock()
    publisher.send_json = AsyncMock()
    publisher.publish_token_stream = AsyncMock()
    publisher.publish_chat_response = AsyncMock()

    fallback = AsyncMock(return_value="recovered answer")

    accumulated, reasoning = await stream_and_accumulate(
        token_generator=failing_gen(),
        event_publisher=publisher,
        fallback_fn=fallback,
        context_label="test",
    )

    assert accumulated == "recovered answer"
    publisher.publish_chat_response.assert_awaited_once()
    assert publisher.publish_chat_response.call_args.kwargs["message"] == "recovered answer"
    # The recovered answer must NOT be surfaced as an error frame.
    error_sends = [
        c for c in publisher.send_json.call_args_list
        if c.args and c.args[0].get("type") == "error"
    ]
    assert error_sends == []


@pytest.mark.asyncio
async def test_stream_and_accumulate_sends_error_when_fallback_also_fails():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    async def failing_gen():
        raise RuntimeError("stream blew up")
        yield ""  # pragma: no cover

    publisher = MagicMock()
    publisher.send_json = AsyncMock()
    publisher.publish_token_stream = AsyncMock()
    publisher.publish_chat_response = AsyncMock()

    fallback = AsyncMock(side_effect=RuntimeError("fallback failed too"))

    await stream_and_accumulate(
        token_generator=failing_gen(),
        event_publisher=publisher,
        fallback_fn=fallback,
        context_label="test",
    )

    publisher.publish_chat_response.assert_not_awaited()
    error_sends = [
        c for c in publisher.send_json.call_args_list
        if c.args and c.args[0].get("type") == "error"
    ]
    assert len(error_sends) == 1


# ---------------------------------------------------------------------------
# stream_final_answer: agent fallback streaming must handle reasoning markers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_final_answer_handles_reasoning_markers():
    from atlas.application.chat.agent.streaming_final_answer import stream_final_answer
    from atlas.modules.llm.models import ReasoningToken

    async def gen(*args, **kwargs):
        yield ReasoningToken(token="step 1 ")
        yield ReasoningBlock(content="step 1 done")
        yield "final answer"

    llm = MagicMock()
    llm.stream_plain = gen
    llm.call_plain = AsyncMock()

    publisher = MagicMock()
    publisher.send_json = AsyncMock()
    publisher.publish_token_stream = AsyncMock()

    result = await stream_final_answer(
        llm=llm, event_publisher=publisher, model="m",
        messages=[{"role": "user", "content": "q"}],
        temperature=0.7, user_email=None,
    )

    assert result == "final answer"
    # Reasoning markers were forwarded as reasoning events...
    sent_types = [c.args[0]["type"] for c in publisher.send_json.call_args_list]
    assert "reasoning_token" in sent_types
    assert "reasoning_content" in sent_types
    # ...and only the text token was published to the answer stream.
    text_tokens = [
        c.kwargs.get("token") for c in publisher.publish_token_stream.call_args_list
        if c.kwargs.get("token")
    ]
    assert "final answer" in text_tokens
    # Non-streaming fallback should NOT have been needed.
    llm.call_plain.assert_not_awaited()
