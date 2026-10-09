"""Integration tests for error flow through the live tools streaming path."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.modes.tools import ToolsModeRunner
from atlas.application.chat.utilities.error_handler import classify_llm_error, error_type_for
from atlas.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ContextWindowExceededError,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMMalformedToolCallError,
    LLMServiceError,
    LLMTimeoutError,
    RateLimitError,
    ValidationError,
)
from atlas.domain.sessions.models import Session
from atlas.interfaces.llm import LLMResponse


def _runner(error=None):
    class StreamingLLM:
        async def stream_with_tools(self, *args, **kwargs):
            if error:
                raise error
            yield "Test response"
            yield LLMResponse(content="Test response")

    manager = MagicMock()
    manager.get_tools_schema.return_value = []
    return ToolsModeRunner(StreamingLLM(), manager, AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("error,error_class,error_type,message_part", [
    (Exception("RateLimitError: high traffic; internal-detail"), RateLimitError, "rate_limit", "high traffic"),
    (Exception("Request timed out; internal-detail"), LLMTimeoutError, "timeout", "timed out"),
    (Exception("Invalid API key; internal-detail"), LLMAuthenticationError, "authentication", "administrator"),
    (Exception("maximum context length; internal-detail"), ContextWindowExceededError,
     "context_window_exceeded", "too long"),
    (Exception("internal-detail"), LLMServiceError, "domain", "encountered an error"),
])
async def test_stream_errors_reach_websocket_and_cli(error, error_class, error_type, message_part):
    runner = _runner(error)
    kwargs = dict(session=Session(), model="test-model", messages=[], selected_tools=[])
    result = await runner.run_streaming(**kwargs)

    payload = runner.event_publisher.send_json.await_args.args[0]
    assert payload["type"] == "error"
    assert payload["error_type"] == error_type
    assert message_part in payload["message"]
    assert "internal-detail" not in payload["message"]
    assert result["message"] == payload["message"]
    runner.event_publisher.publish_response_complete.assert_awaited_once()
    runner.event_publisher.publish_token_stream.assert_awaited_once_with(
        token="", is_first=False, is_last=True,
    )

    runner.raise_on_stream_error = True
    with pytest.raises(error_class) as raised:
        await runner.run_streaming(**kwargs)
    assert str(raised.value) == payload["message"]


@pytest.mark.parametrize("error,error_type", [
    (AuthenticationError("Please sign in again"), "authentication"),
    (AuthorizationError("Access denied"), "authorization"),
    (LLMBadRequestError("Unsupported tool schema", tool_names=["read"]), "bad_request"),
    (LLMMalformedToolCallError("Incomplete tool call", tool_names=["read"], truncated=True),
     "malformed_tool_call"),
])
@pytest.mark.asyncio
async def test_specific_domain_errors_keep_their_safe_message_and_identity(error, error_type):
    error_class, message, _ = classify_llm_error(error)
    assert message == error.message
    assert error_type_for(error_class) == error_type

    runner = _runner(error)
    runner.raise_on_stream_error = True
    with pytest.raises(type(error)) as raised:
        await runner.run_streaming(
            session=Session(), model="test-model", messages=[], selected_tools=[],
        )
    assert raised.value is error


def test_error_type_mapping_handles_subclasses_and_unknown_values():
    class CustomValidationError(ValidationError):
        pass

    assert error_type_for(CustomValidationError) == "validation"
    assert error_type_for(RuntimeError) == "unexpected"
    assert error_type_for(None) == "unexpected"


@pytest.mark.asyncio
async def test_successful_stream_persists_response():
    runner = _runner()
    session = Session()
    result = await runner.run_streaming(
        session=session, model="test-model", messages=[], selected_tools=[],
    )
    assert result["message"] == "Test response"
    assert session.history.messages[-1].content == "Test response"
    runner.event_publisher.send_json.assert_not_awaited()
    runner.event_publisher.publish_response_complete.assert_awaited_once()


class TestLiteLLMCallerErrorClassification:
    """Test _raise_llm_domain_error and _is_retryable_error for context window errors."""

    def test_raise_llm_domain_error_context_window_by_keyword(self):
        """Test that _raise_llm_domain_error maps context window keywords."""
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        exc = Exception("This model's maximum context length is 128000 tokens")
        with pytest.raises(ContextWindowExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_is_retryable_error_context_window_returns_false(self):
        """Test that context window errors are not retryable."""
        from atlas.modules.llm.litellm_caller import LiteLLMCaller

        exc = Exception("maximum context length exceeded")
        assert LiteLLMCaller._is_retryable_error(exc) is False
