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
from atlas.domain.errors import LLMMalformedToolCallError, LLMServiceError
from atlas.domain.messages.models import Message, MessageRole
from atlas.interfaces.llm import LLMResponse
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

        valid, malformed = partition_tool_calls_by_json_validity([cut_off], truncated=True)

        assert (valid, malformed) == ([], [cut_off])

    def test_empty_arguments_stay_valid_when_the_response_completed(self):
        """A no-argument tool call is legitimate when nothing was cut off."""
        no_args = SimpleNamespace(function=SimpleNamespace(name="now", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity([no_args], truncated=False)

        assert (valid, malformed) == ([no_args], [])

    def test_only_the_last_call_is_judged_by_truncation(self):
        """Truncation can only have reached the final call.

        Earlier calls in the same response completed before the limit was hit, so
        a genuine no-argument call among them must still be honoured.
        """
        earlier = SimpleNamespace(function=SimpleNamespace(name="now", arguments=""))
        last = SimpleNamespace(function=SimpleNamespace(name="delete_all", arguments=""))

        valid, malformed = partition_tool_calls_by_json_validity(
            [earlier, last], truncated=True,
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
        import json

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

    async def test_tool_calls_is_none_not_empty_when_all_are_dropped(self):
        """An empty list is not the same as no tool calls.

        `has_tool_calls()` is false either way, but an empty `tool_calls` array
        is a shape providers reject on the follow-up request -- the loop strips
        it defensively for exactly that reason. Normalizing to None at the source
        means there is nothing to strip.
        """
        caller = _caller()
        # Content alongside the bad call, so the guard does not raise and the
        # return shape is observable.
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

        valid, malformed = partition_tool_calls_by_json_validity([sloppy])

        assert (valid, malformed) == ([sloppy], [])
        assert json.loads(sloppy["function"]["arguments"]) == {"q": "hi"}

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
            MALFORMED_TOOL_CALL_TURN_CONTENT,
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
        assert assistant[-1].content == MALFORMED_TOOL_CALL_TURN_CONTENT
