"""Tests for reasoning_content support.

Covers the reasoning path end-to-end on the backend: the stream markers emitted
by the LiteLLM streaming mixin, the mode-runner handling that forwards them as
WebSocket events and persists them to message metadata, and the non-streaming
extraction from a provider response.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.modules.llm.models import LLMResponse, ReasoningBlock, ReasoningToken


# -- models ------------------------------------------------------------------


def test_llm_response_reasoning_content_default_none():
    assert LLMResponse(content="Hello").reasoning_content is None


def test_llm_response_reasoning_content_set():
    assert LLMResponse(content="Hello", reasoning_content="thinking").reasoning_content == "thinking"


def test_reasoning_markers_carry_text():
    assert ReasoningBlock(content="I considered X and Y").content == "I considered X and Y"
    assert ReasoningToken(token="chunk").token == "chunk"


# -- streaming mixin ---------------------------------------------------------


def _delta(content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content, reasoning_content=reasoning_content, tool_calls=tool_calls,
    )


def _chunks(*deltas):
    return [SimpleNamespace(choices=[SimpleNamespace(delta=d)]) for d in deltas]


def _make_mixin():
    from atlas.modules.llm.litellm_streaming import LiteLLMStreamingMixin

    mixin = LiteLLMStreamingMixin()
    mixin._get_litellm_model_name = lambda m: m
    mixin._get_model_kwargs = lambda m, t, user_email=None: {"max_tokens": 100, "temperature": 0.7}
    mixin._prepare_messages = lambda model_name, messages: messages
    mixin._raise_llm_domain_error = lambda exc, **kw: (_ for _ in ()).throw(exc)
    return mixin


def _mock_acompletion(chunks):
    async def _inner(**kwargs):
        async def gen():
            for c in chunks:
                yield c
        return gen()
    return _inner


async def _collect(agen):
    return [item async for item in agen]


@pytest.mark.asyncio
async def test_stream_plain_yields_tokens_then_block_then_content():
    chunks = _chunks(
        _delta(reasoning_content="Let me "),
        _delta(reasoning_content="think..."),
        _delta(content="The "),
        _delta(content="answer"),
    )
    mixin = _make_mixin()

    with patch('atlas.modules.llm.litellm_streaming.acompletion',
               side_effect=_mock_acompletion(chunks)):
        items = await _collect(mixin.stream_plain("test", [{"role": "user", "content": "q"}]))

    assert isinstance(items[0], ReasoningToken) and items[0].token == "Let me "
    assert isinstance(items[1], ReasoningToken) and items[1].token == "think..."
    # The block is emitted once, as soon as the first content token arrives.
    assert isinstance(items[2], ReasoningBlock)
    assert items[2].content == "Let me think..."
    assert items[3:] == ["The ", "answer"]


@pytest.mark.asyncio
async def test_stream_plain_emits_block_when_stream_is_reasoning_only():
    """A stream that ends without content must still emit the final block."""
    chunks = _chunks(_delta(reasoning_content="only thinking"))
    mixin = _make_mixin()

    with patch('atlas.modules.llm.litellm_streaming.acompletion',
               side_effect=_mock_acompletion(chunks)):
        items = await _collect(mixin.stream_plain("test", [{"role": "user", "content": "q"}]))

    assert isinstance(items[0], ReasoningToken)
    assert isinstance(items[-1], ReasoningBlock)
    assert items[-1].content == "only thinking"


@pytest.mark.asyncio
async def test_stream_plain_without_reasoning_yields_only_strings():
    chunks = _chunks(_delta(content="plain "), _delta(content="answer"))
    mixin = _make_mixin()

    with patch('atlas.modules.llm.litellm_streaming.acompletion',
               side_effect=_mock_acompletion(chunks)):
        items = await _collect(mixin.stream_plain("test", [{"role": "user", "content": "q"}]))

    assert items == ["plain ", "answer"]


@pytest.mark.asyncio
async def test_stream_with_tools_yields_reasoning_and_sets_it_on_response():
    chunks = _chunks(
        _delta(reasoning_content="I should call a tool"),
        _delta(tool_calls=[SimpleNamespace(
            index=0, id="call_1",
            function=SimpleNamespace(name="get_weather", arguments='{"city":"NYC"}'),
        )]),
    )
    mixin = _make_mixin()

    with patch('atlas.modules.llm.litellm_streaming.acompletion',
               side_effect=_mock_acompletion(chunks)), \
         patch('atlas.modules.llm.litellm_streaming.record_llm_call'):
        items = await _collect(mixin.stream_with_tools(
            "test", [{"role": "user", "content": "q"}],
            [{"type": "function", "function": {"name": "get_weather"}}],
        ))

    assert isinstance(items[0], ReasoningToken)
    # Model went reasoning -> tool calls with no content: block still emitted.
    assert isinstance(items[1], ReasoningBlock)
    assert items[1].content == "I should call a tool"
    final = items[-1]
    assert isinstance(final, LLMResponse)
    assert final.reasoning_content == "I should call a tool"
    assert final.has_tool_calls()


# -- non-streaming extraction ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning,expected", [("thinking", "thinking"), (None, None)])
async def test_call_with_tools_extracts_reasoning(reasoning, expected):
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    message = SimpleNamespace(content="answer", tool_calls=None, reasoning_content=reasoning)
    mock_resp = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = MagicMock()
    caller.llm_config.models = {"m": MagicMock(
        model_name="m", model_url="https://openrouter.ai/api/v1",
        api_key="k", max_tokens=100, temperature=0.7, extra_headers=None,
    )}

    with patch.object(caller, '_acompletion_with_retry', new_callable=AsyncMock, return_value=mock_resp), \
         patch.object(caller, '_get_litellm_model_name', return_value='openrouter/m'), \
         patch.object(caller, '_get_model_kwargs', return_value={'max_tokens': 100, 'temperature': 0.7}), \
         patch.object(caller, '_prepare_messages', side_effect=lambda mn, m: m):
        result = await caller.call_with_tools(
            "m", [{"role": "user", "content": "q"}],
            [{"type": "function", "function": {"name": "d"}}],
        )

    assert result.reasoning_content == expected


# -- stream_and_accumulate ---------------------------------------------------


class _RecordingPublisher:
    """Minimal EventPublisher capturing what the mode runners emit."""

    def __init__(self):
        self.json_events = []
        self.tokens = []
        self.chat_responses = []

    async def send_json(self, data):
        self.json_events.append(data)

    async def publish_token_stream(self, token, is_first=False, is_last=False):
        self.tokens.append((token, is_first, is_last))

    async def publish_chat_response(self, message, has_pending_tools=False, reasoning_content=None):
        self.chat_responses.append((message, reasoning_content))

    async def publish_response_complete(self):
        pass


async def _gen(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_stream_and_accumulate_forwards_reasoning_and_keeps_it_out_of_text():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    publisher = _RecordingPublisher()
    accumulated, reasoning = await stream_and_accumulate(
        token_generator=_gen([
            ReasoningToken(token="think "),
            ReasoningToken(token="hard"),
            ReasoningBlock(content="think hard"),
            "Hello ",
            "World",
        ]),
        event_publisher=publisher,
    )

    # Reasoning must never leak into the answer text.
    assert accumulated == "Hello World"
    assert reasoning == "think hard"
    assert publisher.json_events == [
        {"type": "reasoning_token", "token": "think "},
        {"type": "reasoning_token", "token": "hard"},
        {"type": "reasoning_content", "content": "think hard"},
    ]
    # Content tokens are still published normally.
    assert [t[0] for t in publisher.tokens if t[0]] == ["Hello ", "World"]


@pytest.mark.asyncio
async def test_stream_and_accumulate_reasoning_only_skips_fallback():
    """Reasoning with no content is legitimate; don't spend a second LLM call."""
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    publisher = _RecordingPublisher()
    fallback = AsyncMock(return_value="fallback text")

    accumulated, reasoning = await stream_and_accumulate(
        token_generator=_gen([ReasoningBlock(content="reasoned then stopped")]),
        event_publisher=publisher,
        fallback_fn=fallback,
    )

    fallback.assert_not_awaited()
    assert accumulated == ""
    assert reasoning == "reasoned then stopped"


@pytest.mark.asyncio
async def test_stream_and_accumulate_no_reasoning_returns_none():
    from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate

    publisher = _RecordingPublisher()
    accumulated, reasoning = await stream_and_accumulate(
        token_generator=_gen(["Hi"]),
        event_publisher=publisher,
    )

    assert accumulated == "Hi"
    assert reasoning is None
    assert publisher.json_events == []


# -- persistence to message metadata -----------------------------------------


@pytest.mark.asyncio
async def test_plain_mode_persists_reasoning_to_message_metadata():
    from atlas.application.chat.modes.plain import PlainModeRunner

    publisher = _RecordingPublisher()
    llm = MagicMock()
    llm.stream_plain = MagicMock(return_value=_gen([
        ReasoningToken(token="hmm"),
        ReasoningBlock(content="hmm"),
        "Answer",
    ]))
    llm.call_plain = AsyncMock(return_value="")

    runner = PlainModeRunner(llm=llm, event_publisher=publisher)
    session = MagicMock()
    added = []
    session.history.add_message = added.append

    await runner.run_streaming(
        session=session, model="m", messages=[{"role": "user", "content": "q"}],
    )

    assert len(added) == 1
    assert added[0].content == "Answer"
    assert added[0].metadata["reasoning_content"] == "hmm"


@pytest.mark.asyncio
async def test_plain_mode_omits_reasoning_key_when_absent():
    from atlas.application.chat.modes.plain import PlainModeRunner

    publisher = _RecordingPublisher()
    llm = MagicMock()
    llm.stream_plain = MagicMock(return_value=_gen(["Answer"]))
    llm.call_plain = AsyncMock(return_value="")

    runner = PlainModeRunner(llm=llm, event_publisher=publisher)
    session = MagicMock()
    added = []
    session.history.add_message = added.append

    await runner.run_streaming(
        session=session, model="m", messages=[{"role": "user", "content": "q"}],
    )

    assert "reasoning_content" not in added[0].metadata


# -- transport ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_chat_response_includes_reasoning_only_when_present():
    from atlas.application.chat.utilities.event_notifier import notify_chat_response

    sent = []

    async def cb(payload):
        sent.append(payload)

    await notify_chat_response("hi", update_callback=cb, reasoning_content="because")
    await notify_chat_response("hi", update_callback=cb)

    assert sent[0]["reasoning_content"] == "because"
    assert "reasoning_content" not in sent[1]
