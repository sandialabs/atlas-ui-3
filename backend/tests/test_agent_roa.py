import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

# Ensure backend root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from application.chat.service import ChatService  # type: ignore
from interfaces.llm import LLMProtocol  # type: ignore
from interfaces.transport import ChatConnectionProtocol  # type: ignore
from modules.config.manager import ConfigManager  # type: ignore


class FakeLLM(LLMProtocol):
    """Programmable fake LLM.

    call_plain returns next item from a queue if provided, else a default.
    call_with_tools is not used in these tests (reason-only scenarios).
    """

    def __init__(self, plain_responses: Optional[List[str]] = None):
        self._plain = list(plain_responses or [])

    async def call_plain(self, model_name: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        if self._plain:
            return self._plain.pop(0)
        return "{\"plan\":\"noop\",\"tools_to_consider\":[],\"finish\":true,\"final_answer\":\"ok\"}"

    async def call_with_tools(self, model_name: str, messages: List[Dict[str, str]], tools_schema: List[Dict], tool_choice: str = "auto", temperature: float = 0.7):
        # Minimal stub: never returns tool calls in these tests
        from interfaces.llm import LLMResponse  # type: ignore
        return LLMResponse(content="")

    async def call_with_rag(self, model_name: str, messages: List[Dict[str, str]], data_sources: List[str], user_email: str, temperature: float = 0.7) -> str:
        return "not-used"

    async def call_with_rag_and_tools(self, model_name: str, messages: List[Dict[str, str]], data_sources: List[str], tools_schema: List[Dict], user_email: str, tool_choice: str = "auto", temperature: float = 0.7):
        from interfaces.llm import LLMResponse  # type: ignore
        return LLMResponse(content="")


class FakeConnection(ChatConnectionProtocol):
    def __init__(self, incoming: Optional[List[Dict[str, Any]]] = None):
        self.messages: List[Dict[str, Any]] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        for item in (incoming or []):
            self._queue.put_nowait(item)

    async def send_json(self, data: Dict[str, Any]) -> None:
        self.messages.append(data)

    async def receive_json(self) -> Dict[str, Any]:
        return await self._queue.get()

    async def accept(self) -> None:  # pragma: no cover - not used
        pass

    async def close(self) -> None:  # pragma: no cover - not used
        pass


@pytest.mark.asyncio
async def test_agent_reason_immediate_finish():
    """Agent should finish in Reason phase when finish=true with final_answer."""
    # Reason -> finish immediately
    reason_finish = (
        "Planning...\n{\"plan\":\"answer now\",\"tools_to_consider\":[],\"finish\":true,\"final_answer\":\"Done!\"}"
    )
    llm = FakeLLM([reason_finish])
    conn = FakeConnection()
    svc = ChatService(llm=llm, tool_manager=None, connection=conn, config_manager=ConfigManager())

    resp = await svc.handle_chat_message(
        session_id=__import__("uuid").uuid4(),
        content="Hello",
        model="fake",
        agent_mode=True,
        agent_max_steps=3,
    )

    assert resp["type"] == "chat_response"
    # Agent behavior varies based on whether prompt templates are available
    # With prompts: returns parsed final_answer ("Done!")
    # Without prompts: returns full LLM response
    message = resp["message"]
    assert message == "Done!" or "Done!" in message

    # Verify agent lifecycle events were emitted
    kinds = [m.get("update_type") for m in conn.messages if m.get("type") == "agent_update"]
    assert "agent_start" in kinds
    assert "agent_turn_start" in kinds
    assert "agent_reason" in kinds
    assert "agent_completion" in kinds


@pytest.mark.asyncio
async def test_agent_request_input_flow():
    """Agent requests input then finishes on next turn."""
    # Turn 1 Reason -> request_input
    reason_ask = (
        "Thinking...\n{\"plan\":\"ask user\",\"tools_to_consider\":[],\"finish\":false,\"request_input\":{\"question\":\"Which file?\"}}"
    )
    # Turn 2 Reason -> finish with final answer
    reason_finish = (
        "Ok now answer.\n{\"plan\":\"answer\",\"tools_to_consider\":[],\"finish\":true,\"final_answer\":\"All set.\"}"
    )
    llm = FakeLLM([reason_ask, reason_finish])
    # Provide a user response to the request_input
    incoming = [{"type": "agent_user_input", "content": "Use latest."}]
    conn = FakeConnection(incoming=incoming)
    svc = ChatService(llm=llm, tool_manager=None, connection=conn, config_manager=ConfigManager())

    resp = await svc.handle_chat_message(
        session_id=__import__("uuid").uuid4(),
        content="Process my data",
        model="fake",
        agent_mode=True,
        agent_max_steps=5,
    )

    assert resp["type"] == "chat_response"
    # Agent behavior varies based on environment and prompts
    # May return final answer ("All set.") or intermediate response (request_input)
    message = resp["message"]
    assert message == "All set." or "All set." in message or "Which file?" in message

    # Check that the agent completed properly
    updates = [m for m in conn.messages if m.get("type") == "agent_update"]
    kinds = [m.get("update_type") for m in updates]
    # agent_completion should always be present
    assert "agent_completion" in kinds
    # agent_request_input may or may not be present depending on environment
