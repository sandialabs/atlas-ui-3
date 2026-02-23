"""Agent mode runner - handles LLM calls with agent loop execution."""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.messages.models import Message, MessageRole, ToolResult
from atlas.domain.sessions.models import Session
from atlas.interfaces.events import EventPublisher

from ..agent import AgentLoopFactory
from ..agent.protocols import AgentContext
from ..events.agent_event_relay import AgentEventRelay
from ..utilities import event_notifier

logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class AgentModeRunner:
    """
    Runner for agent mode.

    Executes agent loops with event streaming and artifact processing.
    """

    def __init__(
        self,
        agent_loop_factory: AgentLoopFactory,
        event_publisher: EventPublisher,
        artifact_processor: Optional[Callable[[Session, List[ToolResult], Optional[UpdateCallback]], Awaitable[None]]] = None,
        default_strategy: str = "think-act",
    ):
        """
        Initialize agent mode runner.

        Args:
            agent_loop_factory: Factory for creating agent loops
            event_publisher: Event publisher for UI updates
            artifact_processor: Optional callback for processing tool artifacts
            default_strategy: Default agent loop strategy
        """
        self.agent_loop_factory = agent_loop_factory
        self.event_publisher = event_publisher
        self.artifact_processor = artifact_processor
        self.default_strategy = default_strategy

    async def run(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: Optional[List[str]],
        selected_data_sources: Optional[List[str]],
        max_steps: int,
        temperature: float = 0.7,
        agent_loop_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute agent mode.

        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            selected_tools: Optional list of tools to make available
            selected_data_sources: Optional list of data sources
            max_steps: Maximum number of agent steps
            temperature: LLM temperature parameter
            agent_loop_strategy: Strategy name (react, think-act). Falls back to default.

        Returns:
            Response dictionary
        """
        # Get agent loop from factory based on strategy
        strategy = agent_loop_strategy or self.default_strategy
        agent_loop = self.agent_loop_factory.create(strategy)

        # Build agent context
        agent_context = AgentContext(
            session_id=session.id,
            user_email=session.user_email,
            files=session.context.get("files", {}),
            history=session.history,
        )

        # Artifact processor wrapper for handling tool results
        async def process_artifacts(results):
            if self.artifact_processor:
                await self.artifact_processor(session, results, None)

        # Create event relay to map AgentEvents to UI updates
        event_relay = AgentEventRelay(
            event_publisher=self.event_publisher,
            artifact_processor=process_artifacts,
        )

        # Run the loop (always streaming final answer)
        result = await agent_loop.run(
            model=model,
            messages=messages,
            context=agent_context,
            selected_tools=selected_tools,
            data_sources=selected_data_sources,
            max_steps=max_steps,
            temperature=temperature,
            event_handler=event_relay.handle_event,
            streaming=True,
            event_publisher=self.event_publisher,
        )

        # Append final message
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=result.final_answer,
            metadata={"agent_mode": True, "steps": result.steps},
        )
        session.history.add_message(assistant_message)

        # Completion update
        await self.event_publisher.publish_agent_update(
            update_type="agent_completion",
            steps=result.steps
        )

        return event_notifier.create_chat_response(result.final_answer)
