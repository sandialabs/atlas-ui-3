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
from typing import Any, Dict, List
from uuid import uuid4

import pytest

# Ensure backend root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.service import ChatService  # type: ignore
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
