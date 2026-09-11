"""Tests for LLM auto-retry with exponential backoff (issue #919).

Verifies that transient errors (rate limit, timeout, service errors) are
retried up to the configured retry count (LLM_MAX_RETRIES env, default 5)
while non-retryable errors (auth) are raised immediately, that the cumulative
backoff is capped at LLM_RETRY_MAX_WAIT_SECONDS (default 300s = 5 minutes),
and that the streaming generators follow the same policy until the first
token reaches the consumer.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

from atlas.domain.errors import (
    ContextWindowExceededError,
    LLMAuthenticationError,
    LLMEmptyStreamError,
    RateLimitError,
)
from atlas.modules.config.config_manager import config_manager
from atlas.modules.llm import litellm_caller as caller_module
from atlas.modules.llm import litellm_streaming as streaming_module
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.models import LLMResponse
from atlas.modules.llm.retry_config import (
    DEFAULT_LLM_RETRIES,
    DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS,
)
from atlas.tests.mocks.mock_flaky_llm import (
    AlwaysFailLLMResponse,
    FlakyLLMResponse,
    make_llm_response,
)


@pytest.fixture(autouse=True)
def _default_retry_env(monkeypatch):
    """Pin retry env vars to their defaults so ambient shell settings cannot
    skew call-count expectations; individual tests override from here."""
    monkeypatch.setenv("LLM_MAX_RETRIES", str(DEFAULT_LLM_RETRIES))
    monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", str(DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS))


@pytest.fixture
def caller():
    """Create a LiteLLMCaller with minimal config for testing."""
    mock_config = MagicMock()
    mock_config.models = {}
    return LiteLLMCaller(llm_config=mock_config)


def _rate_limit_exc():
    return litellm.RateLimitError(
        message="rate limit exceeded", llm_provider="test", model="test",
    )


def _timeout_exc():
    return litellm.Timeout(
        message="request timed out", llm_provider="test", model="test",
    )


def _auth_exc():
    return litellm.AuthenticationError(
        message="invalid api key", llm_provider="test", model="test",
    )


class TestIsRetryableError:
    """Test error classification for retry decisions."""

    def test_rate_limit_error_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(_rate_limit_exc()) is True

    def test_timeout_error_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(_timeout_exc()) is True

    def test_rate_limit_by_message_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("429 rate limit exceeded")) is True

    def test_timeout_by_message_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("request timed out after 60s")) is True

    def test_server_error_503_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("503 service unavailable")) is True

    def test_server_error_502_is_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("502 bad gateway")) is True

    def test_auth_error_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(_auth_exc()) is False

    def test_auth_by_message_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("invalid api key provided")) is False

    def test_unauthorized_by_message_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("unauthorized access")) is False

    def test_context_window_error_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("maximum context length exceeded")) is False

    def test_context_window_error_by_keyword_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("context_length_exceeded")) is False

    def test_generic_error_is_not_retryable(self):
        assert LiteLLMCaller._is_retryable_error(Exception("something unexpected")) is False


class TestAcompletionWithRetry:
    """Test the retry wrapper around acompletion."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_succeeds_after_transient_failures(self, mock_sleep, caller):
        """Flaky LLM that fails twice then succeeds should return on 3rd attempt."""
        flaky = FlakyLLMResponse(fail_count=2, exception=_rate_limit_exc(), success_content="finally worked")
        mock_acompletion = AsyncMock(side_effect=flaky.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            result = await caller._acompletion_with_retry(model="test", messages=[])

        assert result.choices[0].message.content == "finally worked"
        assert mock_acompletion.call_count == 3
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_retries_exhausted(self, mock_sleep, caller):
        """When all retries fail, the last exception should propagate."""
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == DEFAULT_LLM_RETRIES + 1
        assert mock_sleep.call_count == DEFAULT_LLM_RETRIES

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_auth_error_not_retried(self, mock_sleep, caller):
        """Auth errors should raise immediately without retry."""
        mock_acompletion = AsyncMock(side_effect=_auth_exc())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.AuthenticationError):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == 1
        assert mock_sleep.call_count == 0

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_timeout_is_retried(self, mock_sleep, caller):
        """Timeout errors should be retried."""
        flaky = FlakyLLMResponse(fail_count=1, exception=_timeout_exc(), success_content="recovered")
        mock_acompletion = AsyncMock(side_effect=flaky.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            result = await caller._acompletion_with_retry(model="test", messages=[])

        assert result.choices[0].message.content == "recovered"
        assert mock_acompletion.call_count == 2
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_backoff_delay_increases(self, mock_sleep, caller):
        """Each retry should wait longer (exponential backoff)."""
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1], (
                f"Delay {i} ({delays[i]:.1f}s) should exceed delay {i-1} ({delays[i-1]:.1f}s)"
            )

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try_no_delay(self, caller):
        """Successful first call should not sleep at all."""
        mock_acompletion = AsyncMock(return_value=make_llm_response("instant"))

        with patch.object(caller_module, "acompletion", mock_acompletion):
            result = await caller._acompletion_with_retry(model="test", messages=[])

        assert result.choices[0].message.content == "instant"
        assert mock_acompletion.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_generic_error_not_retried(self, mock_sleep, caller):
        """Non-transient generic errors should not be retried."""
        mock_acompletion = AsyncMock(side_effect=ValueError("bad input"))

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(ValueError, match="bad input"):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == 1
        assert mock_sleep.call_count == 0


class TestLlmRetrySettings:
    """Test env resolution for the retry count and wait budget."""

    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
        monkeypatch.delenv("LLM_RETRY_MAX_WAIT_SECONDS", raising=False)
        from atlas.modules.llm.retry_config import _llm_retry_settings
        assert _llm_retry_settings() == (5, 300.0)

    def test_env_overrides_both_values(self, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "2")
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "45")
        from atlas.modules.llm.retry_config import _llm_retry_settings
        assert _llm_retry_settings() == (2, 45.0)

    def test_invalid_values_fall_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "five")
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "soon")
        from atlas.modules.llm.retry_config import _llm_retry_settings
        assert _llm_retry_settings() == (5, 300.0)

    def test_negative_values_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "-3")
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "-10")
        from atlas.modules.llm.retry_config import _llm_retry_settings
        assert _llm_retry_settings() == (0, 0.0)


class TestRetryCountEnvOverride:
    """The retry count must follow LLM_MAX_RETRIES from the environment."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_env_reduces_retry_count(self, mock_sleep, caller, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "1")
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == 2
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_zero_retries_makes_single_call(self, mock_sleep, caller, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "0")
        mock_acompletion = AsyncMock(side_effect=_rate_limit_exc())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == 1
        assert mock_sleep.call_count == 0


class TestRetryWaitBudget:
    """The cumulative backoff must never exceed LLM_RETRY_MAX_WAIT_SECONDS."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_cumulative_backoff_never_exceeds_cap(self, mock_sleep, caller, monkeypatch):
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "1.2")
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        delays = [c.args[0] for c in mock_sleep.call_args_list]
        assert sum(delays) <= 1.2 + 1e-9
        # The budget forces an early stop long before the default 5 retries.
        assert len(delays) < DEFAULT_LLM_RETRIES

    @pytest.mark.asyncio
    async def test_zero_wait_budget_disables_backoff(self, caller, monkeypatch):
        """With no wait budget the first transient failure raises immediately."""
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", "0")
        mock_acompletion = AsyncMock(side_effect=_rate_limit_exc())

        with patch.object(caller_module, "acompletion", mock_acompletion):
            with pytest.raises(litellm.RateLimitError):
                await caller._acompletion_with_retry(model="test", messages=[])

        assert mock_acompletion.call_count == 1


class TestCallPlainWithRetry:
    """Test that call_plain uses retry for transient errors."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_call_plain_retries_rate_limit(self, mock_sleep, caller):
        """call_plain should retry on rate limit and eventually succeed."""
        flaky = FlakyLLMResponse(fail_count=1, exception=_rate_limit_exc(), success_content="recovered response")
        mock_acompletion = AsyncMock(side_effect=flaky.side_effect_list())

        with (
            patch.object(caller_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            result = await caller.call_plain("test-model", [{"role": "user", "content": "hi"}])

        assert result == "recovered response"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    async def test_call_plain_no_retry_on_auth(self, caller):
        """call_plain should not retry auth errors."""
        mock_acompletion = AsyncMock(side_effect=_auth_exc())

        with (
            patch.object(caller_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            with pytest.raises(LLMAuthenticationError):
                await caller.call_plain("test-model", [{"role": "user", "content": "hi"}])

        assert mock_acompletion.call_count == 1


class TestCallWithToolsRetry:
    """Test that call_with_tools uses retry for transient errors."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_call_with_tools_retries_timeout(self, mock_sleep, caller):
        """call_with_tools should retry on timeout and eventually succeed."""
        flaky = FlakyLLMResponse(fail_count=1, exception=_timeout_exc(), success_content="tool response")
        mock_acompletion = AsyncMock(side_effect=flaky.side_effect_list())

        tools_schema = [{"type": "function", "function": {"name": "test_tool"}}]

        with (
            patch.object(caller_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            result = await caller.call_with_tools(
                "test-model", [{"role": "user", "content": "hi"}], tools_schema,
            )

        assert result.content == "tool response"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_call_with_tools_exhausts_retries(self, mock_sleep, caller):
        """call_with_tools should raise domain error after exhausting retries."""
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        tools_schema = [{"type": "function", "function": {"name": "test_tool"}}]

        with (
            patch.object(caller_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            with pytest.raises(RateLimitError):
                await caller.call_with_tools(
                    "test-model", [{"role": "user", "content": "hi"}], tools_schema,
                )

        assert mock_acompletion.call_count == DEFAULT_LLM_RETRIES + 1


class TestRagFallbackDoesNotMaskLLMErrors:
    """Test that call_with_rag re-raises LLM domain errors instead of falling back."""

    @pytest.mark.asyncio
    async def test_call_with_rag_reraises_rate_limit(self, caller):
        """Rate limit from inner call_plain should not trigger RAG fallback."""
        with (
            patch.object(caller, "call_plain", side_effect=RateLimitError("rate limited")),
            patch.object(caller, "_get_litellm_model_name", return_value="test"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            caller._rag_service = MagicMock()
            caller._rag_service.query = AsyncMock(return_value=MagicMock(
                content="rag content", metadata=None, is_completion=False,
            ))

            with pytest.raises(RateLimitError):
                await caller.call_with_rag(
                    "test", [{"role": "user", "content": "hi"}],
                    data_sources=["src"], user_email=config_manager.app_settings.test_user,
                )


    @pytest.mark.asyncio
    async def test_call_with_rag_reraises_context_window_exceeded(self, caller):
        """Context window exceeded from inner call_plain should not trigger RAG fallback."""
        with (
            patch.object(caller, "call_plain", side_effect=ContextWindowExceededError("too long")),
            patch.object(caller, "_get_litellm_model_name", return_value="test"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            caller._rag_service = MagicMock()
            caller._rag_service.query = AsyncMock(return_value=MagicMock(
                content="rag content", metadata=None, is_completion=False,
            ))

            with pytest.raises(ContextWindowExceededError):
                await caller.call_with_rag(
                    "test", [{"role": "user", "content": "hi"}],
                    data_sources=["src"], user_email=config_manager.app_settings.test_user,
                )


def _stream_chunk(content):
    """Build a minimal streaming chunk matching litellm's shape."""
    delta = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _FakeStream:
    """Async-iterable stand-in for litellm's streaming response.

    Yields the given text contents, then raises ``error`` if one is set,
    otherwise stops.
    """

    def __init__(self, contents, error=None):
        self._contents = list(contents)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._contents:
            return _stream_chunk(self._contents.pop(0))
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


class TestStreamingRetry:
    """stream_plain / stream_with_tools follow the same retry policy.

    Retry is only allowed while nothing has been yielded: a token already
    handed to the consumer cannot be taken back, so a retry would duplicate
    part of the answer.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_tools", [False, True])
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_empty_stream_retries_then_recovers(
        self, mock_sleep, caller, with_tools, caplog,
    ):
        empty = _FakeStream([])
        success = _FakeStream(["recovered"])
        completion = AsyncMock(side_effect=[empty, success])
        with (
            patch.object(streaming_module, "acompletion", completion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            stream = (
                caller.stream_with_tools("test-model", [], [{"type": "function"}])
                if with_tools else caller.stream_plain("test-model", [])
            )
            items = [item async for item in stream]

        assert [item for item in items if isinstance(item, str)] == ["recovered"]
        assert completion.call_count == 2
        assert mock_sleep.call_count == 1
        assert empty.closed
        assert "test-model completed with zero content chunks and no tool calls after " in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_tools", [False, True])
    @pytest.mark.parametrize(
        "retries,wait_budget,expected_calls", [(2, 300, 3), (0, 300, 1), (2, 0, 1)],
    )
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_empty_stream_exhaustion_surfaces_domain_error(
        self, mock_sleep, caller, monkeypatch, with_tools, retries,
        wait_budget, expected_calls, caplog,
    ):
        monkeypatch.setenv("LLM_MAX_RETRIES", str(retries))
        monkeypatch.setenv("LLM_RETRY_MAX_WAIT_SECONDS", str(wait_budget))
        streams = [_FakeStream([]) for _ in range(expected_calls)]
        completion = AsyncMock(side_effect=streams)
        with (
            patch.object(streaming_module, "acompletion", completion),
            patch.object(streaming_module, "set_attrs") as set_attrs,
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            stream = (
                caller.stream_with_tools("test-model", [], [{"type": "function"}])
                if with_tools else caller.stream_plain("test-model", [])
            )
            items = []
            with pytest.raises(LLMEmptyStreamError, match="empty response"):
                async for item in stream:
                    items.append(item)

        assert items == []
        assert completion.call_count == expected_calls
        assert all(stream.closed for stream in streams)
        assert caplog.text.count("completed with zero content chunks and no tool calls after ") == expected_calls
        assert mock_sleep.call_count == expected_calls - 1
        attrs = set_attrs.call_args.args[1]
        assert attrs["error_type"] == "LLMEmptyStreamError"
        assert attrs["chunk_count"] == 0
        assert attrs["output_chars"] == 0
        assert attrs["retry_count"] == expected_calls - 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_tools", [False, True])
    async def test_metadata_only_stream_is_empty(self, caller, with_tools, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "0")
        completion = AsyncMock(return_value=_FakeStream([None]))
        with (
            patch.object(streaming_module, "acompletion", completion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            stream = (
                caller.stream_with_tools("test-model", [], [{"type": "function"}])
                if with_tools else caller.stream_plain("test-model", [])
            )
            with pytest.raises(LLMEmptyStreamError):
                async for _ in stream:
                    pass
        assert completion.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_empty_detection_is_per_attempt(self, mock_sleep, caller):
        completion = AsyncMock(side_effect=[
            _FakeStream([None], error=_rate_limit_exc()),
            _FakeStream([]),
            _FakeStream(["recovered"]),
        ])
        with (
            patch.object(streaming_module, "acompletion", completion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            items = [item async for item in caller.stream_plain("test-model", [])]
        assert items == ["recovered"]
        assert completion.call_count == 3

    @pytest.mark.asyncio
    async def test_tool_only_stream_is_not_empty(self, caller):
        async def tool_stream():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    index=0, id="call_1",
                    function=SimpleNamespace(name="test_tool", arguments="{}"),
                )],
            ))])

        completion = AsyncMock(return_value=tool_stream())
        with (
            patch.object(streaming_module, "acompletion", completion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            items = [item async for item in caller.stream_with_tools(
                "test-model", [], [{"type": "function"}],
            )]
        assert len(items) == 1
        assert items[0].tool_calls[0].function.name == "test_tool"
        assert completion.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_plain_retries_rate_limit_before_first_token(self, mock_sleep, caller):
        """A rate limit at stream establishment is retried, then tokens flow."""
        success = _FakeStream(["Hello", " ", "world"])
        mock_acompletion = AsyncMock(side_effect=[_rate_limit_exc(), success])

        with (
            patch.object(streaming_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            tokens = [
                t async for t in caller.stream_plain(
                    "test-model", [{"role": "user", "content": "hi"}],
                )
            ]

        assert tokens == ["Hello", " ", "world"]
        assert mock_acompletion.call_count == 2
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    async def test_stream_plain_no_retry_after_first_token(self, caller):
        """A mid-stream failure after a yielded token must surface, not retry."""
        failing = _FakeStream(["partial"], error=_rate_limit_exc())
        mock_acompletion = AsyncMock(return_value=failing)

        with (
            patch.object(streaming_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            collected = []
            with pytest.raises(RateLimitError):
                async for token in caller.stream_plain(
                    "test-model", [{"role": "user", "content": "hi"}],
                ):
                    collected.append(token)

        assert collected == ["partial"]
        assert mock_acompletion.call_count == 1
        assert failing.closed is True

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_plain_exhausts_retries(self, mock_sleep, caller, monkeypatch):
        """With LLM_MAX_RETRIES=1, two attempts then the domain error."""
        monkeypatch.setenv("LLM_MAX_RETRIES", "1")
        always_fail = AlwaysFailLLMResponse(exception=_rate_limit_exc())
        mock_acompletion = AsyncMock(side_effect=always_fail.side_effect_list())

        with (
            patch.object(streaming_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            with pytest.raises(RateLimitError):
                async for _ in caller.stream_plain(
                    "test-model", [{"role": "user", "content": "hi"}],
                ):
                    pass

        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_with_tools_retries_before_any_yield(self, mock_sleep, caller):
        """Tool streams retry on transient failure before any text is yielded."""
        success = _FakeStream(["tok"])
        mock_acompletion = AsyncMock(side_effect=[_rate_limit_exc(), success])
        tools_schema = [{"type": "function", "function": {"name": "test_tool"}}]

        with (
            patch.object(streaming_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            items = [
                item async for item in caller.stream_with_tools(
                    "test-model", [{"role": "user", "content": "hi"}], tools_schema,
                )
            ]

        assert items[0] == "tok"
        assert isinstance(items[-1], LLMResponse)
        assert items[-1].content == "tok"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    async def test_stream_with_tools_no_retry_after_text(self, caller):
        """Once text streamed to the user, a later failure is not retried."""
        failing = _FakeStream(["partial"], error=_rate_limit_exc())
        mock_acompletion = AsyncMock(return_value=failing)
        tools_schema = [{"type": "function", "function": {"name": "test_tool"}}]

        with (
            patch.object(streaming_module, "acompletion", mock_acompletion),
            patch.object(caller, "_get_litellm_model_name", return_value="test-model"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            collected = []
            with pytest.raises(RateLimitError):
                async for item in caller.stream_with_tools(
                    "test-model", [{"role": "user", "content": "hi"}], tools_schema,
                ):
                    collected.append(item)

        assert collected == ["partial"]
        assert mock_acompletion.call_count == 1
