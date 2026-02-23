"""Tests for LLM token streaming flow (PR #355).

Covers:
- stream_and_accumulate helper (happy path, empty stream, mid-stream error)
- stream_final_answer shared agent helper
- Event publisher publish_token_stream contract
- Backpressure yield in litellm_caller generators
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.agent.streaming_final_answer import stream_final_answer
from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate


# -- Helpers -----------------------------------------------------------------

async def _async_gen(*tokens):
    """Create an async generator yielding the given tokens."""
    for t in tokens:
        yield t


async def _async_gen_error(*tokens):
    """Yield some tokens then raise."""
    for t in tokens:
        yield t
    raise RuntimeError("mid-stream failure")


def _make_publisher():
    """Create a mock EventPublisher with all required methods."""
    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    return pub


def _make_llm(tokens=None, plain_response="fallback"):
    """Create a mock LLMProtocol with stream_plain and call_plain."""
    llm = MagicMock()

    async def _stream(*args, **kwargs):
        for t in (tokens or []):
            yield t

    llm.stream_plain = _stream
    llm.call_plain = AsyncMock(return_value=plain_response)
    return llm


# -- stream_and_accumulate ---------------------------------------------------

@pytest.mark.asyncio
async def test_stream_and_accumulate_happy_path():
    """Tokens are accumulated and is_last is sent at the end."""
    pub = _make_publisher()

    result = await stream_and_accumulate(
        token_generator=_async_gen("Hello", " ", "World"),
        event_publisher=pub,
        context_label="test",
    )

    assert result == "Hello World"
    # 3 token calls + 1 is_last
    assert pub.publish_token_stream.await_count == 4
    # First call has is_first=True
    first_call = pub.publish_token_stream.await_args_list[0]
    assert first_call.kwargs["is_first"] is True
    assert first_call.kwargs["is_last"] is False
    # Last call is the terminator
    last_call = pub.publish_token_stream.await_args_list[-1]
    assert last_call.kwargs["is_last"] is True
    assert last_call.kwargs["token"] == ""


@pytest.mark.asyncio
async def test_stream_and_accumulate_empty_with_fallback():
    """Empty stream calls fallback_fn and publishes chat_response."""
    pub = _make_publisher()
    fallback = AsyncMock(return_value="fallback text")

    result = await stream_and_accumulate(
        token_generator=_async_gen(),  # yields nothing
        event_publisher=pub,
        fallback_fn=fallback,
        context_label="test",
    )

    assert result == "fallback text"
    fallback.assert_awaited_once()
    pub.publish_chat_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_and_accumulate_empty_no_fallback():
    """Empty stream without fallback returns empty string."""
    pub = _make_publisher()

    result = await stream_and_accumulate(
        token_generator=_async_gen(),
        event_publisher=pub,
        context_label="test",
    )

    assert result == ""


@pytest.mark.asyncio
async def test_stream_and_accumulate_error_sends_is_last():
    """Mid-stream error always sends is_last to prevent stuck UI cursor."""
    pub = _make_publisher()

    result = await stream_and_accumulate(
        token_generator=_async_gen_error("partial"),
        event_publisher=pub,
        context_label="test",
    )

    assert result == "partial"
    # Should have: 1 token + 1 is_last (error path)
    last_call = pub.publish_token_stream.await_args_list[-1]
    assert last_call.kwargs["is_last"] is True


@pytest.mark.asyncio
async def test_stream_and_accumulate_error_no_tokens_with_fallback():
    """Error before any tokens calls fallback."""
    pub = _make_publisher()

    async def _immediate_error():
        raise RuntimeError("immediate")
        yield  # noqa: unreachable - makes this an async generator

    fallback = AsyncMock(return_value="error fallback")

    result = await stream_and_accumulate(
        token_generator=_immediate_error(),
        event_publisher=pub,
        fallback_fn=fallback,
        context_label="test",
    )

    assert result == "error fallback"
    fallback.assert_awaited_once()


# -- stream_final_answer (agent helper) -------------------------------------

@pytest.mark.asyncio
async def test_stream_final_answer_happy():
    """Agent streaming helper accumulates tokens and sends stream-end."""
    llm = _make_llm(tokens=["Agent", " answer"])
    pub = _make_publisher()

    result = await stream_final_answer(
        llm=llm, event_publisher=pub, model="test",
        messages=[], temperature=0.7, user_email=None,
    )

    assert result == "Agent answer"
    # 2 tokens + 1 is_last
    assert pub.publish_token_stream.await_count == 3


@pytest.mark.asyncio
async def test_stream_final_answer_empty_falls_back():
    """Empty stream falls back to call_plain."""
    llm = _make_llm(tokens=[], plain_response="plain fallback")
    pub = _make_publisher()

    result = await stream_final_answer(
        llm=llm, event_publisher=pub, model="test",
        messages=[], temperature=0.7, user_email=None,
    )

    assert result == "plain fallback"
    llm.call_plain.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_final_answer_error_sends_stream_end():
    """Error during streaming sends is_last and falls back."""
    llm = MagicMock()

    async def _err_stream(*args, **kwargs):
        yield "partial"
        raise RuntimeError("boom")

    llm.stream_plain = _err_stream
    llm.call_plain = AsyncMock(return_value="should not be called")
    pub = _make_publisher()

    result = await stream_final_answer(
        llm=llm, event_publisher=pub, model="test",
        messages=[], temperature=0.7, user_email=None,
    )

    # Should use partial content since tokens were received
    assert result == "partial"
    last_call = pub.publish_token_stream.await_args_list[-1]
    assert last_call.kwargs["is_last"] is True


# -- Error propagation to frontend ------------------------------------------

@pytest.mark.asyncio
async def test_stream_and_accumulate_error_no_tokens_no_fallback_sends_user_message():
    """When stream errors with no tokens and no fallback, a user-friendly
    error message (from classify_llm_error) is sent via publish_chat_response."""
    pub = _make_publisher()

    async def _auth_error():
        raise RuntimeError("AuthenticationError: invalid api key")
        yield  # noqa: unreachable - makes this an async generator

    result = await stream_and_accumulate(
        token_generator=_auth_error(),
        event_publisher=pub,
        context_label="test",
    )

    # Should use the classified user-friendly message, not raw exception
    assert "authentication" in result.lower() or "error" in result.lower()
    # Must NOT contain raw exception traceback details
    assert "RuntimeError" not in result
    # The message should be sent to the frontend
    pub.publish_chat_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_and_accumulate_error_fallback_also_fails():
    """When both stream and fallback fail, a user-friendly error is returned."""
    pub = _make_publisher()

    async def _immediate_error():
        raise RuntimeError("Failed to stream LLM: invalid api key")
        yield  # noqa: unreachable

    fallback = AsyncMock(side_effect=RuntimeError("also fails"))

    result = await stream_and_accumulate(
        token_generator=_immediate_error(),
        event_publisher=pub,
        fallback_fn=fallback,
        context_label="test",
    )

    # Should return user-friendly message, not raw exception
    assert "RuntimeError" not in result
    assert len(result) > 0
    pub.publish_chat_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_final_answer_both_stream_and_fallback_fail():
    """When streaming and fallback both fail, return a classified error message."""
    llm = MagicMock()

    async def _err_stream(*args, **kwargs):
        raise RuntimeError("Failed to stream LLM: AuthenticationError: invalid api key")
        yield  # noqa: unreachable

    llm.stream_plain = _err_stream
    llm.call_plain = AsyncMock(side_effect=RuntimeError("also fails"))
    pub = _make_publisher()

    result = await stream_final_answer(
        llm=llm, event_publisher=pub, model="test",
        messages=[], temperature=0.7, user_email=None,
    )

    # Should return a user-friendly message
    assert "authentication" in result.lower()
    assert "RuntimeError" not in result
    # Stream-end should still be sent
    last_call = pub.publish_token_stream.await_args_list[-1]
    assert last_call.kwargs["is_last"] is True


@pytest.mark.asyncio
async def test_tools_run_streaming_auth_error_sends_error_to_frontend():
    """When tools streaming fails with auth error, an error message is sent
    to the frontend via send_json instead of sending empty content."""
    from atlas.application.chat.modes.tools import ToolsModeRunner

    # Set up mock dependencies
    llm = MagicMock()

    async def _err_stream(*args, **kwargs):
        raise RuntimeError("Failed to stream LLM with tools: AuthenticationError: invalid api key")
        yield  # noqa: unreachable

    llm.stream_with_tools = _err_stream

    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function", "function": {"name": "test"}}])

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()

    # Create a mock session
    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()

    runner = ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=pub,
    )

    result = await runner.run_streaming(
        session=session,
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        selected_tools=["test_tool"],
    )

    # Error should be sent to frontend via send_json
    pub.send_json.assert_awaited_once()
    error_payload = pub.send_json.await_args[0][0]
    assert error_payload["type"] == "error"
    assert "authentication" in error_payload["message"].lower()

    # Response should be complete
    pub.publish_response_complete.assert_awaited_once()

    # Result should contain the error message
    assert "authentication" in result.get("message", "").lower()


@pytest.mark.asyncio
async def test_tools_run_streaming_partial_content_after_error_not_lost():
    """When tools streaming fails AFTER receiving some tokens, the partial
    content should still be delivered (not replaced with error)."""
    from atlas.application.chat.modes.tools import ToolsModeRunner

    llm = MagicMock()

    async def _partial_stream(*args, **kwargs):
        yield "partial content"
        raise RuntimeError("mid-stream failure")

    llm.stream_with_tools = _partial_stream

    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[])

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()

    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()

    runner = ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=pub,
    )

    result = await runner.run_streaming(
        session=session,
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        selected_tools=["test_tool"],
    )

    # Should NOT send error via send_json since we had partial content
    pub.send_json.assert_not_awaited()
    # Partial content should be preserved
    assert result.get("message") == "partial content"


# -- SimpleNamespace-to-dict conversion in tools streaming -------------------

@pytest.mark.asyncio
async def test_tools_streaming_tool_call_dict_conversion():
    """Tool calls from streaming (SimpleNamespace) are converted to plain dicts."""
    from types import SimpleNamespace
    from atlas.application.chat.modes.tools import ToolsModeRunner
    from atlas.interfaces.llm import LLMResponse

    # Simulate a stream that yields text then an LLMResponse with tool_calls
    tool_call_ns = SimpleNamespace(
        id="call-abc",
        type="function",
        function=SimpleNamespace(
            name="server_tool",
            arguments='{"q":"test"}',
        ),
    )
    llm_response = LLMResponse(
        content="I'll search that for you.",
        tool_calls=[tool_call_ns],
        model_used="test-model",
    )

    async def _stream(*args, **kwargs):
        yield "I'll search"
        yield " that for you."
        yield llm_response

    llm = MagicMock()
    llm.stream_with_tools = _stream

    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])

    # Mock the tool execution to capture the messages list
    captured_messages = []
    async def _mock_execute_single(tool_call, session_context, tool_manager, update_callback, config_manager=None, skip_approval=False):
        from atlas.domain.messages.models import ToolResult
        return ToolResult(tool_call_id=tool_call.id, content="result", success=True)

    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()

    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()
    session.session_id = "test-session"
    session.files = {}

    runner = ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=pub,
    )

    messages = [{"role": "user", "content": "search for test"}]

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_single_tool = _mock_execute_single
        mock_te.build_files_manifest = MagicMock(return_value=None)

        # Mock stream_plain for synthesis
        async def _synth_stream(*args, **kwargs):
            yield "Here are the results."

        llm.stream_plain = _synth_stream
        llm.call_plain = AsyncMock(return_value="Here are the results.")

        result = await runner.run_streaming(
            session=session,
            model="test-model",
            messages=messages,
            selected_tools=["server_tool"],
        )

    # Verify the appended assistant message has dict tool_calls, not SimpleNamespace
    assistant_msg = next(m for m in messages if m.get("role") == "assistant" and "tool_calls" in m)
    for tc in assistant_msg["tool_calls"]:
        assert isinstance(tc, dict), f"Expected dict, got {type(tc)}"
        assert tc["id"] == "call-abc"
        assert tc["function"]["name"] == "server_tool"
        assert tc["function"]["arguments"] == '{"q":"test"}'


# -- CLI event publisher streaming -------------------------------------------

@pytest.mark.asyncio
async def test_cli_publisher_token_stream():
    """CLIEventPublisher accumulates tokens in collected result."""
    from atlas.infrastructure.events.cli_event_publisher import CLIEventPublisher

    pub = CLIEventPublisher(streaming=False)
    await pub.publish_token_stream(token="Hello", is_first=True)
    await pub.publish_token_stream(token=" World", is_first=False)
    await pub.publish_token_stream(token="", is_last=True)

    assert pub.get_result().message == "Hello World"


# -- WebSocket publisher token stream ----------------------------------------

@pytest.mark.asyncio
async def test_websocket_publisher_token_stream():
    """WebSocketEventPublisher delegates to notify_token_stream."""
    from atlas.infrastructure.events.websocket_publisher import WebSocketEventPublisher

    conn = AsyncMock()
    pub = WebSocketEventPublisher(connection=conn)

    await pub.publish_token_stream(token="chunk", is_first=True, is_last=False)

    # Should have called send_json on the connection
    conn.send_json.assert_awaited_once()
    sent = conn.send_json.await_args[0][0]
    assert sent["type"] == "token_stream"
    assert sent["token"] == "chunk"
    assert sent["is_first"] is True
    assert sent["is_last"] is False
