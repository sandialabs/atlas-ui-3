"""Plain mode runner - handles simple LLM calls without tools or RAG."""

import logging
from typing import Any, Dict, List, Optional

from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol

from ..utilities import event_notifier

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
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute plain LLM mode.

        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            temperature: LLM temperature parameter
            user_email: Optional user email for per-user API key resolution

        Returns:
            Response dictionary
        """
        # Call LLM
        response_content = await self.llm.call_plain(model, messages, temperature=temperature, user_email=user_email)

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

        return event_notifier.create_chat_response(response_content)

    async def run_streaming(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute plain LLM mode with token streaming."""
        accumulated = ""
        is_first = True

        try:
            async for token in self.llm.stream_plain(
                model, messages, temperature=temperature, user_email=user_email
            ):
                await self.event_publisher.publish_token_stream(
                    token=token, is_first=is_first, is_last=False,
                )
                accumulated += token
                is_first = False

            if not accumulated:
                # Empty stream fallback
                accumulated = await self.llm.call_plain(
                    model, messages, temperature=temperature, user_email=user_email
                )
                await self.event_publisher.publish_chat_response(
                    message=accumulated, has_pending_tools=False,
                )
            else:
                await self.event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )
        except Exception as exc:
            logger.error("Streaming error, sending partial content: %s", exc)
            await self.event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )
            if not accumulated:
                accumulated = f"Error generating response: {exc}"
                await self.event_publisher.publish_chat_response(
                    message=accumulated, has_pending_tools=False,
                )

        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=accumulated,
        )
        session.history.add_message(assistant_message)
        await self.event_publisher.publish_response_complete()

        return event_notifier.create_chat_response(accumulated)
