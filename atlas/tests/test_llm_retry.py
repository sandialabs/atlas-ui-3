"""Tests for LLM auto-retry with exponential backoff.

Verifies that transient errors (rate limit, timeout, service errors) are
retried up to MAX_LLM_RETRIES times, while non-retryable errors (auth)
are raised immediately.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

from atlas.domain.errors import (
    ContextWindowExceededError,
    LLMAuthenticationError,
    LLMTimeoutError,
    RateLimitError,
)
from atlas.modules.llm import litellm_caller as caller_module
from atlas.modules.llm.litellm_caller import (
    MAX_LLM_RETRIES,
    LiteLLMCaller,
)
from atlas.tests.mocks.mock_flaky_llm import (
    AlwaysFailLLMResponse,
    FlakyLLMResponse,
    make_llm_response,
)


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

        assert mock_acompletion.call_count == MAX_LLM_RETRIES + 1
        assert mock_sleep.call_count == MAX_LLM_RETRIES

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

        assert mock_acompletion.call_count == MAX_LLM_RETRIES + 1


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
                    data_sources=["src"], user_email="test@test.com",
                )

    @pytest.mark.asyncio
    async def test_call_with_rag_and_tools_reraises_timeout(self, caller):
        """Timeout from inner call_with_tools should not trigger RAG fallback."""
        with (
            patch.object(caller, "call_with_tools", side_effect=LLMTimeoutError("timed out")),
            patch.object(caller, "_get_litellm_model_name", return_value="test"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            caller._rag_service = MagicMock()
            caller._rag_service.query = AsyncMock(return_value=MagicMock(
                content="rag content", metadata=None, is_completion=False,
            ))

            with pytest.raises(LLMTimeoutError):
                await caller.call_with_rag_and_tools(
                    "test", [{"role": "user", "content": "hi"}],
                    data_sources=["src"],
                    tools_schema=[{"type": "function", "function": {"name": "t"}}],
                    user_email="test@test.com",
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
                    data_sources=["src"], user_email="test@test.com",
                )

    @pytest.mark.asyncio
    async def test_call_with_rag_and_tools_reraises_context_window_exceeded(self, caller):
        """Context window exceeded from inner call_with_tools should not trigger RAG fallback."""
        with (
            patch.object(caller, "call_with_tools", side_effect=ContextWindowExceededError("too long")),
            patch.object(caller, "_get_litellm_model_name", return_value="test"),
            patch.object(caller, "_get_model_kwargs", return_value={}),
        ):
            caller._rag_service = MagicMock()
            caller._rag_service.query = AsyncMock(return_value=MagicMock(
                content="rag content", metadata=None, is_completion=False,
            ))

            with pytest.raises(ContextWindowExceededError):
                await caller.call_with_rag_and_tools(
                    "test", [{"role": "user", "content": "hi"}],
                    data_sources=["src"],
                    tools_schema=[{"type": "function", "function": {"name": "t"}}],
                    user_email="test@test.com",
                )
