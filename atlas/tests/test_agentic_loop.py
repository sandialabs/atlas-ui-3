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

        loop = _make_loop(llm, tool_mgr)
        result = await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Search for test"}],
            context=_make_context(),
            selected_tools=["search"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "Based on the search results, here is your answer."
        assert result.steps == 2

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


# -- Tests: RAG integration ---------------------------------------------

class TestAgenticLoopRAG:

    @pytest.mark.asyncio
    async def test_rag_path_used_when_data_sources_present(self):
        """When data_sources and user_email are provided, the loop should
        use call_with_rag_and_tools instead of call_with_tools."""
        rag_called = []

        class RAGLLM(FakeLLM):
            async def call_with_rag_and_tools(self, model, messages, data_sources, *a, **kw):
                rag_called.append(data_sources)
                return LLMResponse(content="RAG answer")

        llm = RAGLLM()
        tool_mgr = _make_tool_manager({"tool1": "r"})
        events, handler = _collect_events()

        loop = _make_loop(llm, tool_mgr)
        await loop.run(
            model="test-model",
            messages=[{"role": "user", "content": "Search with RAG"}],
            context=_make_context(),
            selected_tools=["tool1"],
            data_sources=["source1"],
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert len(rag_called) == 1
        assert rag_called[0] == ["source1"]
