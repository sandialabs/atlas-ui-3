"""Non-sensitive diagnostics for plain (summarization) LLM calls (issue #1138).

``call_plain`` used to collapse a missing reply into an empty string, so a
compaction failure said only "empty checkpoint": no finish reason, no token
counts, no way to tell a blank reply from an output budget a reasoning model
drained before it wrote anything. The LiteLLM caller now returns that metadata
next to the text and logs it when the reply is empty.

Everything provider text can reach is checked here: only counts, structure
flags and allow-listed finish reasons are kept, and neither content nor
reasoning text nor a host name is ever copied into the diagnostics.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.modules.llm import litellm_caller as caller_module
from atlas.modules.llm.litellm_caller import LiteLLMCaller, describe_plain_response
from atlas.modules.llm.models import PlainCompletion


@pytest.fixture
def caller():
    return LiteLLMCaller(llm_config=MagicMock())


def _response(*, content, finish_reason, reasoning_tokens=None, reasoning_content=None,
              model="openai/gpt-4o", completion_tokens=20):
    message = SimpleNamespace(content=content, reasoning_content=reasoning_content)
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=completion_tokens, total_tokens=100 + completion_tokens,
        completion_tokens_details=(
            SimpleNamespace(reasoning_tokens=reasoning_tokens) if reasoning_tokens is not None else None
        ),
    )
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)],
        usage=usage,
    )


class TestDescribePlainResponse:
    """What a caller can learn from a response without reading its content."""

    def test_empty_content_reports_structure_and_usage(self):
        diagnostics = describe_plain_response(
            _response(content=None, finish_reason="stop"), "atlas-key")

        assert diagnostics["model"] == "atlas-key"
        assert diagnostics["litellm_model"] == "openai/gpt-4o"
        assert diagnostics["provider"] == "openai"
        assert diagnostics["choice_count"] == 1
        assert diagnostics["content_present"] is False
        assert diagnostics["content_chars"] == 0
        assert diagnostics["finish_reason"] == "stop"
        assert diagnostics["prompt_tokens"] == 100
        assert diagnostics["completion_tokens"] == 20

    def test_exhausted_budget_reports_the_finish_reason_and_reasoning_tokens(self):
        diagnostics = describe_plain_response(
            _response(content=None, finish_reason="length", reasoning_tokens=2950,
                      completion_tokens=3000), "m")

        assert diagnostics["finish_reason"] == "length"
        assert diagnostics["reasoning_tokens"] == 2950

    def test_reasoning_only_reply_is_flagged_without_its_text(self):
        diagnostics = describe_plain_response(
            _response(content=None, finish_reason="stop",
                      reasoning_content="internal thinking about the transcript"), "m")

        assert diagnostics["has_reasoning_content"] is True
        assert "internal thinking" not in str(diagnostics)

    def test_unknown_finish_reason_is_not_interpolated(self):
        diagnostics = describe_plain_response(
            _response(content=None, finish_reason="eos [host.internal/v1]"), "m")

        assert diagnostics["finish_reason"] == "other"
        assert "host.internal" not in str(diagnostics)

    def test_missing_choices_and_usage_are_tolerated(self):
        diagnostics = describe_plain_response(SimpleNamespace(model=""), "m")

        assert diagnostics["choice_count"] == 0
        assert diagnostics["content_present"] is False
        assert diagnostics["finish_reason"] == "other"
        assert diagnostics["prompt_tokens"] is None

    def test_content_is_never_copied_into_the_diagnostics(self):
        secret = "CONFIDENTIAL-TRANSCRIPT-DETAIL"
        diagnostics = describe_plain_response(
            _response(content=secret, finish_reason="stop"), "m")

        assert diagnostics["content_present"] is True
        assert diagnostics["content_chars"] == len(secret)
        assert secret not in str(diagnostics)

    def test_url_shaped_model_alias_is_sanitized_for_logging(self):
        diagnostics = describe_plain_response(
            _response(content=None, finish_reason="stop", model="openai/http://10.0.0.5:4000/v1"), "m")

        assert "http://10.0.0.5" not in str(diagnostics)


class TestCallPlainDetailed:
    """The plain call reports the metadata a compaction failure needs."""

    @pytest.mark.asyncio
    async def test_empty_reply_carries_diagnostics_and_is_logged(self, caller, caplog):
        response = _response(content=None, finish_reason="length", reasoning_tokens=2990,
                             completion_tokens=3000)
        with (
            patch.object(caller_module, "acompletion", AsyncMock(return_value=response)),
            patch.object(caller, "_get_litellm_model_name", return_value="openai/gpt-4o"),
            patch.object(caller, "_get_model_kwargs", return_value={"max_tokens": 3000}),
        ):
            result = await caller.call_plain_detailed("m", [{"role": "user", "content": "hi"}])

        assert isinstance(result, PlainCompletion)
        assert result.text == ""
        assert result.diagnostics["finish_reason"] == "length"
        assert result.diagnostics["reasoning_tokens"] == 2990
        assert result.diagnostics["requested_max_tokens"] == 3000
        assert "returned no content" in caplog.text
        assert "hi" not in caplog.text

    @pytest.mark.asyncio
    async def test_populated_reply_is_logged_at_info_not_warning(self, caller, caplog):
        response = _response(content="## Goal\ncompact", finish_reason="stop")
        with (
            patch.object(caller_module, "acompletion", AsyncMock(return_value=response)),
            patch.object(caller, "_get_litellm_model_name", return_value="openai/gpt-4o"),
            patch.object(caller, "_get_model_kwargs", return_value={"max_tokens": 3000}),
        ):
            result = await caller.call_plain_detailed("m", [{"role": "user", "content": "hi"}])

        assert result.text == "## Goal\ncompact"
        assert "returned no content" not in caplog.text

    @pytest.mark.asyncio
    async def test_call_plain_still_returns_just_the_text(self, caller):
        response = _response(content="plain text", finish_reason="stop")
        with (
            patch.object(caller_module, "acompletion", AsyncMock(return_value=response)),
            patch.object(caller, "_get_litellm_model_name", return_value="openai/gpt-4o"),
            patch.object(caller, "_get_model_kwargs", return_value={"max_tokens": 3000}),
        ):
            assert await caller.call_plain("m", [{"role": "user", "content": "hi"}]) == "plain text"
