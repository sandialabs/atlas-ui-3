"""Reasoning round-trip on the assistant tool-call message (issue #1230).

Reasoning models served through some providers reject the second LLM call of a
tool-using turn when the assistant tool-call message does not carry the
reasoning the model produced with it::

    The assistant turn at messages[2] is missing its reasoning ...
    Send it back in 'reasoning_content'

These tests drive the real ``LiteLLMCaller`` (streaming and non-streaming)
inside the real ``AgenticLoop`` against a fake provider that enforces that
check, and confirm the reasoning stays out of history, the UI stream, logs
and fine-tune capture.
"""

import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Delta,
    Function,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
)
from litellm.types.utils import (
    Message as LiteLLMMessage,
)

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.tests.test_tools_mode_iteration import (
    ScriptedToolsLLM,
    _config,
    _run,
    _runner,
    _tc,
)

REASONING = "SECRET-REASONING: the user wants the weather, call the tool."
DETAILS = [{"type": "reasoning.encrypted", "id": "rd-1", "data": "opaque-blob", "index": 0}]
TOOL_ARGS = '{"city": "Albuquerque"}'


# -- Fake provider -------------------------------------------------------

class StrictReasoningProvider:
    """Fake acompletion that enforces the reasoning round-trip like the
    providers in the issue: the follow-up request fails unless every
    assistant tool-call message carries the reasoning it was produced with.
    """

    def __init__(self, streaming: bool):
        self.streaming = streaming
        self.requests: List[List[Dict[str, Any]]] = []

    async def __call__(self, **kwargs):
        messages = kwargs["messages"]
        self.requests.append([dict(m) for m in messages])
        if len(self.requests) == 1:
            return self._tool_call_response()
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                if msg.get("reasoning_content") != REASONING:
                    raise ValueError(f"The assistant turn at messages[{i}] is missing its reasoning.")
        return self._final_response()

    def _tool_call_response(self):
        if not self.streaming:
            return ModelResponse(choices=[Choices(
                finish_reason="tool_calls",
                message=LiteLLMMessage(
                    content=None,
                    reasoning_content=REASONING,
                    tool_calls=[ChatCompletionMessageToolCall(
                        id="call-1", type="function",
                        function=Function(name="weather_get", arguments=TOOL_ARGS),
                    )],
                ),
            )])

        chunks = [
            # Reasoning arrives in pieces before the tool call.
            _stream_chunk(Delta(reasoning_content=REASONING[:20])),
            _stream_chunk(Delta(reasoning_content=REASONING[20:])),
            _stream_chunk(Delta(tool_calls=[{
                "index": 0, "id": "call-1", "type": "function",
                "function": {"name": "weather_get", "arguments": TOOL_ARGS},
            }])),
            _stream_chunk(Delta(), finish_reason="tool_calls"),
        ]
        return _agen(chunks)

    def _final_response(self):
        if not self.streaming:
            return ModelResponse(choices=[Choices(
                finish_reason="stop",
                message=LiteLLMMessage(content="It is sunny.", reasoning_content="final thoughts"),
            )])
        return _agen([
            _stream_chunk(Delta(reasoning_content="final thoughts")),
            _stream_chunk(Delta(content="It is sunny.")),
            _stream_chunk(Delta(), finish_reason="stop"),
        ])


def _stream_chunk(delta, finish_reason=None):
    return ModelResponseStream(choices=[StreamingChoices(delta=delta, finish_reason=finish_reason)])


async def _agen(items):
    for item in items:
        yield item


def _caller() -> LiteLLMCaller:
    config = MagicMock()
    config.models = {}
    caller = LiteLLMCaller(llm_config=config)
    caller._get_litellm_model_name = MagicMock(return_value="openrouter/reasoner")
    caller._get_model_kwargs = MagicMock(return_value={"max_tokens": 100})
    caller._prepare_messages = MagicMock(side_effect=lambda m, msgs: msgs)
    return caller


def _tool_manager():
    async def execute(tool_call_obj, context=None):
        return ToolResult(tool_call_id=tool_call_obj.id, content="sunny", success=True)

    mgr = MagicMock()
    mgr.execute_tool = AsyncMock(side_effect=execute)
    mgr.get_tools_schema = MagicMock(return_value=[
        {"type": "function", "function": {"name": "weather_get", "parameters": {}}},
    ])
    return mgr


async def _run_turn(streaming: bool):
    provider = StrictReasoningProvider(streaming=streaming)
    publisher = MagicMock()
    publisher.publish_token_stream = AsyncMock()
    context = AgentContext(
        session_id=uuid4(), user_email="test@example.com", files={},
        history=ConversationHistory(),
    )
    events = []

    async def handler(event):
        events.append(event)

    loop = AgenticLoop(llm=_caller(), tool_manager=_tool_manager(), prompt_provider=None)
    loop.skip_approval = True
    with patch("atlas.modules.llm.litellm_caller.acompletion", provider), \
            patch("atlas.modules.llm.litellm_streaming.acompletion", provider):
        result = await loop.run(
            model="reasoner",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Weather in Albuquerque?"},
            ],
            context=context,
            selected_tools=["weather_get"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            streaming=streaming,
            event_publisher=publisher,
        )
    return provider, result, context, publisher, events


# -- Agent loop round trip ---------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["non-streaming", "streaming"])
class TestAgentLoopRoundTrip:

    async def test_follow_up_call_carries_reasoning(self, streaming):
        provider, result, _context, _publisher, _events = await _run_turn(streaming)

        assert result.final_answer == "It is sunny."
        assert len(provider.requests) == 2
        assistant = provider.requests[1][2]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"][0]["id"] == "call-1"
        assert assistant["reasoning_content"] == REASONING

    async def test_reasoning_not_persisted_or_published(self, streaming, caplog):
        caplog.set_level(logging.DEBUG)
        _provider, _result, context, publisher, events = await _run_turn(streaming)

        for msg in context.history.messages:
            assert REASONING not in (msg.content or "")
            assert REASONING not in repr(msg.metadata)
        for call in publisher.mock_calls:
            assert REASONING not in repr(call)
        for event in events:
            assert REASONING not in repr(event.payload)
        assert "SECRET-REASONING" not in caplog.text


class TestAgentLoopWithoutReasoning:

    @pytest.mark.asyncio
    async def test_no_reasoning_adds_no_fields(self):
        """Models that return no reasoning send exactly the old message shape."""
        response = LLMResponse(content="calling", tool_calls=[{"id": "x"}])
        assert response.assistant_message([{"id": "x"}]) == {
            "role": "assistant", "content": "calling", "tool_calls": [{"id": "x"}],
        }


@pytest.mark.asyncio
async def test_streaming_retry_does_not_mix_reasoning_across_attempts():
    """A stream that fails before any token is retried from scratch; reasoning
    from the failed attempt must not leak into the retried response."""
    caller = _caller()
    attempts = {"n": 0}

    async def flaky(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            async def broken():
                yield _stream_chunk(Delta(reasoning_content="stale "))
                raise ConnectionError("connection reset")
            return broken()
        return _agen([
            _stream_chunk(Delta(reasoning_content="fresh")),
            _stream_chunk(Delta(tool_calls=[{
                "index": 0, "id": "c", "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }])),
            _stream_chunk(Delta(), finish_reason="tool_calls"),
        ])

    caller._is_retryable_error = MagicMock(return_value=True)
    final = None
    with patch("atlas.modules.llm.litellm_streaming.acompletion", flaky), \
            patch("atlas.modules.llm.litellm_streaming._llm_retry_settings", return_value=(2, 10.0)), \
            patch("atlas.modules.llm.litellm_streaming._retry_backoff_delay", return_value=0):
        async for item in caller.stream_with_tools("reasoner", [{"role": "user", "content": "x"}], [{"type": "function"}]):
            if isinstance(item, LLMResponse):
                final = item
    assert attempts["n"] == 2
    assert final.reasoning == {"reasoning_content": "fresh"}


# -- Tools mode (non-agent) ---------------------------------------------

@pytest.mark.asyncio
async def test_tools_mode_continuation_round_carries_reasoning():
    """Standard tools mode appends the same assistant tool-call message, so its
    continuation round must carry the reasoning too."""
    class ReasoningScriptedLLM(ScriptedToolsLLM):
        async def stream_with_tools(self, *args, **kwargs):
            async for item in super().stream_with_tools(*args, **kwargs):
                if isinstance(item, LLMResponse) and item.has_tool_calls():
                    item.reasoning = {"reasoning_content": REASONING}
                yield item

    llm = ReasoningScriptedLLM(turns=[
        ("checking", [_tc("c1", "calc", '{"e":"2+2"}')]),
        ("It is 4.", None),
    ])
    executed: List[str] = []
    await _run(_runner(llm, _config(max_extra_rounds=3)), [{"role": "user", "content": "calc"}], executed)

    assert executed == ["calc"]
    follow_up = llm.seen_messages[1]
    assistant = next(m for m in follow_up if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant["reasoning_content"] == REASONING


# -- Review follow-ups -------------------------------------------------

def test_steered_text_only_fold_carries_reasoning():
    """The steered fold (#824) appends an assistant message mid-turn, so it
    must carry the reasoning too, while the history row stays text only."""
    from atlas.application.chat.agent.agentic_loop import _fold_text_only_as_intermediate

    messages: List[Dict[str, Any]] = []
    context = AgentContext(session_id=uuid4(), user_email="u@x", files={}, history=ConversationHistory())
    response = LLMResponse(content="Working on it.", reasoning={"reasoning_content": REASONING})

    _fold_text_only_as_intermediate(messages, context, response, step=1)

    assert messages == [{"role": "assistant", "content": "Working on it.", "reasoning_content": REASONING}]
    row = context.history.messages[0]
    assert row.content == "Working on it."
    assert REASONING not in repr(row.metadata)


@pytest.mark.asyncio
async def test_pre_llm_hook_never_sees_reasoning_and_rewrite_keeps_it():
    """PreLlmCall hooks may forward their envelope to an external service, so
    the reasoning is stripped from it; a hook rewrite then gets it put back."""
    from atlas.hooks.manager import HookOutcome

    seen: Dict[str, Any] = {}

    class Manager:
        def has_hooks(self, event):
            return True

        async def run_event(self, event, payload, session_context=None):
            seen["payload"] = payload
            rewritten = [dict(m) for m in payload["messages"]]
            rewritten[0]["content"] = "[redacted]"
            return HookOutcome(verdict="modify", payload={"messages": rewritten}, fired=True, modified=True)

    provider = StrictReasoningProvider(streaming=False)
    provider.requests.append([])  # skip the tool-call reply: go straight to the strict check
    messages = [
        {"role": "user", "content": "Weather in Albuquerque?"},
        {
            "role": "assistant", "content": None, "reasoning_content": REASONING,
            "tool_calls": [{"id": "call-1", "type": "function",
                            "function": {"name": "weather_get", "arguments": TOOL_ARGS}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "sunny"},
    ]
    caller = _caller()
    with patch("atlas.modules.llm.litellm_caller.get_hook_manager", return_value=Manager()), \
            patch("atlas.modules.llm.litellm_caller.acompletion", provider):
        response = await caller.call_with_tools("reasoner", messages, [{"type": "function"}])

    assert REASONING not in repr(seen["payload"])
    assert response.content == "It is sunny."
    sent = provider.requests[-1]
    assert sent[0]["content"] == "[redacted]"
    assert sent[1]["reasoning_content"] == REASONING
    # The caller's own list is untouched by the strip.
    assert messages[1]["reasoning_content"] == REASONING
