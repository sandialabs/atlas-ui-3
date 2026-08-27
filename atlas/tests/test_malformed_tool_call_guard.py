"""A truncated tool call must not be executed or written into the conversation.

When a model runs out of output tokens partway through a tool call, the
``arguments`` string arrives as a fragment -- ``{"filename": "long-na`` -- that
is not valid JSON. Atlas used to accumulate that fragment verbatim, hand it to
the tool executor, and append it to the assistant message. Because providers
re-parse every tool call in the history on the next request, the fragment then
made *every* subsequent turn fail with

    OpenAIException - Unterminated string starting at: line 1 column 73

which no retry could clear: the poison was in the conversation, not the
request. The guard drops unparseable calls at the point they are accumulated,
and fails the turn with an accurate, retryable message when nothing usable is
left.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.utilities.error_handler import (
    classify_llm_error,
    error_type_for,
)
from atlas.domain.errors import LLMMalformedToolCallError, LLMServiceError
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.models import partition_tool_calls_by_json_validity

# A real fragment shape: the model stopped mid-string inside the arguments.
TRUNCATED_ARGS = '{"filename": "1787784579_62f6178a_MC5094_4A1200_3.0.0_0_topic'


def _caller() -> LiteLLMCaller:
    mock_config = MagicMock()
    mock_config.models = {}
    caller = LiteLLMCaller(llm_config=mock_config)
    caller._get_litellm_model_name = MagicMock(return_value="openai/gemma")
    caller._get_model_kwargs = MagicMock(return_value={"max_tokens": 100})
    caller._prepare_messages = MagicMock(side_effect=lambda m, msgs: msgs)
    return caller


def _chunk(index=0, tool_id=None, name=None, args=None, finish_reason=None):
    """One streaming chunk carrying a tool_call delta."""
    fn = SimpleNamespace(name=name, arguments=args)
    tc = SimpleNamespace(index=index, id=tool_id, function=fn)
    delta = SimpleNamespace(content=None, tool_calls=[tc])
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)]
    )


async def _drain(caller, chunks):
    """Run stream_with_tools over ``chunks`` and return the final LLMResponse."""

    async def _fake_acompletion(*args, **kwargs):
        async def _gen():
            for chunk in chunks:
                yield chunk
        return _gen()

    final = None
    with patch("atlas.modules.llm.litellm_streaming.acompletion", _fake_acompletion):
        async for item in caller.stream_with_tools(
            "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
        ):
            if not isinstance(item, str):
                final = item
    return final


class TestArgumentPartitioning:
    """The parse check itself: only a non-empty unparseable string is a defect."""

    def test_splits_valid_from_truncated(self):
        good = SimpleNamespace(function=SimpleNamespace(name="a", arguments='{"q": 1}'))
        bad = SimpleNamespace(function=SimpleNamespace(name="b", arguments=TRUNCATED_ARGS))

        valid, malformed = partition_tool_calls_by_json_validity([good, bad])

        assert valid == [good]
        assert malformed == [bad]

    def test_empty_arguments_are_valid(self):
        """Models legitimately emit "" for a tool that takes no arguments."""
        no_args = SimpleNamespace(function=SimpleNamespace(name="now", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity([no_args])

        assert valid == [no_args]
        assert malformed == []

    def test_reads_dict_shaped_tool_calls(self):
        """History round-trips hand back plain dicts, not pydantic models."""
        bad = {"id": "1", "function": {"name": "b", "arguments": TRUNCATED_ARGS}}

        valid, malformed = partition_tool_calls_by_json_validity([bad])

        assert (valid, malformed) == ([], [bad])


@pytest.mark.asyncio
class TestStreamingGuard:
    """stream_with_tools must never emit a tool call it cannot parse."""

    async def test_truncated_only_call_fails_the_turn(self):
        caller = _caller()

        with pytest.raises(LLMMalformedToolCallError) as info:
            await _drain(caller, [
                _chunk(tool_id="call-1", name="read_file", args=TRUNCATED_ARGS),
                _chunk(finish_reason="length"),
            ])

        assert info.value.tool_names == ["read_file"]
        # The user is told to retry -- unlike the old "retrying will not help"
        # bad-request message this failure is transient.
        assert "try again" in info.value.message.lower()
        assert "cut off" in info.value.message.lower()

    async def test_message_does_not_blame_the_token_limit_without_evidence(self):
        """Bad JSON with a normal finish_reason is not a truncation."""
        caller = _caller()

        with pytest.raises(LLMMalformedToolCallError) as info:
            await _drain(caller, [
                _chunk(tool_id="call-1", name="read_file", args="not json at all"),
                _chunk(finish_reason="tool_calls"),
            ])

        assert "cut off" not in info.value.message.lower()
        assert "not valid json" in info.value.message.lower()

    async def test_well_formed_calls_still_run_when_a_sibling_is_truncated(self):
        """One bad call must not cost the user the calls that did arrive intact."""
        caller = _caller()

        final = await _drain(caller, [
            _chunk(index=0, tool_id="ok", name="search", args='{"q": "hi"}'),
            _chunk(index=1, tool_id="bad", name="read_file", args=TRUNCATED_ARGS),
            _chunk(finish_reason="length"),
        ])

        assert final is not None
        assert [tc.id for tc in final.tool_calls] == ["ok"]

    async def test_valid_calls_are_untouched(self):
        caller = _caller()

        final = await _drain(caller, [
            _chunk(tool_id="call-1", name="search", args='{"q":'),
            _chunk(args='"hi"}'),
            _chunk(finish_reason="tool_calls"),
        ])

        assert final.tool_calls[0].function.arguments == '{"q":"hi"}'


@pytest.mark.asyncio
class TestNonStreamingGuard:
    """The same defect reaches the non-streaming path; guard it identically."""

    async def test_truncated_call_fails_the_turn(self):
        caller = _caller()
        message = SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(
                id="call-1", type="function",
                function=SimpleNamespace(name="read_file", arguments=TRUNCATED_ARGS),
            )],
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="length")],
        )
        caller._acompletion_with_retry = AsyncMock(return_value=response)

        with pytest.raises(LLMMalformedToolCallError):
            await caller.call_with_tools(
                "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
            )


class TestErrorClassification:
    """The turn's failure must reach the user as itself, not as a generic fault."""

    def test_classify_preserves_the_message(self):
        error = LLMMalformedToolCallError("The model ran out of room.", tool_names=["x"])

        error_class, user_msg, log_msg = classify_llm_error(error)

        assert error_class is LLMMalformedToolCallError
        assert user_msg == "The model ran out of room."
        assert "unusable tool call" in log_msg

    def test_error_type_sent_to_clients(self):
        assert error_type_for(LLMMalformedToolCallError) == "malformed_tool_call"

    def test_domain_errors_are_not_reclassified(self):
        """_raise_llm_domain_error must pass an already-typed error through.

        The streaming accumulator raises from inside the try block that wraps
        the provider call, so without a pass-through the guard's precise message
        would be demoted to "the LLM service encountered an error".
        """
        error = LLMMalformedToolCallError("The model ran out of room.")

        with pytest.raises(LLMMalformedToolCallError):
            LiteLLMCaller._raise_llm_domain_error(error)

    def test_untyped_errors_still_classify(self):
        with pytest.raises(LLMServiceError):
            LiteLLMCaller._raise_llm_domain_error(RuntimeError("something odd"))
