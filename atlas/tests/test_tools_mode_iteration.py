"""Tests for bounded multi-round tool calling in standard (non-agent) tools mode.

Standard tools mode used to do exactly one tool round, then a no-tools synthesis
call. If the model tried to call another tool during synthesis, the provider
rejected the whole stream ("tool_choice is none, but model called a tool") and
the turn failed with a misleading error.

It now runs a bounded loop: after the first round it may take up to
``tools_mode_max_extra_rounds`` (default 3) further rounds to chain dependent
tool calls, guarded against repeating identical calls. When the budget is spent
(or the model keeps repeating), a hardened no-tools synthesis closes the turn,
and a stubborn tool-choice rejection becomes a clear, availability-aware message.
"""

from types import SimpleNamespace
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.modes.tools import ToolsModeRunner
from atlas.domain.messages.models import Message, MessageRole, ToolResult
from atlas.interfaces.llm import LLMResponse


def _tc(call_id: str, name: str, arguments: str = "{}"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class ScriptedToolsLLM:
    """stream_with_tools pops one scripted turn per call; stream_plain synthesizes."""

    def __init__(
        self,
        turns: List[Tuple[Optional[str], Optional[list]]],
        synthesis: str = "Final summary.",
        synthesis_error: Optional[Exception] = None,
    ):
        self._turns = list(turns)
        self.tool_stream_calls = 0
        self.seen_messages: List[List[dict]] = []
        self.synthesis = synthesis
        self.synthesis_error = synthesis_error

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        self.tool_stream_calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        text, tool_calls = self._turns.pop(0) if self._turns else (None, None)
        # A turn scripted with an exception raises it mid-stream, standing in for
        # a provider rejection of the continuation round.
        if isinstance(text, Exception):
            raise text
        if text:
            yield text
        yield LLMResponse(content=text or "", tool_calls=tool_calls)

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        if self.synthesis_error:
            raise self.synthesis_error
        yield self.synthesis

    async def call_plain(self, model, messages, temperature=0.7, user_email=None):
        if self.synthesis_error:
            raise self.synthesis_error
        return self.synthesis


def _config(max_extra_rounds=3, agent_available=False):
    return SimpleNamespace(
        app_settings=SimpleNamespace(
            tools_mode_max_extra_rounds=max_extra_rounds,
            feature_agent_mode_available=agent_available,
        )
    )


def _publisher():
    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()
    return pub


def _runner(llm, config_manager=None):
    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
    return ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=_publisher(),
        config_manager=config_manager,
    )


def _session():
    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()
    session.session_id = "s1"
    session.files = {}
    return session


async def _run(runner, messages, executed_names):
    """Run run_streaming with tool execution patched to record call order."""
    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        results = []
        for tc in tool_calls:
            executed_names.append(tc.function.name)
            results.append(ToolResult(tool_call_id=tc.id, content=f"{tc.function.name}=ok", success=True))
        return results

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        return await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=messages,
            selected_tools=["calc", "pptx"],
        )


@pytest.mark.asyncio
async def test_chains_dependent_tools_then_answers():
    """calc -> pptx -> text answer, all within the round budget, no synthesis error."""
    llm = ScriptedToolsLLM(turns=[
        ("computing", [_tc("c1", "calc", '{"e":"2+2"}')]),
        ("building deck", [_tc("c2", "pptx", '{"title":"X"}')]),
        ("Done! The deck is ready.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "calc then pptx"}], executed)

    assert executed == ["calc", "pptx"]
    assert "Done!" in resp["message"]


@pytest.mark.asyncio
async def test_selected_data_sources_propagated_to_execution_context():
    """Tools mode must carry the request's selected RAG sources on the execution
    context so the atlas_rag tools honor the UI selection -- matching agent mode
    -- instead of falling back to all authorized sources.
    """
    captured = {}
    llm = ScriptedToolsLLM(turns=[
        ("querying", [_tc("c1", "atlas_rag_query", '{"query":"x"}')]),
        ("Done.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))

    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        captured["session_context"] = session_context
        return [ToolResult(tool_call_id=tc.id, content="ok", success=True) for tc in tool_calls]

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=[{"role": "user", "content": "find the policy"}],
            selected_tools=["atlas_rag_query"],
            selected_data_sources=["atlas_rag:technical-docs"],
        )

    assert captured["session_context"]["selected_data_sources"] == ["atlas_rag:technical-docs"]


@pytest.mark.asyncio
async def test_anti_loop_stops_on_repeated_identical_call():
    """A model repeating the identical tool call is stopped and synthesized."""
    same = ("again", [_tc("c1", "calc", '{"e":"2+2"}')])
    llm = ScriptedToolsLLM(
        turns=[
            ("computing", [_tc("c1", "calc", '{"e":"2+2"}')]),
            same,  # identical signature -> anti-loop guard trips
        ],
        synthesis="Here is the result: 4.",
    )
    runner = _runner(llm, _config(max_extra_rounds=3))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "calc"}], executed)

    # calc executed once; the repeat was refused (not executed again).
    assert executed == ["calc"]
    assert resp["message"] == "Here is the result: 4."


async def _run_for_history(runner, session, messages):
    """Run run_streaming against a real-ish session and return history writes."""
    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        results = []
        for tc in tool_calls:
            # Emit the lifecycle events the recorder persists, the way the
            # real executor does through the turn's update callback.
            if update_callback is not None:
                await update_callback({
                    "type": "tool_start", "tool_call_id": tc.id,
                    "tool_name": tc.function.name, "server_name": "srv",
                    "arguments": {},
                })
                await update_callback({
                    "type": "tool_complete", "tool_call_id": tc.id,
                    "tool_name": tc.function.name, "success": True, "result": "ok",
                })
            results.append(ToolResult(tool_call_id=tc.id, content="ok", success=True))
        return results

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=session,
            model="test-model",
            messages=messages,
            selected_tools=["calc", "pptx"],
        )
    return [c.args[0] for c in session.history.add_message.call_args_list]


@pytest.mark.asyncio
async def test_narration_is_persisted_at_segment_close():
    """Pre-tool narration closes its stream segment long before the turn ends:
    the tools that follow can park on approval for minutes, and a reopen in
    that window reads the run's history -- where the narration otherwise does
    not exist until the turn closes, showing an empty assistant turn
    (issue #957). It is written at the close, as the same display-only
    ``agent_intermediate`` row the agentic loop uses."""
    llm = ScriptedToolsLLM(turns=[
        ("Let me check that.", [_tc("c1", "calc", '{"e":"2+2"}')]),
        ("Done! It is 4.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))

    added = await _run_for_history(runner, _session(), [{"role": "user", "content": "2+2?"}])

    narration = [m for m in added if m.metadata.get("message_type") == "agent_intermediate"]
    assert [m.content for m in narration] == ["Let me check that."]
    assert narration[0].metadata["agent_intermediate"] is True
    # The turn's closing answer is a separate, ordinary assistant row.
    closing = [m for m in added if m.metadata.get("message_type") != "agent_intermediate"
               and m.role.value == "assistant"]
    assert closing and closing[-1].content == "Done! It is 4."


@pytest.mark.asyncio
async def test_every_rounds_narration_is_persisted():
    """Each continuation round's narration closes its own segment; every one
    is persisted, in order, before the tools that follow it run. Tool rows
    flush per round too, so the reloaded transcript interleaves the way the
    live view did instead of bunching every narration ahead of every tool."""
    llm = ScriptedToolsLLM(turns=[
        ("first I compute", [_tc("c1", "calc", '{"e":"2+2"}')]),
        ("now I build", [_tc("c2", "pptx", '{"title":"X"}')]),
        ("All done.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))

    added = await _run_for_history(runner, _session(), [{"role": "user", "content": "go"}])

    kinds = [
        ("narration" if m.metadata.get("message_type") == "agent_intermediate"
         else "tool" if m.metadata.get("message_type") == "tool_call"
         else "answer")
        for m in added
    ]
    assert kinds == ["narration", "tool", "narration", "tool", "answer"]
    narration = [m.content for m in added if m.metadata.get("message_type") == "agent_intermediate"]
    assert narration == ["first I compute", "now I build"]


@pytest.mark.asyncio
async def test_extra_round_budget_is_respected():
    """With max_extra_rounds=1, only one continuation round runs before synthesis."""
    llm = ScriptedToolsLLM(
        turns=[
            ("r0", [_tc("c1", "a", "{}")]),
            ("r1", [_tc("c2", "b", "{}")]),
            ("r2", [_tc("c3", "c", "{}")]),  # would run, but budget is spent
        ],
        synthesis="Wrapped up.",
    )
    runner = _runner(llm, _config(max_extra_rounds=1))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    # Round 0 (a) + one extra round (b); c is never reached.
    assert executed == ["a", "b"]
    assert resp["message"] == "Wrapped up."


@pytest.mark.asyncio
async def test_zero_extra_rounds_is_classic_single_round():
    """max_extra_rounds=0 reproduces single-round behavior: execute then synthesize."""
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis="Single-round answer.",
    )
    runner = _runner(llm, _config(max_extra_rounds=0))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert executed == ["calc"]
    assert llm.tool_stream_calls == 1  # no continuation call
    assert resp["message"] == "Single-round answer."


@pytest.mark.asyncio
async def test_graceful_message_mentions_agent_mode_when_available():
    """If synthesis is rejected for a tool-call attempt, the message adapts to availability."""
    err = RuntimeError("litellm.APIError: Tool choice is none, but model called a tool")
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis_error=err,
    )
    runner = _runner(llm, _config(max_extra_rounds=0, agent_available=True))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert "Agent Mode" in resp["message"]
    assert "tried to call another tool" in resp["message"]


@pytest.mark.asyncio
async def test_graceful_message_neutral_when_agent_mode_disabled():
    """When agent mode is admin-disabled, the message must not suggest enabling it."""
    err = RuntimeError("litellm.APIError: Tool choice is none, but model called a tool")
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis_error=err,
    )
    runner = _runner(llm, _config(max_extra_rounds=0, agent_available=False))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert "Agent Mode" not in resp["message"]
    assert "follow-up" in resp["message"].lower()


@pytest.mark.asyncio
async def test_continuation_provider_error_falls_back_to_synthesis():
    """A provider rejection mid-continuation must degrade to synthesis, not crash.

    The failed round leaves no LLMResponse, so the loop substitutes a placeholder
    whose ``tool_calls`` is None. Iterating that unguarded raised
    ``TypeError: 'NoneType' object is not iterable`` in the canvas-only check and
    turned a recoverable provider error into a hard failure.
    """
    err = RuntimeError(
        "litellm.BadRequestError: An assistant message with 'tool_calls' must be "
        "followed by tool messages responding to each 'tool_call_id'. The following "
        "tool_call_ids did not have response messages: call_abc123"
    )
    llm = ScriptedToolsLLM(
        turns=[
            ("computing", [_tc("c1", "calc", '{"e":"2+2"}')]),
            (err, None),  # continuation round is rejected by the provider
        ],
        synthesis="The calculation returned 4.",
    )
    runner = _runner(llm, _config(max_extra_rounds=3))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "calc"}], executed)

    assert executed == ["calc"]
    assert resp["message"] == "The calculation returned 4."


def _real_session(prompt="draw"):
    """A Session with a real history, for assertions on get_messages_for_llm."""
    from atlas.domain.sessions.models import Session

    session = Session()
    session.history.add_message(Message(role=MessageRole.USER, content=prompt))
    return session


async def _run_on_real_session(runner, session, messages):
    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        return [ToolResult(tool_call_id=tc.id, content="ok", success=True) for tc in tool_calls]

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=session,
            model="test-model",
            messages=messages,
            selected_tools=["atlas_canvas"],
        )


@pytest.mark.asyncio
async def test_canvas_only_turn_keeps_its_prose_as_the_llm_visible_reply():
    """A canvas-only round's narration is NOT persisted as an intermediate
    row: the turn closes with that very text, and the closing message is the
    LLM-visible copy. Persisting both would show the next turn the literal
    placeholder "Content displayed in canvas." as the assistant's previous
    reply -- the prose filtered out with the display-only row."""
    llm = ScriptedToolsLLM(turns=[
        ("Here is the diagram.", [_tc("c1", "atlas_canvas", '{"content":"svg"}')]),
    ])
    runner = _runner(llm, _config(max_extra_rounds=0))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    assistant_rows = [m for m in session.history.messages if m.role == MessageRole.ASSISTANT]
    assert [m.content for m in assistant_rows] == ["Here is the diagram."]
    assert all(m.metadata.get("message_type") != "agent_intermediate" for m in assistant_rows)

    llm_visible = session.history.get_messages_for_llm()
    visible = [m["content"] for m in llm_visible if m["role"] == "assistant"]
    assert any("Here is the diagram." in content for content in visible)
    assert all("Content displayed in canvas" not in content for content in visible)


@pytest.mark.asyncio
async def test_canvas_round_narration_survives_a_continuation_round():
    """With rounds left in the budget, a canvas-only round is followed by a
    continuation that closes the turn -- so the shortcut never fires and the
    canvas round's narration must still exist after a reload. It is deferred
    at its segment close and flushed (display-only) when the turn ends
    another way."""
    llm = ScriptedToolsLLM(turns=[
        ("Let me draw that.", [_tc("c1", "atlas_canvas", '{"content":"svg"}')]),
        ("Done! The diagram is ready.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=1))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    # The full row sequence, in the live view's order: the canvas round's
    # narration lands where it streamed (before the continuation's answer),
    # not bunched at the turn's end.
    rows = [(m.role, m.metadata.get("message_type"), m.content) for m in session.history.messages]
    assert rows == [
        (MessageRole.USER, None, "draw"),
        (MessageRole.ASSISTANT, "agent_intermediate", "Let me draw that."),
        (MessageRole.ASSISTANT, None, "Done! The diagram is ready."),
    ]

    # The closing answer is the LLM-visible reply; the narration row is
    # display-only by design (matching the agentic loop's intermediate rows).
    llm_visible = session.history.get_messages_for_llm()
    visible = [m["content"] for m in llm_visible if m["role"] == "assistant"]
    assert any("Done! The diagram is ready." in content for content in visible)
    assert all("Content displayed in canvas" not in content for content in visible)


@pytest.mark.asyncio
async def test_canvas_response_then_stream_error_does_not_duplicate_the_narration():
    """A provider can yield a canvas-only response and then raise. The error
    branch defers the narration exactly like the clean path, so the close
    (which ends on that response) carries it once, not twice."""
    class CanvasThenRaiseLLM:
        async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                    temperature=0.7, user_email=None):
            yield "Here is the diagram."
            yield LLMResponse(
                content="Here is the diagram.",
                tool_calls=[_tc("c1", "atlas_canvas", '{"content":"svg"}')],
            )
            raise RuntimeError("stream died after the response")

        async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
            yield "unused"

        async def call_plain(self, model, messages, temperature=0.7, user_email=None):
            return "unused"

    runner = _runner(CanvasThenRaiseLLM(), _config(max_extra_rounds=0))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    contents = [m.content for m in session.history.messages if m.role == MessageRole.ASSISTANT]
    assert contents.count("Here is the diagram.") == 1


@pytest.mark.asyncio
async def test_a_cancel_during_a_canvas_round_keeps_the_deferred_narration():
    """A Stop mid-canvas-round unwinds through the BaseException handler: the
    deferred narration the user watched stream in is flushed, not dropped
    with the turn."""
    import asyncio as _asyncio

    llm = ScriptedToolsLLM(turns=[
        ("Let me draw that.", [_tc("c1", "atlas_canvas", '{"content":"svg"}')]),
    ])
    runner = _runner(llm, _config(max_extra_rounds=0))
    session = _real_session()

    async def _cancelled_execute(tool_calls, session_context, tool_manager,
                                 update_callback=None, config_manager=None, skip_approval=False):
        raise _asyncio.CancelledError()

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _cancelled_execute
        mock_te.build_files_manifest = MagicMock(return_value=None)
        with pytest.raises(_asyncio.CancelledError):
            await runner.run_streaming(
                session=session,
                model="test-model",
                messages=[{"role": "user", "content": "draw"}],
                selected_tools=["atlas_canvas"],
            )

    rows = [(m.role, m.metadata.get("message_type"), m.content) for m in session.history.messages]
    assert (MessageRole.ASSISTANT, "agent_intermediate", "Let me draw that.") in rows


@pytest.mark.asyncio
async def test_two_identical_canvas_narrations_do_not_collapse():
    """Two canvas rounds that narrate identically are two bubbles live, so
    they are two rows after a reload: the close takes back at most the one
    row whose text the turn closes with."""
    llm = ScriptedToolsLLM(turns=[
        ("Same words.", [_tc("c1", "atlas_canvas", '{"content":"a"}')]),
        ("Same words.", [_tc("c2", "atlas_canvas", '{"content":"b"}')]),
    ])
    runner = _runner(llm, _config(max_extra_rounds=1))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    contents = [m.content for m in session.history.messages if m.role == MessageRole.ASSISTANT]
    assert contents.count("Same words.") == 2


@pytest.mark.asyncio
async def test_an_empty_canvas_continuation_does_not_shadow_earlier_prose():
    """A canvas-only continuation that streams no text must not shadow the
    prose an earlier canvas round deferred: the close picks the last
    non-empty narration, not the last slot."""
    llm = ScriptedToolsLLM(turns=[
        ("Here is the diagram.", [_tc("c1", "atlas_canvas", '{"content":"svg"}')]),
        (None, [_tc("c2", "atlas_canvas", '{"content":"svg2"}')]),
    ])
    runner = _runner(llm, _config(max_extra_rounds=1))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    contents = [m.content for m in session.history.messages if m.role == MessageRole.ASSISTANT]
    assert contents.count("Here is the diagram.") == 1
    assert "Content displayed in canvas." not in contents


@pytest.mark.asyncio
async def test_mixed_canvas_and_tool_round_persists_narration_immediately():
    """A round calling canvas alongside a real tool is not canvas-only: the
    shortcut cannot fire, so its narration is persisted at the segment close
    like any other round's."""
    llm = ScriptedToolsLLM(turns=[
        ("Computing, then drawing.", [_tc("c1", "atlas_canvas", '{"content":"svg"}'), _tc("c2", "calc", '{"e":"2+2"}')]),
        ("All done.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=1))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "go"}])

    rows = [(m.role, m.metadata.get("message_type"), m.content) for m in session.history.messages]
    assert (MessageRole.ASSISTANT, "agent_intermediate", "Computing, then drawing.") in rows
    assert rows[-1] == (MessageRole.ASSISTANT, None, "All done.")


@pytest.mark.asyncio
async def test_canvas_only_turn_keeps_a_narration_that_never_streamed():
    """A response whose text never streamed (no deltas, content only on the
    final item) exists nowhere else -- it remains the turn's answer, as the
    closing message, LLM-visible."""
    class ContentOnlyCanvasLLM:
        async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                    temperature=0.7, user_email=None):
            yield LLMResponse(
                content="Here is the diagram.",
                tool_calls=[_tc("c1", "atlas_canvas", '{"content":"svg"}')],
            )

        async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
            yield "unused"

        async def call_plain(self, model, messages, temperature=0.7, user_email=None):
            return "unused"

    runner = _runner(ContentOnlyCanvasLLM(), _config(max_extra_rounds=0))
    session = _real_session()

    await _run_on_real_session(runner, session, [{"role": "user", "content": "draw"}])

    assistant_rows = [m for m in session.history.messages if m.role == MessageRole.ASSISTANT]
    assert [m.content for m in assistant_rows] == ["Here is the diagram."]

    llm_visible = session.history.get_messages_for_llm()
    visible = [m["content"] for m in llm_visible if m["role"] == "assistant"]
    assert any("Here is the diagram." in content for content in visible)


@pytest.mark.asyncio
async def test_narration_streamed_before_a_round_error_is_still_persisted():
    """A continuation round can stream narration and then fail (a provider
    rejection mid-stream). The user watched that text stream in; dropping it
    would leave a reload showing a turn that said nothing before its tools --
    and its segment already closed, so the replay buffer no longer holds it
    either (issue #957)."""
    class ErrAfterTextLLM:
        def __init__(self):
            self._calls = 0

        async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                    temperature=0.7, user_email=None):
            self._calls += 1
            if self._calls == 1:
                yield "computing"
                yield LLMResponse(content="computing", tool_calls=[_tc("c1", "calc", '{"e":"2+2"}')])
            else:
                yield "almost there"
                raise RuntimeError("provider rejected the continuation")

        async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
            yield "The calculation returned 4."

        async def call_plain(self, model, messages, temperature=0.7, user_email=None):
            return "The calculation returned 4."

    runner = _runner(ErrAfterTextLLM(), _config(max_extra_rounds=3))

    added = await _run_for_history(runner, _session(), [{"role": "user", "content": "calc"}])

    narration = [m.content for m in added if m.metadata.get("message_type") == "agent_intermediate"]
    assert narration == ["computing", "almost there"]


@pytest.mark.asyncio
async def test_data_sources_neither_inject_context_nor_add_the_search_tool():
    """Selected sources scope ``atlas_search``; they retrieve nothing themselves.

    Tools mode used to route every turn with data sources through
    ``stream_with_rag_and_tools``, which queried the sources and prepended the
    passages as a system message before the model spoke. Then the sources also
    added ``atlas_search`` to the schema (#862), so a "use search" prompt could
    invoke it without the user ever turning it on (#921). Now the ordinary
    streaming call is made with exactly the tools the user selected.
    """
    llm = ScriptedToolsLLM(turns=[("No search needed.", None)])
    runner = _runner(llm, _config())
    runner.config_manager = SimpleNamespace(app_settings=SimpleNamespace(
        tools_mode_max_extra_rounds=3,
        feature_agent_mode_available=False,
        feature_rag_enabled=True,
        feature_atlas_rag_tools_enabled=True,
    ))
    assert not hasattr(llm, "stream_with_rag_and_tools")

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=[{"role": "user", "content": "what is in the docs?"}],
            selected_tools=["calc"],
            selected_data_sources=["srv:docs"],
            user_email="u@example.com",
        )

    # The model saw the conversation as-is -- no retrieved-context system turn.
    assert llm.seen_messages[0] == [{"role": "user", "content": "what is in the docs?"}]
    requested = runner.tool_manager.get_tools_schema.call_args[0][0]
    assert requested == ["calc"]


@pytest.mark.asyncio
async def test_the_requesting_user_reaches_the_schema_scoping_api():
    """Tool schemas are scoped per user; the mode must actually pass the user.

    A server that gates ``tools/list`` publishes its catalogue marked
    ``user_scoped``, and ``get_tools_schema`` withholds one that is not the
    requester's. That protection is only worth anything if the user reaches
    it, so this pins the argument rather than the scoping logic.
    """
    llm = ScriptedToolsLLM(turns=[("Done.", None)])
    runner = _runner(llm, _config(max_extra_rounds=3))

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = AsyncMock(return_value=[])
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            selected_tools=["calc"],
            user_email="owner@example.gov",
        )

    args, kwargs = runner.tool_manager.get_tools_schema.call_args
    assert (kwargs.get("user_email") or (args[1] if len(args) > 1 else None)) == "owner@example.gov"


@pytest.mark.asyncio
async def test_the_requesting_user_reaches_the_schema_scoping_api_non_streaming():
    llm = ScriptedToolsLLM(turns=[("Done.", None)])
    runner = _runner(llm, _config(max_extra_rounds=3))

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = AsyncMock(return_value=[])
        mock_te.build_files_manifest = MagicMock(return_value=None)
        try:
            await runner.run(
                session=_session(),
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
                selected_tools=["calc"],
                user_email="owner@example.gov",
            )
        except Exception:
            # Expected: schemas are resolved before the LLM call, and this
            # scripted double does not implement the non-streaming call path.
            # What is under test is the argument that already went out.
            pass

    args, kwargs = runner.tool_manager.get_tools_schema.call_args
    assert (kwargs.get("user_email") or (args[1] if len(args) > 1 else None)) == "owner@example.gov"


@pytest.mark.asyncio
async def test_a_retried_discovery_reports_its_real_options_not_the_cached_note():
    """A failed ``atlas_discover_launch_options`` is exempt from the anti-loop
    guard so the model can retry it. The exemption is only worth having if the
    retry's result actually reaches the model: the launch that follows needs the
    discovered workspaces and models. Before #949's review fix the retry was
    re-executed but its tool message was overwritten with the
    "identical tool call already executed" note, so the model never saw the
    options and every subsequent launch kept being refused.
    """
    from atlas.modules.mcp_tools.atlas_server import DISCOVER_LAUNCH_OPTIONS_TOOL_NAME

    def discovery_call(cid):
        return _tc(cid, DISCOVER_LAUNCH_OPTIONS_TOOL_NAME, "{}")
    llm = ScriptedToolsLLM(turns=[
        ("discovering", [discovery_call("d1")]),
        # The identical call again -- same name, same arguments.
        ("retrying discovery", [discovery_call("d2")]),
        # A third identical call in the same turn should be blocked by the cap.
        ("retrying discovery again", [discovery_call("d3")]),
        ("Here are your options.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))

    attempts = {"n": 0}
    options_payload = '{"workspaces": [{"name": "Research"}], "models": [{"name": "gpt-4o"}]}'

    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        results = []
        for tc in tool_calls:
            attempts["n"] += 1
            if attempts["n"] == 1:
                # First discovery fails and publishes nothing.
                results.append(ToolResult(
                    tool_call_id=tc.id, content="discovery failed", success=False))
            else:
                session_context["launch_discovery"] = {
                    "workspaces": [{"name": "Research"}],
                    "models": [{"name": "gpt-4o"}],
                }
                results.append(ToolResult(
                    tool_call_id=tc.id, content=options_payload, success=True))
        return results

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=[{"role": "user", "content": "launch a sub-conversation"}],
            selected_tools=[DISCOVER_LAUNCH_OPTIONS_TOOL_NAME],
        )

    # The retry was executed exactly once; the third identical call was capped.
    assert attempts["n"] == 2, "the exempted discovery retry was not re-executed"

    # The messages the model saw on its third turn must carry the options.
    final_messages = llm.seen_messages[-1]
    retry_message = next(
        m for m in final_messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "d2"
    )
    assert retry_message["content"] == options_payload
    assert "already executed" not in retry_message["content"]
    third_retry_message = next(
        m for m in final_messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "d3"
    )
    assert "already executed" in third_retry_message["content"]
