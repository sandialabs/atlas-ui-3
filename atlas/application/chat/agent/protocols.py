from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol
from uuid import UUID

from atlas.domain.messages.models import ConversationHistory
from atlas.interfaces.events import EventPublisher


@dataclass
class AgentContext:
    session_id: UUID
    user_email: Optional[str]
    files: Dict[str, Any]
    history: ConversationHistory
    # Conversation scope used to key persistent MCP sessions. Must mirror the
    # value regular (non-agent) chat passes so stateful MCP servers keep a
    # single reused session across the loop's sequential tool calls. When
    # absent, MCP tool calls fall back to single-use sessions and stateful
    # servers raise session errors. Defaults to None for backward compatibility.
    conversation_id: Optional[str] = None


@dataclass
class AgentResult:
    final_answer: str
    steps: int
    metadata: Dict[str, Any]


@dataclass
class AgentEvent:
    type: str
    payload: Dict[str, Any]


AgentEventHandler = Callable[[AgentEvent], Awaitable[None]]


class AgentLoopProtocol(Protocol):
    async def run(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        context: AgentContext,
        selected_tools: Optional[List[str]],
        data_sources: Optional[List[str]],
        max_steps: int,
        temperature: float,
        event_handler: AgentEventHandler,
        streaming: bool = False,
        event_publisher: Optional[EventPublisher] = None,
    ) -> AgentResult: ...
