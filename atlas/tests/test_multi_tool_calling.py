"""Tests for parallel multi-tool calling support (issue #353).

Verifies that when an LLM returns multiple tool calls in a single response,
they are executed concurrently via asyncio.gather and all results are
correctly returned to the conversation.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.utilities.tool_executor import (
    execute_multiple_tools,
)
from atlas.domain.messages.models import ToolResult


def _make_tool_call(tool_id: str, name: str, arguments: str = "{}"):
    """Build a SimpleNamespace mimicking a LiteLLM tool_call object."""
    return SimpleNamespace(
        id=tool_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _make_tool_manager(results: Dict[str, str]):
    """Return a mock tool manager that maps tool names to result strings."""
    async def fake_execute(tool_call_obj, context=None):
        return ToolResult(
            tool_call_id=tool_call_obj.id,
            content=results.get(tool_call_obj.name, "unknown"),
            success=True,
        )

    mgr = MagicMock()
    mgr.execute_tool = AsyncMock(side_effect=fake_execute)
    mgr.get_tools_schema = MagicMock(return_value=[])
    return mgr


# ---------------------------------------------------------------------------
# execute_multiple_tools
# ---------------------------------------------------------------------------

class TestExecuteMultipleTools:

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        results = await execute_multiple_tools(
            tool_calls=[],
            session_context={},
            tool_manager=MagicMock(),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_single_tool_delegates_to_single(self):
        mgr = _make_tool_manager({"toolA": "result-A"})
        tc = _make_tool_call("id-1", "toolA")

        results = await execute_multiple_tools(
            tool_calls=[tc],
            session_context={},
            tool_manager=mgr,
            skip_approval=True,
        )

        assert len(results) == 1
        assert results[0].content == "result-A"
        assert results[0].tool_call_id == "id-1"

    @pytest.mark.asyncio
    async def test_multiple_tools_run_concurrently(self):
        """Verify that two tool calls overlap in time (parallel execution)."""
        execution_log: List[str] = []

        async def slow_execute(tool_call_obj, context=None):
            execution_log.append(f"start-{tool_call_obj.name}")
            await asyncio.sleep(0.05)
            execution_log.append(f"end-{tool_call_obj.name}")
            return ToolResult(
                tool_call_id=tool_call_obj.id,
                content=f"result-{tool_call_obj.name}",
                success=True,
            )

        mgr = MagicMock()
        mgr.execute_tool = AsyncMock(side_effect=slow_execute)
        mgr.get_tools_schema = MagicMock(return_value=[])

        tc1 = _make_tool_call("id-1", "toolA")
        tc2 = _make_tool_call("id-2", "toolB")

        results = await execute_multiple_tools(
            tool_calls=[tc1, tc2],
            session_context={},
            tool_manager=mgr,
            skip_approval=True,
        )

        assert len(results) == 2
        assert results[0].content == "result-toolA"
        assert results[1].content == "result-toolB"

        # Both should have started before either finished (parallel)
        assert execution_log.index("start-toolA") < execution_log.index("end-toolB")
        assert execution_log.index("start-toolB") < execution_log.index("end-toolA")

    @pytest.mark.asyncio
    async def test_results_preserve_order(self):
        """Results should match the order of input tool_calls."""
        mgr = _make_tool_manager({"alpha": "A", "beta": "B", "gamma": "C"})
        calls = [
            _make_tool_call("1", "alpha"),
            _make_tool_call("2", "beta"),
            _make_tool_call("3", "gamma"),
        ]

        results = await execute_multiple_tools(
            tool_calls=calls,
            session_context={},
            tool_manager=mgr,
            skip_approval=True,
        )

        assert [r.tool_call_id for r in results] == ["1", "2", "3"]
        assert [r.content for r in results] == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_exception_converted_to_error_result(self):
        """If one tool raises an exception, it becomes an error ToolResult."""
        async def mixed_execute(tool_call_obj, context=None):
            if tool_call_obj.name == "bad_tool":
                raise RuntimeError("connection timeout")
            return ToolResult(
                tool_call_id=tool_call_obj.id,
                content="ok",
                success=True,
            )

        mgr = MagicMock()
        mgr.execute_tool = AsyncMock(side_effect=mixed_execute)
        mgr.get_tools_schema = MagicMock(return_value=[])

        calls = [
            _make_tool_call("1", "good_tool"),
            _make_tool_call("2", "bad_tool"),
        ]

        results = await execute_multiple_tools(
            tool_calls=calls,
            session_context={},
            tool_manager=mgr,
            skip_approval=True,
        )

        assert len(results) == 2
        assert results[0].success is True
        assert results[0].content == "ok"
        assert results[1].success is False
        assert "connection timeout" in results[1].content


# ---------------------------------------------------------------------------
# Agent loop integration: verify multi-tool messages are correctly built
# ---------------------------------------------------------------------------

class TestAgentLoopMultiToolMessages:
    """Test that agent loops correctly append multiple tool results to messages."""

    @pytest.mark.asyncio
    async def test_act_loop_executes_all_non_finished_tools(self):
        """Act loop should execute ALL non-finished tool calls, not just the first."""
        from atlas.application.chat.agent.act_loop import ActAgentLoop
        from atlas.interfaces.llm import LLMResponse

        call_count = 0

        class MultiToolLLM:
            async def call_plain(self, *a, **kw):
                return "fallback"

            async def call_with_tools(self, model, messages, tools_schema, tool_choice, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # Return two real tool calls
                    return LLMResponse(
                        content="",
                        tool_calls=[
                            {"id": "tc1", "type": "function", "function": {"name": "toolA", "arguments": "{}"}},
                            {"id": "tc2", "type": "function", "function": {"name": "toolB", "arguments": "{}"}},
                        ],
                    )
                # Second call: finish
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {"id": "tc3", "type": "function", "function": {"name": "finished", "arguments": '{"final_answer": "done"}'}},
                    ],
                )

            async def call_with_rag_and_tools(self, *a, **kw):
                return LLMResponse(content="")

            async def stream_plain(self, *a, **kw):
                yield "done"

        mgr = _make_tool_manager({"toolA": "result-A", "toolB": "result-B"})
        mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "toolA", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "toolB", "parameters": {"type": "object", "properties": {}}}},
        ])

        events: List[Dict[str, Any]] = []

        async def handler(event):
            events.append({"type": event.type, "payload": event.payload})

        from atlas.application.chat.agent.protocols import AgentContext
        from atlas.domain.messages.models import ConversationHistory

        loop = ActAgentLoop(
            llm=MultiToolLLM(),
            tool_manager=mgr,
            prompt_provider=None,
        )
        loop.skip_approval = True

        result = await loop.run(
            model="test",
            messages=[{"role": "user", "content": "test"}],
            context=AgentContext(session_id="s1", user_email="test@test.com", files={}, history=ConversationHistory()),
            selected_tools=["toolA", "toolB"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "done"

        # Check that agent_tool_results event included both results
        tool_events = [e for e in events if e["type"] == "agent_tool_results"]
        assert len(tool_events) >= 1
        first_tool_event = tool_events[0]
        tool_results = first_tool_event["payload"]["results"]
        assert len(tool_results) == 2

    @pytest.mark.asyncio
    async def test_react_loop_executes_multiple_tools(self):
        """ReAct loop should execute all tool calls in a single Act step."""
        from atlas.application.chat.agent.react_loop import ReActAgentLoop
        from atlas.interfaces.llm import LLMResponse

        call_count = 0

        class MultiToolLLM:
            async def call_plain(self, *a, **kw):
                return "fallback"

            async def call_with_tools(self, model, messages, tools_schema, tool_choice, **kw):
                nonlocal call_count
                call_count += 1

                # First call: Reason phase - plan to use tools
                if call_count == 1:
                    return LLMResponse(
                        content="",
                        tool_calls=[{
                            "id": "r1",
                            "type": "function",
                            "function": {
                                "name": "agent_decide_next",
                                "arguments": '{"finish": false, "next_plan": "use tools"}',
                            },
                        }],
                    )
                # Second call: Act phase - return two tool calls
                if call_count == 2:
                    return LLMResponse(
                        content="",
                        tool_calls=[
                            {"id": "tc1", "type": "function", "function": {"name": "toolA", "arguments": "{}"}},
                            {"id": "tc2", "type": "function", "function": {"name": "toolB", "arguments": "{}"}},
                        ],
                    )
                # Third call: Observe phase - done
                if call_count == 3:
                    return LLMResponse(
                        content="observation",
                        tool_calls=[{
                            "id": "o1",
                            "type": "function",
                            "function": {
                                "name": "agent_observe_decide",
                                "arguments": '{"should_continue": false, "final_answer": "all done"}',
                            },
                        }],
                    )
                return LLMResponse(content="fallback")

            async def call_with_rag_and_tools(self, *a, **kw):
                return LLMResponse(content="")

            async def stream_plain(self, *a, **kw):
                yield "done"

        mgr = _make_tool_manager({"toolA": "result-A", "toolB": "result-B"})
        mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "toolA", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "toolB", "parameters": {"type": "object", "properties": {}}}},
        ])

        events: List[Dict[str, Any]] = []

        async def handler(event):
            events.append({"type": event.type, "payload": event.payload})

        from atlas.application.chat.agent.protocols import AgentContext
        from atlas.domain.messages.models import ConversationHistory

        loop = ReActAgentLoop(
            llm=MultiToolLLM(),
            tool_manager=mgr,
            prompt_provider=None,
        )
        loop.skip_approval = True

        result = await loop.run(
            model="test",
            messages=[{"role": "user", "content": "test"}],
            context=AgentContext(session_id="s1", user_email="test@test.com", files={}, history=ConversationHistory()),
            selected_tools=["toolA", "toolB"],
            data_sources=None,
            max_steps=5,
            temperature=0.7,
            event_handler=handler,
        )

        assert result.final_answer == "all done"

        # Check that agent_tool_results event included both results
        tool_events = [e for e in events if e["type"] == "agent_tool_results"]
        assert len(tool_events) >= 1
        tool_results = tool_events[0]["payload"]["results"]
        assert len(tool_results) == 2
