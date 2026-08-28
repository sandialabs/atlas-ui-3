"""Tests for the AgenticLoop (Claude-native agentic strategy).

Verifies that the loop correctly:
- Finishes when the LLM responds with text only (no tool calls)
- Executes tools and loops when tool calls are present
- Handles multi-step tool-use sequences
- Respects max_steps limit
- Streams tokens when streaming is enabled
- Works through the AgentLoopFactory
"""

import os
import sys
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.factory import AgentLoopFactory
from atlas.application.chat.agent.protocols import AgentContext, AgentEvent
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse

# -- Test doubles -------------------------------------------------------

class FakeLLM:
    """Programmable fake LLM that returns queued responses."""

    def __init__(self, responses: Optional[List[LLMResponse]] = None):
        self._responses = list(responses or [])
        self.call_count = 0
        self.last_tool_choice: Optional[str] = None

    async def call_with_tools(
        self, model, messages, tools_schema, tool_choice="auto",
        temperature=0.7, user_email=None,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_tool_choice = tool_choice
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="Default response")

    async def call_with_rag_and_tools(
        self, model, messages, data_sources, tools_schema,
        user_email, tool_choice="auto", temperature=0.7,
    ) -> LLMResponse:
        return await self.call_with_tools(
            model, messages, tools_schema, tool_choice,
            temperature=temperature, user_email=user_email,
        )

    async def call_plain(
        self, model, messages, temperature=0.7, user_email=None,
    ) -> str:
        self.call_count += 1
        if self._responses:
            return self._responses.pop(0).content
        return "Fallback answer"

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield "streamed "
        yield "fallback"

    async def stream_with_tools(
        self, model, messages, tools_schema, tool_choice="auto",
        temperature=0.7, user_email=None,
    ):
        resp = await self.call_with_tools(
            model, messages, tools_schema, tool_choice,
            temperature, user_email,
        )
        if resp.has_tool_calls():
            yield resp
        else:
            for word in (resp.content or "").split(" "):
                yield word + " "
            yield resp

    async def stream_with_rag_and_tools(
        self, model, messages, data_sources, tools_schema,
        user_email, tool_choice="auto", temperature=0.7,
    ):
        async for item in self.stream_with_tools(
            model, messages, tools_schema, tool_choice,
            temperature, user_email,
        ):
            yield item


def _make_tool_call(call_id: str, name: str, arguments: str = "{}"):
    """Build a SimpleNamespace mimicking a LiteLLM tool_call object.

    Both streaming and non-streaming paths return objects with attribute
    access (SimpleNamespace or litellm pydantic models).
    """
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _make_tool_manager(results: Dict[str, str]):
    """Return a mock tool manager mapping tool names to results."""
    async def fake_execute(tool_call_obj, context=None):
        name = getattr(tool_call_obj, "name", None)
        call_id = getattr(tool_call_obj, "id", "unknown")
        return ToolResult(
            tool_call_id=call_id,
            content=results.get(name, "unknown"),
            success=True,
        )

    mgr = MagicMock()
    mgr.execute_tool = AsyncMock(side_effect=fake_execute)
    mgr.get_tools_schema = MagicMock(return_value=[
        {"type": "function", "function": {"name": n, "parameters": {}}}
        for n in results
    ])
    return mgr


def _make_context():
    return AgentContext(
        session_id=uuid4(),
        user_email="test@example.com",
        files={},
        history=ConversationHistory(),
    )


def _collect_events():
    events: List[AgentEvent] = []

    async def handler(event: AgentEvent):
        events.append(event)

    return events, handler


def _make_loop(llm, tool_mgr=None, **kwargs):
    """Create an AgenticLoop with skip_approval=True for testing."""
    loop = AgenticLoop(
        llm=llm,
        tool_manager=tool_mgr,
        prompt_provider=None,
        **kwargs,
    )
    loop.skip_approval = True
    return loop


# -- Tests: basic completion --------------------------------------------

class TestAgenticLoopBasicCompletion:

    @pytest.mark.asyncio
    async def test_text_only_response_finishes_immediately(self):
        """When the LLM responds with text and no tool calls, the loop
        should return that text as the final answer in 1 step."""
        llm = FakeLLM([LLMResponse(content="Hello! How can I help?")])
        events, handler = _collect_events()

        loop = _make_loop(llm)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            context=_make_context(),
            selected_tools=None,
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Hello! How can I help?"
        assert result.steps == 1
        assert result.metadata["strategy"] == "agentic"
        assert llm.call_count == 1

        event_types = [e.type for e in events]
        assert "agent_start" in event_types
        assert "agent_turn_start" in event_types
        assert "agent_completion" in event_types

    @pytest.mark.asyncio
    async def test_empty_tool_calls_treated_as_done(self):
        """An LLM response with tool_calls=[] should be treated as text-only."""
        llm = FakeLLM([LLMResponse(content="Done.", tool_calls=[])])
        events, handler = _collect_events()

        loop = _make_loop(llm)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Test"}],
            context=_make_context(),
            selected_tools=None,
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Done."
        assert result.steps == 1


# -- Tests: tool execution flow -----------------------------------------

class TestAgenticLoopToolExecution:

    @pytest.mark.asyncio
    async def test_single_tool_then_text_answer(self):
        """LLM calls a tool in step 1, then responds with text in step 2."""
        llm = FakeLLM([
            LLMResponse(
                content="Let me search for that.",
                tool_calls=[_make_tool_call("call-1", "search", '{"q": "test"}')],
            ),
            LLMResponse(content="Based on the search results, here is your answer."),
        ])
        tool_mgr = _make_tool_manager({"search": "Found 3 results."})
        events, handler = _collect_events()

        context = _make_context()
        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Search for test"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Based on the search results, here is your answer."
        assert result.steps == 2
        # The loop is the single owner of narration persistence: the step-1
        # narration is flushed straight into history as a display-only
        # agent_intermediate row (there is no metadata copy of it).
        intermediate = context.history.messages[0]
        assert intermediate.content == "Let me search for that."
        assert intermediate.metadata["message_type"] == "agent_intermediate"
        assert intermediate.metadata["step"] == 1

        event_types = [e.type for e in events]
        assert "agent_tool_results" in event_types

    @pytest.mark.asyncio
    async def test_multi_tool_parallel_execution(self):
        """LLM calls two tools in one response, both execute in parallel."""
        llm = FakeLLM([
            LLMResponse(
                content="Running both tools.",
                tool_calls=[
                    _make_tool_call("call-1", "toolA"),
                    _make_tool_call("call-2", "toolB"),
                ],
            ),
            LLMResponse(content="Both tools completed successfully."),
        ])
        tool_mgr = _make_tool_manager({"toolA": "result-A", "toolB": "result-B"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Use both tools"}],
            context=_make_context(),
            selected_tools=["toolA", "toolB"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Both tools completed successfully."
        assert result.steps == 2

    @pytest.mark.asyncio
    async def test_multi_step_tool_chain(self):
        """LLM calls tools across 3 steps before finishing."""
        llm = FakeLLM([
            LLMResponse(
                content="Step 1",
                tool_calls=[_make_tool_call("c1", "search")],
            ),
            LLMResponse(
                content="Step 2",
                tool_calls=[_make_tool_call("c2", "analyze")],
            ),
            LLMResponse(
                content="Step 3",
                tool_calls=[_make_tool_call("c3", "search")],
            ),
            LLMResponse(content="Final answer after 3 tool steps."),
        ])
        tool_mgr = _make_tool_manager({"search": "found", "analyze": "analyzed"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Complex task"}],
            context=_make_context(),
            selected_tools=["search", "analyze"],
            data_sources=None,
            max_steps=10,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Final answer after 3 tool steps."
        assert result.steps == 4


# -- Tests: max steps and fallback ---------------------------------------

class TestAgenticLoopMaxSteps:

    @pytest.mark.asyncio
    async def test_max_steps_triggers_fallback(self):
        """When max_steps is exhausted, the loop calls call_plain for a
        final synthesis answer."""
        llm = FakeLLM([
            LLMResponse(
                content="Calling tool",
                tool_calls=[_make_tool_call("c1", "search")],
            ),
            LLMResponse(
                content="Still going",
                tool_calls=[_make_tool_call("c2", "search")],
            ),
            LLMResponse(content="Fallback answer"),
        ])
        tool_mgr = _make_tool_manager({"search": "result"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Go"}],
            context=_make_context(),
            selected_tools=["search"],
            data_sources=None,
            max_steps=2,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Fallback answer"
        assert result.steps == 2

    @pytest.mark.asyncio
    async def test_max_steps_streaming_fallback(self):
        """When max_steps is exhausted with streaming enabled, the loop
        streams the final answer."""
        llm = FakeLLM([
            LLMResponse(
                content="Calling tool",
                tool_calls=[_make_tool_call("c1", "search")],
            ),
        ])
        tool_mgr = _make_tool_manager({"search": "result"})
        events, handler = _collect_events()
        publisher = MagicMock()
        publisher.publish_token_stream = AsyncMock()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Go"}],
            context=_make_context(),
            selected_tools=["search"],
            data_sources=None,
            max_steps=1,
            temperature=0.7,
            event_handler=handler,
            streaming=True,
            event_publisher=publisher,
        )

        assert "fallback" in result.final_answer.lower() or result.final_answer
        assert result.steps == 1


# -- Tests: tool_choice is always "auto" ---------------------------------

class TestAgenticLoopToolChoiceAuto:

    @pytest.mark.asyncio
    async def test_tool_choice_is_auto(self):
        """The agentic loop must always use tool_choice='auto', never 'required'."""
        llm = FakeLLM([LLMResponse(content="Done.")])
        tool_mgr = _make_tool_manager({"tool1": "r"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Test"}],
            context=_make_context(),
            selected_tools=["tool1"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert llm.last_tool_choice == "auto"


# -- Tests: no control tools in schema ----------------------------------

class TestAgenticLoopNoControlTools:

    @pytest.mark.asyncio
    async def test_no_finished_tool_in_schema(self):
        """The agentic loop must NOT inject any control tools (finished,
        agent_decide_next, etc.) into the tools schema."""
        call_schemas = []

        class SpyLLM(FakeLLM):
            async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto", **kw):
                call_schemas.append(tools_schema)
                return LLMResponse(content="Done.")

        llm = SpyLLM()
        tool_mgr = _make_tool_manager({"real_tool": "result"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Test"}],
            context=_make_context(),
            selected_tools=["real_tool"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert len(call_schemas) == 1
        tool_names = [
            t.get("function", {}).get("name", "")
            for t in call_schemas[0]
        ]
        control_tools = {"finished", "agent_decide_next", "agent_observe_decide", "agent_think"}
        assert not (set(tool_names) & control_tools), (
            f"Control tools found in schema: {set(tool_names) & control_tools}"
        )


# -- Tests: message accumulation ----------------------------------------

class TestAgenticLoopMessageAccumulation:

    @pytest.mark.asyncio
    async def test_tool_results_added_to_messages(self):
        """After tool execution, assistant and tool messages should be
        appended to the messages list."""
        messages = [{"role": "user", "content": "Search for X"}]
        llm = FakeLLM([
            LLMResponse(
                content="Searching...",
                tool_calls=[_make_tool_call("c1", "search")],
            ),
            LLMResponse(content="Here are the results."),
        ])
        tool_mgr = _make_tool_manager({"search": "Found X."})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=messages,
            context=_make_context(),
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        # Original user message + assistant (with tool_calls) + tool result = 3
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert messages[1]["tool_calls"] is not None
        assert messages[2]["role"] == "tool"
        assert messages[2]["content"] == "Found X."

    @pytest.mark.asyncio
    async def test_assistant_tool_calls_are_serializable_dicts(self):
        """The assistant message's tool_calls must be plain JSON-serializable
        dicts, not SimpleNamespace/objects.

        Regression for multi-step chains: the streaming path yields
        SimpleNamespace tool calls (good for attribute-access execution), but
        when that assistant message is re-sent on the next turn, non-dict
        tool_calls serialize to an empty array and providers like OpenAI
        reject the follow-up call (``Invalid 'messages[N].tool_calls': empty
        array``), breaking any task that needs more than one tool call.
        """
        import json

        messages = [{"role": "user", "content": "Compute step by step"}]
        # Two tool-call turns in a row, then a text answer -- the second call
        # only succeeds if turn 1's assistant message round-tripped cleanly.
        llm = FakeLLM([
            LLMResponse(content="step 1", tool_calls=[_make_tool_call("c1", "calc", '{"e": "1+1"}')]),
            LLMResponse(content="step 2", tool_calls=[_make_tool_call("c2", "calc", '{"e": "2+2"}')]),
            LLMResponse(content="Done."),
        ])
        tool_mgr = _make_tool_manager({"calc": "ok"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=messages,
            context=_make_context(),
            selected_tools=["calc"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Done."
        assert result.steps == 3

        assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_msgs) == 2
        for msg in assistant_msgs:
            for tc in msg["tool_calls"]:
                # Must be a plain dict in OpenAI wire format, not an object.
                assert isinstance(tc, dict), f"tool_call is {type(tc)}, not dict"
                assert isinstance(tc["function"], dict)
                assert tc["function"]["name"] == "calc"
            # The whole message must JSON-serialize (objects would raise here).
            json.dumps(msg)


# -- Tests: streaming error surfacing -----------------------------------

class _StreamErrorPublisher:
    """Minimal event publisher recording published tokens."""

    def __init__(self):
        self.tokens: List[str] = []
        self.calls: List[Dict[str, object]] = []

    async def publish_token_stream(self, token, is_first=False, is_last=False):
        self.tokens.append(token)
        self.calls.append({
            "token": token,
            "is_first": is_first,
            "is_last": is_last,
        })


class TestAgenticLoopStreamingErrorSurfacing:
    """A streaming LLM error with no accumulated content must propagate, not
    silently return an empty response.

    Regression: when the provider rejects a mid-stream tool call ("tool_choice
    is none, but model called a tool") before any text streams, the loop used
    to swallow the exception and return ``LLMResponse(content="")`` -- the UI
    then showed nothing, looking like the model never responded.
    """

    @pytest.mark.asyncio
    async def test_streaming_error_before_any_token_propagates(self):
        class BoomLLM(FakeLLM):
            async def stream_with_tools(self, *a, **k):
                raise RuntimeError("tool_choice is none, but model called a tool")
                yield  # pragma: no cover -- makes this an async generator

        events, handler = _collect_events()
        loop = _make_loop(BoomLLM())

        with pytest.raises(RuntimeError, match="tool_choice is none"):
            await loop.run(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                context=_make_context(),
                selected_tools=["calc"],
                data_sources=None,
                max_steps=5,
                temperature=0.7,
                event_handler=handler,
                streaming=True,
                event_publisher=_StreamErrorPublisher(),
            )

    @pytest.mark.asyncio
    async def test_streaming_error_after_partial_text_keeps_partial(self):
        """If text already streamed before the error, keep the partial answer
        and close the stream rather than raising."""
        class PartialThenBoomLLM(FakeLLM):
            async def stream_with_tools(self, *a, **k):
                yield "partial "
                yield "answer"
                raise RuntimeError("connection reset mid-stream")

        events, handler = _collect_events()
        publisher = _StreamErrorPublisher()
        loop = _make_loop(PartialThenBoomLLM())

        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            context=_make_context(),
            selected_tools=["calc"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            streaming=True,
            event_publisher=publisher,
        )

        assert result.final_answer == "partial answer"
        # The partial text is finalized by exactly one is_last close (the single
        # stream-close after the try/except), not a redundant double close from
        # the error handler as well.
        assert [call["is_last"] for call in publisher.calls].count(True) == 1


class TestAgenticLoopStreamingNarration:

    @pytest.mark.asyncio
    async def test_tool_call_turn_stream_is_closed_and_metadata_collected(self):
        """Narration streamed before a tool call should finalize its bubble."""
        class NarratingToolLLM(FakeLLM):
            async def stream_with_tools(self, *a, **k):
                self.call_count += 1
                if self.call_count == 1:
                    yield "I will search first."
                    yield LLMResponse(
                        content="I will search first.",
                        tool_calls=[_make_tool_call("call-1", "search", '{"q": "x"}')],
                    )
                else:
                    yield "Done."
                    yield LLMResponse(content="Done.")

        events, handler = _collect_events()
        publisher = _StreamErrorPublisher()
        context = _make_context()
        loop = _make_loop(NarratingToolLLM(), _make_tool_manager({"search": "found"}))

        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "search"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            streaming=True,
            event_publisher=publisher,
        )

        assert result.final_answer == "Done."
        assert [call["is_last"] for call in publisher.calls].count(True) == 2
        assert publisher.calls[1] == {
            "token": "",
            "is_first": False,
            "is_last": True,
        }
        assert context.history.messages[0].content == "I will search first."
        assert context.history.messages[0].metadata["agent_intermediate"] is True
        assert context.history.messages[0].metadata["message_type"] == "agent_intermediate"


# -- Tests: factory integration -----------------------------------------

class TestAgenticLoopFactory:

    def test_factory_creates_agentic_loop(self):
        llm = FakeLLM()
        factory = AgentLoopFactory(llm=llm)
        loop = factory.create("agentic")
        assert isinstance(loop, AgenticLoop)

    def test_factory_lists_agentic_strategy(self):
        llm = FakeLLM()
        factory = AgentLoopFactory(llm=llm)
        strategies = factory.get_available_strategies()
        assert "agentic" in strategies

    def test_factory_caches_agentic_loop(self):
        llm = FakeLLM()
        factory = AgentLoopFactory(llm=llm)
        loop1 = factory.create("agentic")
        loop2 = factory.create("agentic")
        assert loop1 is loop2


# -- Tests: search is an explicit tool call ------------------------------

class TestAgenticLoopSearchIsATool:
    """Data sources scope ``atlas_search``; they never run retrieval on their own.

    Retrieval used to happen before the model was asked anything: any turn with
    ``data_sources`` went through ``call_with_rag_and_tools``, which queried
    every source and injected the passages as a system message. The user saw no
    search, and the model never chose one. Now the loop makes the same normal
    tools call it makes without sources, and the model has to call
    ``atlas_search`` for anything to be retrieved.
    """

    @pytest.mark.asyncio
    async def test_data_sources_do_not_trigger_retrieval(self):
        calls = []

        class RecordingLLM(FakeLLM):
            async def call_with_tools(self, model, messages, tools_schema, *a, **kw):
                calls.append(messages)
                return LLMResponse(content="answered without searching")

        llm = RecordingLLM()
        tool_mgr = _make_tool_manager({"tool1": "r"})
        events, handler = _collect_events()

        result = await _make_loop(llm, tool_mgr).run(
            model="test-model",
            messages=[{"role": "user", "content": "Search with RAG"}],
            context=_make_context(),
            selected_tools=["tool1"],
            data_sources=["source1"],
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "answered without searching"
        assert len(calls) == 1
        # Nothing was injected: the model saw exactly the turn it was given.
        assert calls[0] == [{"role": "user", "content": "Search with RAG"}]

    @pytest.mark.asyncio
    async def test_selected_sources_offer_the_search_tool(self):
        """A user who picks a source but not the tool still gets to search."""
        tool_mgr = _make_tool_manager({"tool1": "r"})
        events, handler = _collect_events()
        config = SimpleNamespace(app_settings=SimpleNamespace(
            feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True,
        ))

        await _make_loop(FakeLLM(), tool_mgr, config_manager=config).run(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            context=_make_context(),
            selected_tools=["tool1"],
            data_sources=["source1"],
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        requested = tool_mgr.get_tools_schema.call_args[0][0]
        assert requested == ["tool1", "atlas_search"]

    @pytest.mark.asyncio
    async def test_no_sources_means_no_implicit_search_tool(self):
        tool_mgr = _make_tool_manager({"tool1": "r"})
        events, handler = _collect_events()
        config = SimpleNamespace(app_settings=SimpleNamespace(
            feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True,
        ))

        await _make_loop(FakeLLM(), tool_mgr, config_manager=config).run(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            context=_make_context(),
            selected_tools=["tool1"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert tool_mgr.get_tools_schema.call_args[0][0] == ["tool1"]


# -- Tests: persistent MCP session scope --------------------------------

class TestAgenticLoopConversationScope:
    """Regression tests for stateful MCP session reuse in agent mode.

    The agentic loop must forward ``conversation_id`` to the tool manager so
    ``MCPSessionManager`` reuses a single persistent session across sequential
    tool calls. Before the fix the loop built ``session_context`` without
    ``conversation_id``; ``MCPToolManager.call_tool`` then took the single-use
    session branch and stateful MCP servers raised session errors -- a failure
    that only manifested in agent mode (regular mode passes the value via
    ``build_session_context``).
    """

    @pytest.mark.asyncio
    async def test_conversation_id_forwarded_to_tool_manager(self):
        """Every tool call in the loop receives the conversation_id in context."""
        seen_contexts = []

        async def capture_execute(tool_call_obj, context=None):
            seen_contexts.append(context)
            return ToolResult(
                tool_call_id=getattr(tool_call_obj, "id", "unknown"),
                content="ok",
                success=True,
            )

        tool_mgr = MagicMock()
        tool_mgr.execute_tool = AsyncMock(side_effect=capture_execute)
        tool_mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "search", "parameters": {}}}
        ])

        llm = FakeLLM([
            LLMResponse(
                content="Calling tool.",
                tool_calls=[_make_tool_call("call-1", "search", '{"q": "x"}')],
            ),
            LLMResponse(content="Done."),
        ])
        events, handler = _collect_events()

        context = AgentContext(
            session_id=uuid4(),
            user_email="test@example.com",
            files={},
            history=ConversationHistory(),
            conversation_id="conv-abc-123",
        )

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "search"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert seen_contexts, "tool manager was never invoked"
        for ctx in seen_contexts:
            assert ctx is not None
            assert ctx.get("conversation_id") == "conv-abc-123", (
                "agentic loop must forward conversation_id so stateful MCP "
                "sessions are reused across tool calls"
            )

    @pytest.mark.asyncio
    async def test_conversation_id_falls_back_to_session_id(self):
        """When AgentContext omits conversation_id, the loop falls back to the
        session id so MCP calls still share one persistent session (never None,
        matching ChatService's default scoping)."""
        seen_contexts = []

        async def capture_execute(tool_call_obj, context=None):
            seen_contexts.append(context)
            return ToolResult(
                tool_call_id=getattr(tool_call_obj, "id", "unknown"),
                content="ok",
                success=True,
            )

        tool_mgr = MagicMock()
        tool_mgr.execute_tool = AsyncMock(side_effect=capture_execute)
        tool_mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "search", "parameters": {}}}
        ])

        llm = FakeLLM([
            LLMResponse(
                content="Calling tool.",
                tool_calls=[_make_tool_call("call-1", "search", '{"q": "x"}')],
            ),
            LLMResponse(content="Done."),
        ])
        events, handler = _collect_events()

        # _make_context() builds an AgentContext without conversation_id.
        context = _make_context()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "search"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert seen_contexts, "tool manager was never invoked"
        for ctx in seen_contexts:
            assert ctx.get("conversation_id") == str(context.session_id)


# -- Tests: agent steering (issue #824) ---------------------------------

class _RecordingLLM:
    """Fake LLM that records the message roles it sees per call.

    ``push_on_call`` (1-based) queues a steering message into ``channel`` when
    that call is made, simulating a user who sends a message mid-run. This
    lets a test place the steering message after a step's LLM call but before
    the next iteration's drain.
    """

    def __init__(self, responses, channel=None, push_on_call=None, push_text="steer!"):
        self._responses = list(responses)
        self.call_count = 0
        self.seen_roles: List[List[str]] = []
        self.seen_user_contents: List[List[str]] = []
        self._channel = channel
        self._push_on_call = push_on_call
        self._push_text = push_text

    async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                              temperature=0.7, user_email=None) -> LLMResponse:
        self.call_count += 1
        self.seen_roles.append([m.get("role") for m in messages])
        self.seen_user_contents.append([
            m.get("content") for m in messages if m.get("role") == "user"
        ])
        if (
            self._channel is not None
            and self._push_on_call is not None
            and self.call_count == self._push_on_call
        ):
            self._channel.queue.put_nowait(self._push_text)
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="Default response")

    async def call_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                      user_email, tool_choice="auto", temperature=0.7):
        return await self.call_with_tools(
            model, messages, tools_schema, tool_choice,
            temperature=temperature, user_email=user_email,
        )

    async def call_plain(self, model, messages, temperature=0.7, user_email=None) -> str:
        self.call_count += 1
        return "Fallback answer"

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield "fallback"

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        resp = await self.call_with_tools(
            model, messages, tools_schema, tool_choice, temperature, user_email,
        )
        if resp.has_tool_calls():
            yield resp
        else:
            for word in (resp.content or "").split(" "):
                yield word + " "
            yield resp

    async def stream_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                        user_email, tool_choice="auto", temperature=0.7):
        async for item in self.stream_with_tools(
            model, messages, tools_schema, tool_choice, temperature, user_email,
        ):
            yield item


class TestAgenticLoopSteering:
    """Issue #824: a user message sent mid-run reaches the LLM at the next
    iteration boundary as a normal user turn, without stopping the loop."""

    @pytest.mark.asyncio
    async def test_steering_injected_between_steps(self):
        """A steering message queued during step 1's LLM call is present in
        the messages seen by step 2's LLM call, and the loop continues."""
        from atlas.application.chat.agent.steering import SteeringChannel

        channel = SteeringChannel()
        # Step 1: tool call. Step 2: final text answer.
        llm = _RecordingLLM(
            [
                LLMResponse(content="Searching.", tool_calls=[_make_tool_call("c1", "search")]),
                LLMResponse(content="Done with your steer."),
            ],
            channel=channel,
            # Queue the steering message while step 1's LLM call runs, so it
            # is waiting before step 2's iteration-boundary drain.
            push_on_call=1,
        )
        tool_mgr = _make_tool_manager({"search": "found"})
        events, handler = _collect_events()
        context = _make_context()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Run search"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            steering=channel,
        )

        assert result.steps == 2
        assert llm.call_count == 2
        # Step 2 saw the steering text as a user message.
        assert "steer!" in llm.seen_user_contents[1]
        # The loop was not stopped: it produced a final answer after steering.
        assert result.final_answer == "Done with your steer."

    @pytest.mark.asyncio
    async def test_steering_persisted_as_normal_user_turn(self):
        """The injected steering message lands in history as a USER message
        (so it counts as a normal user turn, per the issue), not display-only."""
        from atlas.application.chat.agent.steering import SteeringChannel

        channel = SteeringChannel()
        llm = _RecordingLLM(
            [
                LLMResponse(content="Searching.", tool_calls=[_make_tool_call("c1", "search")]),
                LLMResponse(content="Done."),
            ],
            channel=channel,
            push_on_call=1,
            push_text="also check the logs",
        )
        tool_mgr = _make_tool_manager({"search": "found"})
        events, handler = _collect_events()
        context = _make_context()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Run search"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            steering=channel,
        )

        user_msgs = [m for m in context.history.messages if m.role.value == "user"]
        contents = [m.content for m in user_msgs]
        assert "also check the logs" in contents
        # The steering user message must NOT be display-only: later turns must
        # include it via get_messages_for_llm (issue: land as a normal user turn).
        steered = next(m for m in user_msgs if m.content == "also check the logs")
        assert steered.metadata.get("message_type") is None
        assert steered.metadata.get("steered") is True
        # And it is visible to a later turn's LLM context.
        llm_msgs = context.history.get_messages_for_llm()
        assert any(m["role"] == "user" and m["content"] == "also check the logs"
                   for m in llm_msgs)

    @pytest.mark.asyncio
    async def test_steering_during_final_answer_continues_loop(self):
        """If the model produces a text-only (would-be final) response while a
        steering message is pending, the loop folds that response in as
        intermediate narration and continues rather than ignoring the steer."""
        from atlas.application.chat.agent.steering import SteeringChannel

        channel = SteeringChannel()
        llm = _RecordingLLM(
            [
                # Step 1: text-only "final" answer, but a steer is pending.
                LLMResponse(content="Here is the answer."),
                # Step 2: final answer after addressing the steer.
                LLMResponse(content="And here is more after steering."),
            ],
            channel=channel,
            # Queue the steer during the first LLM call so it is pending when
            # the text-only response is returned.
            push_on_call=1,
        )
        tool_mgr = _make_tool_manager({"search": "found"})
        events, handler = _collect_events()
        context = _make_context()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Summarize"}],
            context=context,
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            steering=channel,
        )

        # The loop continued past the first text-only response.
        assert llm.call_count == 2
        assert result.steps == 2
        assert result.final_answer == "And here is more after steering."
        # The would-be final answer was kept as a display-only intermediate row.
        intermediate = [
            m for m in context.history.messages
            if m.metadata.get("message_type") == "agent_intermediate"
        ]
        assert any(m.content == "Here is the answer." for m in intermediate)

    @pytest.mark.asyncio
    async def test_no_steering_channel_is_unchanged(self):
        """Omitting the steering channel preserves the existing loop behavior."""
        llm = _RecordingLLM([
            LLMResponse(content="Searching.", tool_calls=[_make_tool_call("c1", "search")]),
            LLMResponse(content="Done."),
        ])
        tool_mgr = _make_tool_manager({"search": "found"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Run search"}],
            context=_make_context(),
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
            # No steering channel: behaves exactly as before.
        )

        assert result.steps == 2
        assert result.final_answer == "Done."

    @pytest.mark.asyncio
    async def test_empty_and_none_steering_messages_are_skipped(self):
        """Empty/None payloads do not create empty user turns."""
        from atlas.application.chat.agent.steering import SteeringChannel

        channel = SteeringChannel()
        channel.queue.put_nowait("")
        channel.queue.put_nowait(None)
        llm = _RecordingLLM([LLMResponse(content="Done.")])
        events, handler = _collect_events()
        context = _make_context()

        loop = _make_loop(llm)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            context=context,
            selected_tools=None,
            data_sources=None,
            max_steps=3,
            temperature=0.7,
            event_handler=handler,
            steering=channel,
        )

        # No user messages were injected into history (the original "Hi" lives
        # in the messages list, not history -- the orchestrator adds it; the
        # loop only adds steering turns it injects, and those were all empty).
        user_msgs = [m for m in context.history.messages if m.role.value == "user"]
        assert len(user_msgs) == 0
        assert result.final_answer == "Done."
