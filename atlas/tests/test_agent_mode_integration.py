"""End-to-end agent-mode tests through ChatService.

ATLAS uses a single native agent loop (the ``AgenticLoop``): the model is
called with ``tool_choice="auto"`` and signals completion by returning text
with no tool calls. These tests exercise the wiring from
``ChatService.handle_chat_message(agent_mode=True)`` through the factory and
loop to the emitted lifecycle events. The loop internals are covered in
``test_agentic_loop.py``; multi-tool execution in ``test_multi_tool_calling.py``.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# Ensure backend root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.agent import AgentLoopFactory  # type: ignore
from atlas.application.chat.service import ChatService  # type: ignore
from atlas.domain.messages.models import ToolResult  # type: ignore
from atlas.interfaces.llm import LLMProtocol, LLMResponse  # type: ignore
from atlas.interfaces.transport import ChatConnectionProtocol  # type: ignore
from atlas.modules.config.config_manager import ConfigManager  # type: ignore


class FakeLLM(LLMProtocol):
    """Fake LLM whose tool-call turn returns a text-only response.

    A text-only response (no tool calls) is how the native agentic loop knows
    it is done, so this drives an immediate, single-step finish.
    """

    def __init__(self, final_text: str = "Done!"):
        self._final_text = final_text

    async def call_plain(self, model_name, messages, temperature: float = 0.7, **kwargs) -> str:
        return self._final_text

    async def call_with_tools(self, model_name, messages, tools_schema, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        return LLMResponse(content=self._final_text)

    async def call_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        return LLMResponse(content=self._final_text)

    async def stream_with_tools(self, model_name, messages, tools_schema, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        # Yield the final text as a token, then the terminal response with no
        # tool calls so the loop completes.
        yield self._final_text
        yield LLMResponse(content=self._final_text)

    async def stream_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        yield self._final_text
        yield LLMResponse(content=self._final_text)

    async def stream_plain(self, model_name, messages, temperature: float = 0.7, **kwargs):
        yield self._final_text


class FakeConnection(ChatConnectionProtocol):
    def __init__(self):
        self.messages: List[Dict[str, Any]] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send_json(self, data: Dict[str, Any]) -> None:
        self.messages.append(data)

    async def receive_json(self) -> Dict[str, Any]:
        return await self._queue.get()

    async def accept(self) -> None:  # pragma: no cover - not used
        pass

    async def close(self) -> None:  # pragma: no cover - not used
        pass


@pytest.mark.asyncio
async def test_agent_mode_finishes_on_text_only_response():
    """Agent mode completes when the model returns text with no tool calls."""
    llm = FakeLLM(final_text="Done!")
    conn = FakeConnection()
    svc = ChatService(llm=llm, tool_manager=None, connection=conn, config_manager=ConfigManager())

    resp = await svc.handle_chat_message(
        session_id=uuid4(),
        content="Hello",
        model="fake",
        agent_mode=True,
        agent_max_steps=3,
    )

    assert resp["type"] == "chat_response"
    assert "Done!" in resp["message"]

    # Verify agent lifecycle events were emitted.
    kinds = [m.get("update_type") for m in conn.messages if m.get("type") == "agent_update"]
    assert "agent_start" in kinds
    assert "agent_turn_start" in kinds
    assert "agent_completion" in kinds


@pytest.mark.asyncio
async def test_agent_mode_legacy_strategy_resolves_to_agentic():
    """A legacy strategy value still runs (resolves to the agentic loop)."""
    llm = FakeLLM(final_text="All set.")
    conn = FakeConnection()
    svc = ChatService(llm=llm, tool_manager=None, connection=conn, config_manager=ConfigManager())

    resp = await svc.handle_chat_message(
        session_id=uuid4(),
        content="Process my data",
        model="fake",
        agent_mode=True,
        agent_max_steps=5,
        agent_loop_strategy="think-act",  # deprecated; must not break
    )

    assert resp["type"] == "chat_response"
    assert "All set." in resp["message"]
    kinds = [m.get("update_type") for m in conn.messages if m.get("type") == "agent_update"]
    assert "agent_completion" in kinds


# ---------------------------------------------------------------------------
# Multi-tool, multi-step e2e: a task that genuinely needs several tool calls
# chained one after another (each turn depends on the previous result).
# ---------------------------------------------------------------------------


def _tool_call(call_id: str, name: str, arguments: str = "{}"):
    """Build a SimpleNamespace mimicking a LiteLLM tool_call object.

    The real tool executor reads ``tool_call.id`` and
    ``tool_call.function.name`` via attribute access, so a fake LLM must
    return objects shaped like this (not plain dicts).
    """
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class ScriptedToolLLM(LLMProtocol):
    """Fake LLM that drives a fixed, multi-step agent run.

    It returns each queued :class:`LLMResponse` once, in order. A response
    with ``tool_calls`` makes the loop execute those tools and call again;
    the first text-only response signals completion. This lets a test script
    an exact "call tool A -> see result -> call tool B -> answer" sequence.
    """

    def __init__(self, turns: List[LLMResponse]):
        self._turns = list(turns)
        self.calls = 0

    def _next(self) -> LLMResponse:
        self.calls += 1
        if self._turns:
            return self._turns.pop(0)
        return LLMResponse(content="All done.")

    async def call_plain(self, model_name, messages, temperature: float = 0.7, **kwargs) -> str:
        return self._next().content or ""

    async def call_with_tools(self, model_name, messages, tools_schema, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        return self._next()

    async def call_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        return self._next()

    async def stream_with_tools(self, model_name, messages, tools_schema, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        resp = self._next()
        if resp.has_tool_calls():
            # Tool-call turns are not streamed token-by-token; the loop just
            # needs the LLMResponse with the tool calls.
            yield resp
        else:
            for word in (resp.content or "").split(" "):
                yield word + " "
            yield resp

    async def stream_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice: str = "auto", temperature: float = 0.7, **kwargs):
        async for item in self.stream_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, **kwargs):
            yield item

    async def stream_plain(self, model_name, messages, temperature: float = 0.7, **kwargs):
        yield self._next().content or ""


def _recording_tool_manager(results: Dict[str, str], call_log: List[str]):
    """Fake tool manager that records execution order and returns canned output."""

    async def fake_execute(tool_call_obj, context=None):
        name = getattr(tool_call_obj, "name", None) or getattr(
            getattr(tool_call_obj, "function", None), "name", "unknown"
        )
        call_log.append(name)
        return ToolResult(
            tool_call_id=getattr(tool_call_obj, "id", "unknown"),
            content=results.get(name, "unknown"),
            success=True,
        )

    mgr = MagicMock()
    mgr.execute_tool = AsyncMock(side_effect=fake_execute)
    mgr.get_tools_schema = MagicMock(return_value=[
        {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
        for n in results
    ])
    mgr.get_server_for_tool = MagicMock(return_value=None)
    return mgr


def _service_with_tools(llm, tool_manager, conn) -> ChatService:
    """Build a ChatService whose agent loop skips approval (tools pre-approved).

    Approval is an orthogonal concern covered elsewhere; pre-approving keeps
    this test focused on the multi-tool *sequencing* behavior. The factory is
    the same one ChatService builds internally, so the full agent-mode path
    (orchestrator -> AgentModeRunner -> factory -> AgenticLoop) still runs.
    """
    factory = AgentLoopFactory(llm=llm, tool_manager=tool_manager)
    factory.skip_approval = True
    return ChatService(
        llm=llm,
        tool_manager=tool_manager,
        connection=conn,
        config_manager=ConfigManager(),
        agent_loop_factory=factory,
    )


@pytest.mark.asyncio
async def test_agent_mode_chains_multiple_tools_in_sequence():
    """A task needing several tools runs them one after another, each turn
    building on the previous result, then synthesizes a final answer.

    Scenario: "What's the weather in Paris in Fahrenheit?" requires
      1. get_weather(Paris)  -> 18 C
      2. convert_c_to_f(18)  -> 64.4 F
      3. text answer using both results.
    """
    call_log: List[str] = []
    llm = ScriptedToolLLM([
        LLMResponse(
            content="Looking up the weather first.",
            tool_calls=[_tool_call("c1", "get_weather", '{"city": "Paris"}')],
        ),
        LLMResponse(
            content="Now converting to Fahrenheit.",
            tool_calls=[_tool_call("c2", "convert_c_to_f", '{"celsius": 18}')],
        ),
        LLMResponse(content="It is 64.4F (18C) in Paris."),
    ])
    tool_mgr = _recording_tool_manager(
        {"get_weather": "18 C", "convert_c_to_f": "64.4 F"}, call_log
    )
    conn = FakeConnection()
    svc = _service_with_tools(llm, tool_mgr, conn)

    resp = await svc.handle_chat_message(
        session_id=uuid4(),
        content="What's the weather in Paris in Fahrenheit?",
        model="fake",
        selected_tools=["get_weather", "convert_c_to_f"],
        agent_mode=True,
        agent_max_steps=10,
        user_email="user@example.com",
    )

    # Both tools ran, in order, exactly once each.
    assert call_log == ["get_weather", "convert_c_to_f"]
    assert tool_mgr.execute_tool.await_count == 2

    # Final answer synthesized from the chained results.
    assert resp["type"] == "chat_response"
    assert "64.4F" in resp["message"]

    # Lifecycle: one turn per LLM call (2 tool turns + 1 final = 3 turns),
    # and tool results were surfaced to the UI for each tool turn.
    updates = [m for m in conn.messages if m.get("type") == "agent_update"]
    turn_starts = [m for m in updates if m.get("update_type") == "agent_turn_start"]
    assert len(turn_starts) == 3
    assert any(m.get("update_type") == "agent_completion" for m in updates)


@pytest.mark.asyncio
async def test_agent_mode_runs_parallel_then_sequential_tools():
    """A turn with two tool calls (parallel) followed by another tool turn,
    proving both fan-out within a turn and chaining across turns work e2e."""
    call_log: List[str] = []
    llm = ScriptedToolLLM([
        LLMResponse(
            content="Fetching both cities at once.",
            tool_calls=[
                _tool_call("c1", "get_weather", '{"city": "Paris"}'),
                _tool_call("c2", "get_weather", '{"city": "Berlin"}'),
            ],
        ),
        LLMResponse(
            content="Summarizing.",
            tool_calls=[_tool_call("c3", "summarize", "{}")],
        ),
        LLMResponse(content="Paris and Berlin are both mild today."),
    ])
    tool_mgr = _recording_tool_manager(
        {"get_weather": "mild", "summarize": "both mild"}, call_log
    )
    conn = FakeConnection()
    svc = _service_with_tools(llm, tool_mgr, conn)

    resp = await svc.handle_chat_message(
        session_id=uuid4(),
        content="Compare the weather in Paris and Berlin.",
        model="fake",
        selected_tools=["get_weather", "summarize"],
        agent_mode=True,
        agent_max_steps=10,
        user_email="user@example.com",
    )

    # 2 parallel get_weather calls + 1 summarize = 3 executions total.
    assert tool_mgr.execute_tool.await_count == 3
    assert call_log.count("get_weather") == 2
    assert call_log[-1] == "summarize"

    assert resp["type"] == "chat_response"
    assert "mild" in resp["message"].lower()

    turn_starts = [
        m for m in conn.messages
        if m.get("type") == "agent_update" and m.get("update_type") == "agent_turn_start"
    ]
    # Turn 1 (parallel tools) + turn 2 (summarize) + turn 3 (final) = 3.
    assert len(turn_starts) == 3
