"""Plain mode runner - handles simple LLM calls without tools or RAG."""

import logging
from typing import Dict, Any, List

from domain.sessions.models import Session
from domain.messages.models import Message, MessageRole
from interfaces.llm import LLMProtocol
from interfaces.events import EventPublisher
from ..utilities import notification_utils

logger = logging.getLogger(__name__)


class PlainModeRunner:
    """
    Runner for plain LLM mode.
    
    Executes simple LLM calls without tools or RAG integration.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        event_publisher: EventPublisher,
    ):
        """
        Initialize plain mode runner.
        
        Args:
            llm: LLM protocol implementation
            event_publisher: Event publisher for UI updates
        """
        self.llm = llm
        self.event_publisher = event_publisher
    
    async def run(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Execute plain LLM mode.
        
        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            temperature: LLM temperature parameter
            
        Returns:
            Response dictionary
        """
        # Call LLM
        response_content = await self.llm.call_plain(model, messages, temperature=temperature)

        # Add assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=response_content
        )
        session.history.add_message(assistant_message)

        # Publish events
        await self.event_publisher.publish_chat_response(
            message=response_content,
            has_pending_tools=False,
        )
        await self.event_publisher.publish_response_complete()

        return notification_utils.create_chat_response(response_content)
