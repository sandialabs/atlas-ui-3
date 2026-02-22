"""Agent event relay - maps AgentEvents to EventPublisher calls."""

import logging
from typing import Any, Awaitable, Callable, Optional

from atlas.interfaces.events import EventPublisher

from ..agent.protocols import AgentEvent

logger = logging.getLogger(__name__)

# Constants
UNKNOWN_TOOL_NAME = "unknown"


class AgentEventRelay:
    """
    Translates agent loop events to UI update events.

    Maps AgentEvent instances to appropriate EventPublisher method calls,
    providing a clean separation between agent logic and UI transport.
    """

    def __init__(
        self,
        event_publisher: EventPublisher,
        artifact_processor: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        """
        Initialize agent event relay.

        Args:
            event_publisher: Publisher for sending UI updates
            artifact_processor: Optional callback for processing tool artifacts
        """
        self.event_publisher = event_publisher
        self.artifact_processor = artifact_processor

    async def handle_event(self, evt: AgentEvent) -> None:
        """
        Handle an agent event and relay it to the UI.

        Args:
            evt: Agent event to handle
        """
        et = evt.type
        p = evt.payload or {}

        # Map event types to publisher calls
        if et == "agent_start":
            await self.event_publisher.publish_agent_update(
                update_type="agent_start",
                max_steps=p.get("max_steps"),
                strategy=p.get("strategy"),
            )

        elif et == "agent_turn_start":
            await self.event_publisher.publish_agent_update(
                update_type="agent_turn_start",
                step=p.get("step"),
            )

        elif et == "agent_reason":
            await self.event_publisher.publish_agent_update(
                update_type="agent_reason",
                message=p.get("message"),
                step=p.get("step"),
            )

        elif et == "agent_request_input":
            await self.event_publisher.publish_agent_update(
                update_type="agent_request_input",
                question=p.get("question"),
                step=p.get("step"),
            )

        elif et == "agent_tool_start":
            await self.event_publisher.publish_tool_start(
                tool_name=p.get("tool", UNKNOWN_TOOL_NAME),
            )

        elif et == "agent_tool_complete":
            await self.event_publisher.publish_tool_complete(
                tool_name=p.get("tool", UNKNOWN_TOOL_NAME),
                result=p.get("result"),
            )

        elif et == "agent_tool_results":
            # Delegate artifact processing to external handler
            if self.artifact_processor:
                results = p.get("results") or []
                if results:
                    await self.artifact_processor(results)

        elif et == "agent_observe":
            await self.event_publisher.publish_agent_update(
                update_type="agent_observe",
                message=p.get("message"),
                step=p.get("step"),
            )

        elif et == "agent_completion":
            await self.event_publisher.publish_agent_update(
                update_type="agent_completion",
                steps=p.get("steps"),
            )

        elif et == "agent_token_stream":
            await self.event_publisher.publish_token_stream(
                token=p.get("token", ""),
                is_first=p.get("is_first", False),
                is_last=p.get("is_last", False),
            )

        elif et == "agent_error":
            await self.event_publisher.publish_agent_update(
                update_type="agent_error",
                message=p.get("message"),
            )
