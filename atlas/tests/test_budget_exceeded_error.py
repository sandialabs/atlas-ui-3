"""LiteLLM budget refusals must be reported as a spent budget, not as an invalid
request or transient throttling.

Since LiteLLM 1.104 an exhausted key/user/team budget is a 422 with
``error.type: "budget_exceeded"``; older builds and
``budget_exceeded_status_code: 429`` use 429, and per-model virtual-key budgets
use 400. Before this, 422 became "the provider rejected this request as
invalid" (with advice to start a new conversation) and 429 became "high
traffic" and was retried with backoff -- both wrong, because every retry is
refused the same way until the budget is raised.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import litellm
import pytest

from atlas.application.chat.agent.streaming_final_answer import stream_final_answer
from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate
from atlas.application.chat.utilities.error_handler import (
    classify_llm_error,
    error_type_for,
)
from atlas.domain.errors import (
    ContextWindowExceededError,
    LLMBadRequestError,
    LLMBudgetExceededError,
    LLMServiceError,
    RateLimitError,
)
from atlas.modules.llm import litellm_caller, litellm_streaming
from atlas.modules.llm.litellm_caller import LiteLLMCaller

BUDGET_MESSAGE = (
    "The spending budget for this model or team has been used up. "
    "Choose a different model or team, or contact your administrator."
)

KEY_BUDGET_BODY = {
    "error": {
        "message": (
            "Budget has been exceeded! Key=lit-8229-exhausted (sk-...X-rQ) "
            "Current cost: 0.0, Max budget: 0.0"
        ),
        "type": "budget_exceeded",
        "param": None,
        "code": "422",
    }
}

TEAM_BUDGET_BODY = {
    "error": {
        "message": (
            "Budget has been exceeded! Team=de35b29e-6ca8-4f47-b804-2b79d07aa99a "
            "Current cost: 3.5e-06, Max budget: 1e-09"
        ),
        "type": "budget_exceeded",
        "param": None,
        "code": "422",
    }
}


def _bad_request(message: str, body=None) -> litellm.BadRequestError:
    return litellm.BadRequestError(
        message=message, model="test-model", llm_provider="openai", body=body
    )


def _rate_limit(message: str, body=None, status: int = 429) -> litellm.RateLimitError:
    response = httpx.Response(status, json=body) if body is not None else None
    return litellm.RateLimitError(
        message=message,
        model="test-model",
        llm_provider="openai",
        response=response,
    )


class TestStructuredBudgetType:
    """`error.type == "budget_exceeded"` wins regardless of HTTP code."""

    def test_422_key_budget(self):
        exc = _bad_request("Budget has been exceeded!", body=KEY_BUDGET_BODY)

        with pytest.raises(LLMBudgetExceededError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert info.value.message == BUDGET_MESSAGE

    def test_429_older_version_or_status_override(self):
        body = {
            "error": {
                "message": "Budget has been exceeded! Key=sk-...X-rQ",
                "type": "budget_exceeded",
                "param": None,
                "code": "429",
            }
        }
        exc = _rate_limit("Budget has been exceeded!", body=body, status=429)

        with pytest.raises(LLMBudgetExceededError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert info.value.message == BUDGET_MESSAGE

    def test_400_per_model_key_budget(self):
        body = {
            "error": {
                "message": "Budget has been exceeded for model gpt-4o.",
                "type": "budget_exceeded",
                "param": None,
                "code": "400",
            }
        }
        exc = _bad_request("Budget has been exceeded for model gpt-4o.", body=body)

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_body_may_be_a_json_string(self):
        exc = _bad_request("Budget has been exceeded!", body=json.dumps(KEY_BUDGET_BODY))

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_numeric_code_in_the_envelope_is_normalised(self):
        body = {
            "error": {
                "message": "Budget has been exceeded!",
                "type": "budget_exceeded",
                "code": 422,
            }
        }
        exc = _bad_request("Budget has been exceeded!", body=body)

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_structured_type_on_the_exception_is_honoured(self):
        """Some litellm versions copy the parsed fields onto the exception."""
        exc = _rate_limit("Budget has been exceeded!")
        exc.type = "budget_exceeded"

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_team_budget_body_is_recognised(self):
        exc = _bad_request("Budget has been exceeded!", body=TEAM_BUDGET_BODY)

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)


class TestUnstructuredFallback:
    """Legacy and provider-routing refusals carry no structured type."""

    def test_routing_budget_429_without_a_type(self):
        body = {
            "error": {
                "message": "Deployment crossed budget for provider azure; refusing request.",
                "type": None,
                "param": None,
                "code": "429",
            }
        }
        exc = _rate_limit("Deployment crossed budget for provider azure.", body=body)

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_legacy_budget_phrase_without_a_body(self):
        exc = _rate_limit("Budget exceeded for this team.")

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_plain_bad_request_with_budget_phrase(self):
        exc = _bad_request("Exceeded budget for this API key.")

        with pytest.raises(LLMBudgetExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_budget_phrase_under_an_unrelated_status_is_not_a_budget_error(self):
        """Only 400/422/429 are budget-refusal statuses; 500 text is unrelated."""
        exc = litellm.APIError(
            status_code=500,
            message="Exceeded budget while rendering the usage report.",
            model="test-model",
            llm_provider="openai",
        )

        with pytest.raises(Exception) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert not isinstance(info.value, LLMBudgetExceededError)

    def test_budget_phrase_without_a_status_is_too_ambiguous(self):
        """Without a structured type or a refusal code, do not guess."""
        with pytest.raises(LLMServiceError) as info:
            LiteLLMCaller._raise_llm_domain_error(Exception("Budget exceeded"))

        assert not isinstance(info.value, LLMBudgetExceededError)


class TestOrdinaryErrorsKeepTheirMeaning:
    def test_token_budget_overflow_is_still_a_context_window_error(self):
        """A token/context allowance is not a spending budget."""
        exc = litellm.ContextWindowExceededError(
            message=(
                "This model's token budget was exceeded: maximum context "
                "length is 8192 tokens."
            ),
            model="test-model",
            llm_provider="openai",
        )

        with pytest.raises(ContextWindowExceededError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_token_budget_message_in_a_400_is_not_a_spending_budget(self):
        exc = _bad_request("Invalid request: token budget exceeded for this response.")

        with pytest.raises(LLMBadRequestError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_thinking_budget_message_is_not_a_spending_budget(self):
        """Claude's extended-thinking budget is a per-request token allowance."""
        exc = _bad_request("Invalid request: thinking budget exceeded max_tokens.")

        with pytest.raises(LLMBadRequestError):
            LiteLLMCaller._raise_llm_domain_error(exc)

    def test_rpm_tpm_429_is_still_a_rate_limit(self):
        exc = litellm.RateLimitError(
            message=(
                "Rate limit reached for gpt-4o on tokens per min (TPM): "
                "Limit 30000, Used 30000."
            ),
            model="test-model",
            llm_provider="openai",
        )

        with pytest.raises(RateLimitError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert "high traffic" in info.value.message

    def test_unrelated_422_is_still_a_bad_request(self):
        exc = _bad_request(
            "Invalid value for 'temperature': must be <= 2.",
            body={"error": {"message": "Invalid value", "type": "invalid_request_error", "code": "422"}},
        )

        with pytest.raises(LLMBadRequestError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert "rejected this request as invalid" in info.value.message


class TestNoRetryAndNoLeak:
    def test_budget_error_is_not_retryable(self):
        exc = _bad_request("Budget has been exceeded!", body=KEY_BUDGET_BODY)

        assert LiteLLMCaller._is_retryable_error(exc) is False
        assert LiteLLMCaller._is_retryable_error(LLMBudgetExceededError(BUDGET_MESSAGE)) is False

    def test_budget_429_is_not_retried_by_the_call_loop(self, monkeypatch):
        attempts = 0

        async def fail(**kwargs):
            nonlocal attempts
            attempts += 1
            raise _rate_limit("Budget has been exceeded!", body=KEY_BUDGET_BODY)

        monkeypatch.setattr(litellm_caller, "acompletion", fail)
        caller = object.__new__(LiteLLMCaller)

        with pytest.raises(litellm.RateLimitError):
            asyncio.run(caller._acompletion_with_retry(model="openai/gpt-4o", messages=[]))

        assert attempts == 1

    def test_user_message_omits_key_team_and_spend(self):
        exc = _bad_request("Budget has been exceeded!", body=KEY_BUDGET_BODY)

        with pytest.raises(LLMBudgetExceededError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        message = info.value.message
        for secret in ("lit-8229-exhausted", "sk-...X-rQ", "Current cost", "Max budget"):
            assert secret not in message

    def test_original_exception_is_chained_for_diagnosis(self):
        exc = _bad_request("Budget has been exceeded!", body=KEY_BUDGET_BODY)

        with pytest.raises(LLMBudgetExceededError) as info:
            LiteLLMCaller._raise_llm_domain_error(exc)

        assert info.value.__cause__ is exc

    def test_classify_preserves_the_budget_message(self):
        err = LLMBudgetExceededError(BUDGET_MESSAGE)

        error_class, user_msg, log_msg = classify_llm_error(err)

        assert error_class is LLMBudgetExceededError
        assert user_msg == BUDGET_MESSAGE
        assert "budget" in log_msg.lower()

    def test_error_type_maps_to_budget_exceeded(self):
        assert error_type_for(LLMBudgetExceededError) == "budget_exceeded"


BUDGET_429_BODY = {
    "error": {
        "message": "Budget has been exceeded! Key=lit-8229-exhausted (sk-...X-rQ)",
        "type": "budget_exceeded",
        "param": None,
        "code": "429",
    }
}


def _streaming_caller() -> LiteLLMCaller:
    """A caller with just the collaborators the streaming mixin needs."""
    caller = object.__new__(LiteLLMCaller)

    async def resolve(model_name, temperature, user_email):
        return "openai/gpt-4o", {"api_base": "http://test", "api_key": "test"}

    caller._resolve_call_target = resolve
    caller._prepare_messages = lambda model_name, messages: messages
    caller._fire_pre_llm_hook = AsyncMock(return_value=None)
    return caller


class TestStreamingBudgetRefusal:
    """The streaming paths share the retry policy and the log noise fix."""

    @pytest.mark.asyncio
    async def test_stream_plain_attempts_a_budget_429_once(self, monkeypatch, caplog):
        attempts = 0

        async def fail(**kwargs):
            nonlocal attempts
            attempts += 1
            raise _rate_limit("Budget has been exceeded!", body=BUDGET_429_BODY)

        monkeypatch.setattr(litellm_streaming, "acompletion", fail)
        monkeypatch.setenv("LLM_MAX_RETRIES", "2")
        caller = _streaming_caller()

        with caplog.at_level(logging.WARNING), pytest.raises(LLMBudgetExceededError):
            async for _ in caller.stream_plain("test-model", [{"role": "user", "content": "hi"}]):
                pass

        assert attempts == 1
        streaming_errors = [
            record
            for record in caplog.records
            if record.name == "atlas.modules.llm.litellm_streaming"
            and record.levelno >= logging.ERROR
        ]
        assert streaming_errors == [], "a budget refusal must not log an ERROR traceback"

    @pytest.mark.asyncio
    async def test_stream_with_tools_attempts_a_budget_429_once(self, monkeypatch):
        attempts = 0

        async def fail(**kwargs):
            nonlocal attempts
            attempts += 1
            raise _rate_limit("Budget has been exceeded!", body=BUDGET_429_BODY)

        monkeypatch.setattr(litellm_streaming, "acompletion", fail)
        monkeypatch.setenv("LLM_MAX_RETRIES", "2")
        caller = _streaming_caller()
        tools_schema = [
            {"type": "function", "function": {"name": "calculator", "parameters": {}}}
        ]

        with pytest.raises(LLMBudgetExceededError):
            async for _ in caller.stream_with_tools(
                "test-model", [{"role": "user", "content": "hi"}], tools_schema
            ):
                pass

        assert attempts == 1

    @pytest.mark.asyncio
    async def test_stream_plain_still_retries_a_real_rpm_429(self, monkeypatch):
        attempts = 0

        async def fail(**kwargs):
            nonlocal attempts
            attempts += 1
            raise litellm.RateLimitError(
                message="Rate limit reached for gpt-4o on tokens per min (TPM).",
                model="test-model",
                llm_provider="openai",
            )

        monkeypatch.setattr(litellm_streaming, "acompletion", fail)
        monkeypatch.setenv("LLM_MAX_RETRIES", "1")
        caller = _streaming_caller()

        with pytest.raises(RateLimitError):
            async for _ in caller.stream_plain("test-model", [{"role": "user", "content": "hi"}]):
                pass

        assert attempts == 2


class TestBudgetErrorBypassesTheNonStreamingFallback:
    """A fallback would send the identical refused request and fake success."""

    @staticmethod
    def _publisher():
        publisher = AsyncMock()
        publisher.publish_token_stream = AsyncMock()
        publisher.publish_chat_response = AsyncMock()
        return publisher

    @pytest.mark.asyncio
    async def test_stream_and_accumulate_propagates_instead_of_falling_back(self):
        async def failing_stream():
            raise LLMBudgetExceededError(BUDGET_MESSAGE)
            yield  # pragma: no cover - makes this an async generator

        publisher = self._publisher()
        fallback = AsyncMock(return_value="a simulated answer")

        with pytest.raises(LLMBudgetExceededError):
            await stream_and_accumulate(
                failing_stream(),
                publisher,
                fallback_fn=fallback,
                context_label="plain",
            )

        fallback.assert_not_awaited()
        publisher.publish_chat_response.assert_not_awaited()
        # The open token bubble is still closed before the error propagates.
        publisher.publish_token_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_stream_final_answer_propagates_instead_of_falling_back(self):
        async def failing_stream(model, messages, temperature=None, user_email=None):
            raise LLMBudgetExceededError(BUDGET_MESSAGE)
            yield  # pragma: no cover - makes this an async generator

        llm = SimpleNamespace(stream_plain=failing_stream, call_plain=AsyncMock(return_value="x"))
        publisher = self._publisher()

        with pytest.raises(LLMBudgetExceededError):
            await stream_final_answer(llm, publisher, "test-model", [], 0.7, None)

        llm.call_plain.assert_not_awaited()
