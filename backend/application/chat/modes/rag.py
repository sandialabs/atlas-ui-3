"""RAG mode runner - handles LLM calls with RAG integration."""

import logging
from typing import Dict, Any, List

from domain.sessions.models import Session
from domain.messages.models import Message, MessageRole
from interfaces.llm import LLMProtocol
from interfaces.events import EventPublisher
from ..utilities import notification_utils

logger = logging.getLogger(__name__)


class RagModeRunner:
    """
    Runner for RAG mode.
    
    Executes LLM calls with Retrieval-Augmented Generation integration.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        event_publisher: EventPublisher,
    ):
        """
        Initialize RAG mode runner.
        
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
        data_sources: List[str],
        user_email: str,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Execute RAG mode.
        
        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            data_sources: List of data sources to query
            user_email: User email for authorization
            temperature: LLM temperature parameter
            
        Returns:
            Response dictionary
        """
        # Call LLM with RAG
        response_content = await self.llm.call_with_rag(
            model, messages, data_sources, user_email, temperature=temperature
        )

        # Add assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=response_content,
            metadata={"data_sources": data_sources}
        )
        session.history.add_message(assistant_message)

        # Publish events
        await self.event_publisher.publish_chat_response(
            message=response_content,
            has_pending_tools=False,
        )
        await self.event_publisher.publish_response_complete()

        return notification_utils.create_chat_response(response_content)
