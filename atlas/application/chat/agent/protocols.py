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
