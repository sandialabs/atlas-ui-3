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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.utilities.error_handler import (
    classify_llm_error,
    error_type_for,
)
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.domain.errors import LLMMalformedToolCallError, LLMServiceError
from atlas.domain.messages.models import Message, MessageRole
from atlas.interfaces.llm import LLMResponse
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.tool_call_guard import (
    dropped_call_warning,
    partition_tool_calls_by_json_validity,
    repair_structural_json,
    response_was_cut_off,
)

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
        # The advice must fit an *output* limit: shortening the input cannot
        # relieve one, so "one thing at a time" leads.
        assert "one thing at a time" in info.value.message.lower()

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


class _StreamThenFail:
    """An LLM whose stream emits narration and then fails the malformed check.

    The real shape of the bug: the model says "Let me read that file", starts a
    tool call, and runs out of tokens mid-argument. The guard raises only after
    the narration has already been yielded.
    """

    def __init__(self, narration="Let me read that file for you."):
        self.narration = narration

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        if self.narration:
            yield self.narration
        raise LLMMalformedToolCallError(
            "The model ran out of room while writing its tool call.",
            tool_names=["read_file"],
        )

    async def stream_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                        user_email, tool_choice="auto", temperature=0.7):
        async for item in self.stream_with_tools(model, messages, tools_schema, tool_choice):
            yield item

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield "unused"


@pytest.mark.asyncio
class TestNarrationDoesNotHideTheFailure:
    """Partial text must not turn a skipped tool call into a "successful" answer.

    Both streaming consumers swallow a mid-stream error once any text has been
    streamed, on the reasoning that partial output beats none. That is right for
    a transport fault, but wrong here: the model announced work, the call was
    dropped, and reporting the narration as the final answer shows the user a
    reply that silently skipped what it promised.
    """

    async def test_agentic_loop_propagates_after_partial_text(self):
        from atlas.application.chat.agent.agentic_loop import AgenticLoop

        loop = AgenticLoop(llm=_StreamThenFail(), tool_manager=None, prompt_provider=None)
        publisher = AsyncMock()
        context = MagicMock()
        context.user_email = "u@example.com"

        with pytest.raises(LLMMalformedToolCallError):
            await loop._call_llm_streaming(
                "gemma", [{"role": "user", "content": "read it"}],
                [{"type": "function"}], None, context, 0.7, publisher,
            )

        # The open bubble is closed before the error, so the UI does not leave a
        # live cursor blinking underneath it.
        assert publisher.publish_token_stream.await_args.kwargs["is_last"] is True

    async def test_tools_mode_reports_the_error_after_partial_text(self):
        from atlas.application.chat.modes.tools import ToolsModeRunner

        publisher = AsyncMock()
        tool_manager = MagicMock()
        tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
        runner = ToolsModeRunner(
            llm=_StreamThenFail(),
            tool_manager=tool_manager,
            event_publisher=publisher,
            config_manager=None,
        )
        session = MagicMock()
        session.history = MagicMock()
        session.session_id = "s1"
        session.files = {}

        with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
            mock_te.build_files_manifest = MagicMock(return_value=None)
            await runner.run_streaming(
                session=session,
                model="gemma",
                messages=[{"role": "user", "content": "read it"}],
                selected_tools=["read_file"],
            )

        errors = [
            call.args[0] for call in publisher.send_json.await_args_list
            if call.args and call.args[0].get("type") == "error"
        ]
        assert errors, "the dropped tool call must be reported, not hidden by narration"
        assert errors[0]["error_type"] == "malformed_tool_call"


class TestTruncatedBeforeAnyArguments:
    """Parseability alone cannot see a call cut off before its first argument.

    Empty arguments parse fine as "no arguments" and would execute with ``{}``.
    For a tool whose parameters are all optional that is not a visible failure --
    it is the wrong action performed silently.
    """

    def test_empty_arguments_are_malformed_when_the_response_was_truncated(self):
        cut_off = SimpleNamespace(function=SimpleNamespace(name="delete_all", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity([cut_off], finish_reason="length")

        assert (valid, malformed) == ([], [cut_off])

    def test_empty_arguments_stay_valid_when_the_response_completed(self):
        """A no-argument tool call is legitimate when nothing was cut off."""
        no_args = SimpleNamespace(function=SimpleNamespace(name="now", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity([no_args], finish_reason="tool_calls")

        assert (valid, malformed) == ([no_args], [])

    def test_only_the_last_call_is_judged_by_truncation(self):
        """Truncation can only have reached the final call.

        Earlier calls in the same response completed before the limit was hit, so
        a genuine no-argument call among them must still be honoured.
        """
        earlier = SimpleNamespace(function=SimpleNamespace(name="now", arguments=""))
        last = SimpleNamespace(function=SimpleNamespace(name="delete_all", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity(
            [earlier, last], finish_reason="length",
        )

        assert valid == [earlier]
        assert malformed == [last]


@pytest.mark.asyncio
class TestHistoryIsNeverPoisoned:
    """The defect was in what got *written*, not in what the turn returned.

    Every tool call that survives must survive the round-trip the agentic loop
    puts it through -- serialized into the assistant message and re-parsed by the
    provider on each later request. That is the assertion the original bug would
    have failed.
    """

    async def test_assembled_assistant_message_always_reparses(self):
        from atlas.application.chat.agent.agentic_loop import _to_tool_call_dict

        caller = _caller()
        final = await _drain(caller, [
            _chunk(index=0, tool_id="ok", name="search", args='{"q": "hi"}'),
            _chunk(index=1, tool_id="bad", name="read_file", args=TRUNCATED_ARGS),
            _chunk(finish_reason="length"),
        ])

        serialized = [_to_tool_call_dict(tc) for tc in final.tool_calls]
        for tool_call in serialized:
            # Providers re-parse this on every subsequent request. A fragment
            # here is what made the conversation unrecoverable.
            json.loads(tool_call["function"]["arguments"])
        assert TRUNCATED_ARGS not in json.dumps(serialized)


@pytest.mark.asyncio
class TestNonStreamingParity:
    """The non-streaming path must apply the guard on the same terms."""

    async def _call(self, tool_calls, finish_reason):
        caller = _caller()
        message = SimpleNamespace(content=None, tool_calls=tool_calls)
        caller._acompletion_with_retry = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        ))
        return await caller.call_with_tools(
            "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
        )

    async def test_well_formed_sibling_survives(self):
        good = SimpleNamespace(
            id="ok", type="function",
            function=SimpleNamespace(name="search", arguments='{"q": "hi"}'),
        )
        bad = SimpleNamespace(
            id="bad", type="function",
            function=SimpleNamespace(name="read_file", arguments=TRUNCATED_ARGS),
        )

        response = await self._call([good, bad], "length")

        assert [tc.id for tc in response.tool_calls] == ["ok"]

    async def test_empty_arguments_on_a_truncated_response_fail_the_turn(self):
        cut_off = SimpleNamespace(
            id="call-1", type="function",
            function=SimpleNamespace(name="delete_all", arguments=""),
        )

        with pytest.raises(LLMMalformedToolCallError):
            await self._call([cut_off], "length")

    async def test_empty_arguments_on_a_complete_response_still_run(self):
        no_args = SimpleNamespace(
            id="call-1", type="function",
            function=SimpleNamespace(name="now", arguments=""),
        )

        response = await self._call([no_args], "tool_calls")

        assert [tc.id for tc in response.tool_calls] == ["call-1"]


class TestRepairDoesNotInventValues:
    """The executor's JSON repair must not finish a value the model never sent.

    `_try_repair_json` used to close an open string, so the production fragment
    `{"filename": "1787784579_..._topic` became a *different, valid-looking*
    filename and the tool would have executed against the wrong file. A repair
    may complete the shape of the object, never the content of a value.
    """

    def test_a_cut_off_string_value_is_not_guessed_at(self):
        from atlas.application.chat.utilities.tool_executor import _try_repair_json

        assert _try_repair_json(TRUNCATED_ARGS) is None

    def test_structural_repair_still_works(self):
        from atlas.application.chat.utilities.tool_executor import _try_repair_json

        assert _try_repair_json('"q": "hi"') == {"q": "hi"}
        assert _try_repair_json('{"q": "hi"') == {"q": "hi"}


@pytest.mark.asyncio
class TestNonStreamingReturnShape:
    """The all-malformed branch of `call_with_tools` and the shape it returns."""

    async def test_tool_calls_is_none_not_empty(self):
        """An empty list is not the same as no tool calls.

        `has_tool_calls()` is false either way, but an empty `tool_calls` array
        is a shape providers reject on the follow-up request. A provider handing
        back a list with nothing usable in it is the reachable way to get there:
        every entry is filtered out, so nothing is dropped and nothing raises.
        """
        caller = _caller()
        message = SimpleNamespace(content="Here you go.", tool_calls=[None])
        caller._acompletion_with_retry = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
        ))

        response = await caller.call_with_tools(
            "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
        )

        assert response.tool_calls is None
        assert response.has_tool_calls() is False

    async def test_all_malformed_fails_the_turn(self):
        caller = _caller()
        message = SimpleNamespace(
            content="Let me look that up.",
            tool_calls=[SimpleNamespace(
                id="bad", type="function",
                function=SimpleNamespace(name="read_file", arguments=TRUNCATED_ARGS),
            )],
        )
        caller._acompletion_with_retry = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="length")],
        ))

        with pytest.raises(LLMMalformedToolCallError):
            await caller.call_with_tools(
                "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
            )

    async def test_surviving_call_reports_the_dropped_sibling(self):
        caller = _caller()
        good = SimpleNamespace(
            id="ok", type="function",
            function=SimpleNamespace(name="search", arguments='{"q": "hi"}'),
        )
        bad = SimpleNamespace(
            id="bad", type="function",
            function=SimpleNamespace(name="read_file", arguments=TRUNCATED_ARGS),
        )
        message = SimpleNamespace(content=None, tool_calls=[good, bad])
        caller._acompletion_with_retry = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="length")],
        ))

        response = await caller.call_with_tools(
            "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
        )

        assert [tc.id for tc in response.tool_calls] == ["ok"]
        assert response.dropped_tool_calls == ["read_file"]


class TestBraceOnlyDamageIsRepairedNotRejected:
    """Missing braces is a sloppy envelope, not a truncation.

    Some models emit `"q": "hi"` rather than `{"q": "hi"}`. That shape was
    repaired and executed long before this guard existed, so failing the turn
    over it would be a regression. The repair happens here, at accumulation, and
    the repaired string is written back -- which is what keeps history parseable,
    something repairing downstream in the executor never did.
    """

    def test_missing_braces_are_repaired_in_place(self):
        sloppy = SimpleNamespace(function=SimpleNamespace(name="search", arguments='"q": "hi"'))

        valid, malformed = partition_tool_calls_by_json_validity([sloppy])

        assert (valid, malformed) == ([sloppy], [])
        assert json.loads(sloppy.function.arguments) == {"q": "hi"}

    def test_repair_is_written_back_for_dict_shaped_calls(self):
        sloppy = {"id": "1", "function": {"name": "search", "arguments": '{"q": "hi"'}}

        valid, malformed = partition_tool_calls_by_json_validity(
            [sloppy], finish_reason="tool_calls",
        )

        assert (valid, malformed) == ([sloppy], [])
        assert json.loads(sloppy["function"]["arguments"]) == {"q": "hi"}

    def test_a_truncated_last_call_is_not_brace_repaired(self):
        """Balancing braces on a mid-object truncation invents a complete call.

        `{"path": "/data", "recursive": true` closes into valid JSON whose
        remaining keys were silently dropped -- it executes, and it is written to
        history, looking entirely well-formed. Completing the shape is only
        honest when nothing is known to be missing.
        """
        cut_off = SimpleNamespace(function=SimpleNamespace(
            name="list_dir", arguments='{"path": "/data", "recursive": true',
        ))

        valid, malformed = partition_tool_calls_by_json_validity([cut_off], finish_reason="length")

        assert (valid, malformed) == ([], [cut_off])
        # And the arguments are left exactly as they arrived, not rewritten.
        assert cut_off.function.arguments == '{"path": "/data", "recursive": true'

    def test_the_same_shape_is_repaired_when_nothing_was_truncated(self):
        """Without a truncation signal it is just a sloppy envelope."""
        sloppy = SimpleNamespace(function=SimpleNamespace(
            name="list_dir", arguments='{"path": "/data", "recursive": true',
        ))

        valid, malformed = partition_tool_calls_by_json_validity([sloppy], finish_reason="tool_calls")

        assert (valid, malformed) == ([sloppy], [])
        assert json.loads(sloppy.function.arguments) == {"path": "/data", "recursive": True}

    def test_a_cut_off_value_is_still_rejected(self):
        """The repair must not rescue a truncation by inventing the rest."""
        cut_off = SimpleNamespace(
            function=SimpleNamespace(name="read_file", arguments=TRUNCATED_ARGS),
        )

        valid, malformed = partition_tool_calls_by_json_validity([cut_off])

        assert (valid, malformed) == ([], [cut_off])

    def test_executor_and_guard_share_one_repair_definition(self):
        """One policy, one implementation -- the two layers cannot drift apart."""
        from atlas.application.chat.utilities.tool_executor import _try_repair_json

        assert _try_repair_json('"q": "hi"') == {"q": "hi"}
        assert _try_repair_json(TRUNCATED_ARGS) is None


@pytest.mark.asyncio
class TestPartialDropIsAnnounced:
    """A dropped-but-not-fatal call must not be invisible.

    The turn keeps going with the calls that parsed, so without an explicit
    warning neither the user nor the model learns that one was discarded --
    the user just sees an answer built on less work than the model intended.
    """

    async def test_streaming_response_carries_the_dropped_names(self):
        caller = _caller()

        final = await _drain(caller, [
            _chunk(index=0, tool_id="ok", name="search", args='{"q": "hi"}'),
            _chunk(index=1, tool_id="bad", name="read_file", args=TRUNCATED_ARGS),
            _chunk(finish_reason="length"),
        ])

        assert final.dropped_tool_calls == ["read_file"]

    async def test_tools_mode_publishes_a_warning(self):
        from atlas.application.chat.modes.tools import ToolsModeRunner
        from atlas.domain.messages.models import ToolResult

        class PartialDropLLM:
            async def stream_with_tools(self, model, messages, tools_schema,
                                        tool_choice="auto", temperature=0.7, user_email=None):
                yield LLMResponse(
                    content="", model_used="gemma",
                    tool_calls=[SimpleNamespace(
                        id="ok", type="function",
                        function=SimpleNamespace(name="search", arguments='{"q": "hi"}'))],
                    dropped_tool_calls=["read_file"],
                )

            async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
                yield "Here is what I found."

        publisher = AsyncMock()
        tool_manager = MagicMock()
        tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
        runner = ToolsModeRunner(
            llm=PartialDropLLM(), tool_manager=tool_manager,
            event_publisher=publisher, config_manager=None,
        )
        session = MagicMock()
        session.history = MagicMock()
        session.session_id = "s1"
        session.files = {}

        async def _execute(tool_calls, session_context, tool_manager,
                           update_callback=None, config_manager=None, skip_approval=False):
            return [ToolResult(tool_call_id=tc.id, content="ok", success=True)
                    for tc in tool_calls]

        with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
            mock_te.execute_multiple_tools = _execute
            mock_te.build_files_manifest = MagicMock(return_value=None)
            await runner.run_streaming(
                session=session, model="gemma",
                messages=[{"role": "user", "content": "find it"}],
                selected_tools=["search"],
            )

        warned = [c.kwargs.get("message", "") for c in publisher.publish_warning.await_args_list]
        assert warned, "a dropped sibling call was never announced"
        assert "read_file" in warned[0]


@pytest.mark.asyncio
class TestTheTurnIsNeverSavedEmpty:
    """Failing the turn must not also erase what the user already saw."""

    async def test_tools_mode_persists_the_narration_it_streamed(self):
        from atlas.application.chat.modes.tools import ToolsModeRunner

        publisher = AsyncMock()
        tool_manager = MagicMock()
        tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
        runner = ToolsModeRunner(
            llm=_StreamThenFail(), tool_manager=tool_manager,
            event_publisher=publisher, config_manager=None,
        )
        session = MagicMock()
        session.history = MagicMock()
        session.session_id = "s1"
        session.files = {}

        with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
            mock_te.build_files_manifest = MagicMock(return_value=None)
            await runner.run_streaming(
                session=session, model="gemma",
                messages=[{"role": "user", "content": "read it"}],
                selected_tools=["read_file"],
            )

        saved = [c.args[0].content for c in session.history.add_message.call_args_list]
        assert any("Let me read that file" in text for text in saved), (
            "text the user watched stream in was lost on reload"
        )

    async def test_agent_mode_closes_the_turn(self):
        from atlas.application.chat.agent.factory import AgentLoopFactory
        from atlas.application.chat.modes.agent import (
            MALFORMED_TOOL_CALL_TURN_CONTENT_INVALID_JSON,
            AgentModeRunner,
        )
        from atlas.domain.sessions.models import Session

        session = Session()
        session.history.add_message(Message(role=MessageRole.USER, content="read it"))

        connection = MagicMock()
        connection.send_json = AsyncMock()
        factory = AgentLoopFactory(
            llm=_StreamThenFail(), tool_manager=MagicMock(), connection=connection,
        )
        runner = AgentModeRunner(agent_loop_factory=factory, event_publisher=AsyncMock())

        with pytest.raises(LLMMalformedToolCallError):
            await runner.run(
                session=session, model="gemma",
                messages=[{"role": "user", "content": "read it"}],
                selected_tools=["read_file"],
                selected_data_sources=None,
                max_steps=3,
            )

        assistant = [m for m in session.history.messages if m.role == MessageRole.ASSISTANT]
        assert assistant, "the turn was saved with no assistant reply at all"
        # This failure carried no truncation flag, so the persisted text must
        # not assert one -- it is written into history and read back later.
        assert assistant[-1].content == MALFORMED_TOOL_CALL_TURN_CONTENT_INVALID_JSON
        assert "cut off" not in assistant[-1].content

    async def test_agent_mode_says_which_failure_it_was(self):
        """The turn-closing text is persisted, so it must name the real cause."""
        from atlas.application.chat.agent.factory import AgentLoopFactory
        from atlas.application.chat.modes.agent import (
            MALFORMED_TOOL_CALL_TURN_CONTENT,
            AgentModeRunner,
        )
        from atlas.domain.sessions.models import Session

        class TruncatedLLM:
            async def stream_with_tools(self, model, messages, tools_schema,
                                        tool_choice="auto", temperature=0.7, user_email=None):
                raise LLMMalformedToolCallError(
                    "The model ran out of room.", tool_names=["read_file"], truncated=True,
                )
                yield  # pragma: no cover - generator marker

        session = Session()
        session.history.add_message(Message(role=MessageRole.USER, content="read it"))
        connection = MagicMock()
        connection.send_json = AsyncMock()
        runner = AgentModeRunner(
            agent_loop_factory=AgentLoopFactory(
                llm=TruncatedLLM(), tool_manager=MagicMock(), connection=connection,
            ),
            event_publisher=AsyncMock(),
        )

        with pytest.raises(LLMMalformedToolCallError):
            await runner.run(
                session=session, model="gemma",
                messages=[{"role": "user", "content": "read it"}],
                selected_tools=["read_file"], selected_data_sources=None, max_steps=3,
            )

        assistant = [m for m in session.history.messages if m.role == MessageRole.ASSISTANT]
        assert assistant[-1].content == MALFORMED_TOOL_CALL_TURN_CONTENT


class TestWarningCopy:
    """The warning has to say what actually happened, in the right number."""

    def test_truncation_and_bad_json_read_differently(self):
        truncated = dropped_call_warning(["read_file"], truncated=True)
        unparseable = dropped_call_warning(["read_file"], truncated=False)

        assert "ran out of room" in truncated
        assert "valid JSON" not in truncated
        assert "valid JSON" in unparseable
        assert "ran out of room" not in unparseable

    def test_it_pluralizes(self):
        one = dropped_call_warning(["a"], truncated=True)
        two = dropped_call_warning(["a", "b"], truncated=True)

        assert "call to 'a'" in one and " it " in f" {one} "
        assert "calls to 'a', 'b'" in two
        assert "they" in two

    def test_it_does_not_claim_the_other_calls_already_ran(self):
        """The warning is published before the surviving calls execute."""
        warning = dropped_call_warning(["read_file"], truncated=True)

        assert "ran normally" not in warning
        assert "continuing" in warning


class TestLogSafety:
    """Values interpolated into the drop log line are neutralized first."""

    def test_a_newline_bearing_tool_name_cannot_forge_a_log_line(self):
        forged = "read_file\nERROR:root:granted admin"

        assert "\n" not in sanitize_for_logging(forged)
        assert "granted admin" in sanitize_for_logging(forged)

    def test_finish_reason_is_allow_listed(self):
        from atlas.modules.llm.litellm_streaming import KNOWN_FINISH_REASONS

        assert "length" in KNOWN_FINISH_REASONS
        # Provider text outside the vocabulary is reported as "other" rather
        # than interpolated, so it cannot carry anything into the log.
        assert "eos\nINFO:root:spoofed" not in KNOWN_FINISH_REASONS


@pytest.mark.asyncio
class TestExecutorRefusesUnparseableArguments:
    """The executor must not fall back to running the tool with no arguments."""

    async def test_unparseable_arguments_fail_the_call(self):
        from atlas.application.chat.utilities.tool_executor import prepare_tool_arguments
        from atlas.domain.errors import ToolError

        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="delete_everything", arguments=TRUNCATED_ARGS),
        )

        with pytest.raises(ToolError):
            prepare_tool_arguments(tool_call, {"user_email": "u@example.com"}, None)

    async def test_repairable_arguments_still_run(self):
        from atlas.application.chat.utilities.tool_executor import prepare_tool_arguments

        tool_call = SimpleNamespace(
            id="call-1", function=SimpleNamespace(name="search", arguments='"q": "hi"'),
        )

        args = prepare_tool_arguments(tool_call, {"user_email": "u@example.com"}, None)

        assert args["q"] == "hi"


@pytest.mark.asyncio
async def test_agent_mode_publishes_the_dropped_call_warning():
    """Agent mode must announce a partial drop just as tools mode does."""
    from atlas.application.chat.agent.agentic_loop import AgenticLoop

    class PartialDropLLM:
        async def stream_with_tools(self, model, messages, tools_schema,
                                    tool_choice="auto", temperature=0.7, user_email=None):
            yield LLMResponse(
                content="", model_used="gemma",
                tool_calls=[SimpleNamespace(
                    id="ok", type="function",
                    function=SimpleNamespace(name="search", arguments='{"q": "hi"}'))],
                dropped_tool_calls=["read_file"],
                dropped_tool_calls_truncated=True,
            )

    loop = AgenticLoop(llm=PartialDropLLM(), tool_manager=None, prompt_provider=None)
    publisher = AsyncMock()
    context = MagicMock()
    context.user_email = "u@example.com"

    await loop._call_llm_streaming(
        "gemma", [{"role": "user", "content": "find it"}],
        [{"type": "function"}], None, context, 0.7, publisher,
    )

    warned = [c.kwargs.get("message", "") for c in publisher.publish_warning.await_args_list]
    assert warned, "agent mode dropped a call without telling anyone"
    assert "read_file" in warned[0]
    assert "ran out of room" in warned[0]


@pytest.mark.asyncio
async def test_agent_mode_keeps_the_narration_it_streamed():
    """Agent mode kept nothing on failure while tools mode kept its narration."""
    from atlas.application.chat.agent.agentic_loop import AgenticLoop

    loop = AgenticLoop(llm=_StreamThenFail(), tool_manager=None, prompt_provider=None)
    publisher = AsyncMock()
    context = MagicMock()
    context.user_email = "u@example.com"
    context.history = MagicMock()

    with pytest.raises(LLMMalformedToolCallError):
        await loop._call_llm_streaming(
            "gemma", [{"role": "user", "content": "read it"}],
            [{"type": "function"}], None, context, 0.7, publisher,
        )

    saved = [c.args[0] for c in context.history.add_message.call_args_list]
    assert saved, "narration the user watched stream in was discarded"
    assert "Let me read that file" in saved[0].content
    assert saved[0].metadata["incomplete"] is True
    assert saved[0].metadata["message_type"] == "agent_intermediate"


class TestWhatCountsAsCutOff:
    """An output limit is not the only way a response stops mid-thought."""

    def test_clean_finishes_are_not_truncations(self):
        assert response_was_cut_off("stop") is False
        assert response_was_cut_off("tool_calls") is False

    def test_content_filter_and_unknown_reasons_are(self):
        """Both can stop a response mid-object, so the last call is suspect."""
        assert response_was_cut_off("length") is True
        assert response_was_cut_off("content_filter") is True
        assert response_was_cut_off("guardrail_intervened") is True

    def test_a_missing_reason_is_treated_as_clean(self):
        """Providers routinely omit it; treating that as truncation would drop
        legitimate no-argument calls across the board."""
        assert response_was_cut_off(None) is False

    def test_a_content_filtered_last_call_is_not_brace_repaired(self):
        cut_off = SimpleNamespace(function=SimpleNamespace(
            name="list_dir", arguments='{"path": "/data", "recursive": true',
        ))

        valid, malformed = partition_tool_calls_by_json_validity(
            [cut_off], finish_reason="content_filter",
        )

        assert (valid, malformed) == ([], [cut_off])


@pytest.mark.asyncio
class TestNonStreamingAlsoWarns:
    """With streaming off, a dropped sibling call must not be silent."""

    async def test_agent_loop_warns_on_the_non_streaming_path(self):
        from atlas.application.chat.agent.agentic_loop import AgenticLoop

        class NonStreamingLLM:
            async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                      temperature=0.7, user_email=None):
                return LLMResponse(
                    content="", model_used="gemma",
                    tool_calls=[SimpleNamespace(
                        id="ok", type="function",
                        function=SimpleNamespace(name="search", arguments="{}"))],
                    dropped_tool_calls=["read_file"],
                    dropped_tool_calls_truncated=True,
                )

        loop = AgenticLoop(llm=NonStreamingLLM(), tool_manager=None, prompt_provider=None)
        publisher = AsyncMock()
        context = MagicMock()
        context.user_email = "u@example.com"

        await loop._call_llm(
            "gemma", [{"role": "user", "content": "find it"}], [{"type": "function"}],
            None, context, 0.7, False, publisher,
        )

        warned = [c.kwargs.get("message", "") for c in publisher.publish_warning.await_args_list]
        assert warned and "read_file" in warned[0]


class TestRepairKeysOnShapeNotJustFinishReason:
    """A missing *closing* brace is the cut-off signature; a missing opening one is not.

    Without this split, an absent `finish_reason` -- which providers routinely
    omit -- either loses the sloppy-envelope repair entirely or lets a truncated
    object be completed with keys the model never sent.
    """

    def test_an_unclosed_object_needs_positive_evidence_of_completeness(self):
        cut_shape = '{"path": "/data", "recursive": true'

        assert repair_structural_json(cut_shape, allow_closing_brace=False) is None
        assert repair_structural_json(cut_shape, allow_closing_brace=True) == {
            "path": "/data", "recursive": True,
        }

    def test_a_missing_opening_brace_is_repaired_either_way(self):
        """It cannot be a truncation: the cut would be at the end, not the start."""
        envelope = '"q": "hi"'

        assert repair_structural_json(envelope, allow_closing_brace=False) == {"q": "hi"}
        assert repair_structural_json(envelope, allow_closing_brace=True) == {"q": "hi"}

    def test_an_absent_finish_reason_keeps_the_envelope_repair(self):
        sloppy = SimpleNamespace(function=SimpleNamespace(name="search", arguments='"q": "hi"'))

        valid, malformed = partition_tool_calls_by_json_validity([sloppy], finish_reason=None)

        assert (valid, malformed) == ([sloppy], [])

    def test_an_absent_finish_reason_refuses_to_close_an_open_object(self):
        cut_off = SimpleNamespace(function=SimpleNamespace(
            name="list_dir", arguments='{"path": "/data", "recursive": true',
        ))

        valid, malformed = partition_tool_calls_by_json_validity([cut_off], finish_reason=None)

        assert (valid, malformed) == ([], [cut_off])

    def test_earlier_calls_are_repaired_even_on_a_truncated_response(self):
        """Only the last call can have been reached by the cut."""
        earlier = SimpleNamespace(function=SimpleNamespace(
            name="search", arguments='{"q": "hi"',
        ))
        last = SimpleNamespace(function=SimpleNamespace(name="now", arguments="{}"))

        valid, malformed = partition_tool_calls_by_json_validity(
            [earlier, last], finish_reason="length",
        )

        assert malformed == []
        assert json.loads(earlier.function.arguments) == {"q": "hi"}


class TestTruncationFlagSurvivesReclassification:
    """`truncated` decides text that is persisted into history."""

    def test_safe_call_llm_with_tools_forwards_it(self):
        import asyncio

        from atlas.application.chat.utilities.error_handler import safe_call_llm_with_tools

        class FailingLLM:
            async def call_with_tools(self, *a, **k):
                raise LLMMalformedToolCallError(
                    "The model ran out of room.", tool_names=["read_file"], truncated=True,
                )

        with pytest.raises(LLMMalformedToolCallError) as info:
            asyncio.run(safe_call_llm_with_tools(
                llm_caller=FailingLLM(), model="gemma",
                messages=[{"role": "user", "content": "hi"}],
                tools_schema=[{"type": "function"}],
            ))

        assert info.value.truncated is True
        assert info.value.tool_names == ["read_file"]
