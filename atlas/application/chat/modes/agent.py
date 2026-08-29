"""Agent mode runner - handles LLM calls with agent loop execution."""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.errors import LLMMalformedToolCallError
from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    Message,
    MessageRole,
    ToolResult,
)
from atlas.domain.sessions.models import Session
from atlas.interfaces.events import EventPublisher

from ..agent import AgentLoopFactory
from ..agent.protocols import AgentContext
from ..agent.steering import SteeringChannel
from ..events.agent_event_relay import AgentEventRelay
from ..utilities import event_notifier
from ..utilities.agent_digest import build_tool_digest
from ..utilities.interrupted_turn import INTERRUPTED_TURN_CONTENT

# Closing text for a turn that ended because the model's tool call could not be
# parsed. Mirrors INTERRUPTED_TURN_CONTENT: the turn must not be saved without an
# assistant reply, and the next turn should see why it stopped.
MALFORMED_TOOL_CALL_TURN_CONTENT = (
    "(This turn ended early: the model's tool call was cut off before it "
    "finished and could not be run.)"
)
MALFORMED_TOOL_CALL_TURN_CONTENT_INVALID_JSON = (
    "(This turn ended early: the model's tool call could not be read as valid "
    "JSON and could not be run.)"
)

logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]

_AGENT_NARRATION_INSTRUCTION = (
    "In agent mode, briefly state what you are about to do before each tool call. "
    "Keep these progress updates concise."
)


def _ensure_agent_narration_instruction(messages: List[Dict[str, Any]]) -> None:
    """Ensure agent-mode LLM calls ask for per-tool-call narration."""
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content")
        if isinstance(content, str) and _AGENT_NARRATION_INSTRUCTION not in content:
            messages[0]["content"] = f"{content.rstrip()}\n\n{_AGENT_NARRATION_INSTRUCTION}"
        return

    messages.insert(0, {"role": "system", "content": _AGENT_NARRATION_INSTRUCTION})


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
        default_strategy: str = "agentic",
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

    def _get_send_json(self) -> Optional[UpdateCallback]:
        """Get send_json callback from the event publisher if available.

        Mirrors ToolsModeRunner so the artifact processor can emit
        files_update / canvas_files events for generated files.
        """
        if hasattr(self.event_publisher, "send_json"):
            return self.event_publisher.send_json
        logger.warning(
            "AgentModeRunner event_publisher has no send_json; canvas/file "
            "updates for generated artifacts will be skipped. Type: %s",
            type(self.event_publisher),
        )
        return None

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
        steering: Optional[SteeringChannel] = None,
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
            agent_loop_strategy: Accepted for backward compatibility; the
                native agentic loop is always used.
            steering: Optional steering channel (issue #824). When supplied,
                the loop drains user messages from it at each iteration
                boundary so the user can steer a running agent. Activated here
                (not by the transport) so the channel only accepts messages
                while a loop is genuinely consuming it.

        Returns:
            Response dictionary
        """
        # Get agent loop from factory based on strategy
        strategy = agent_loop_strategy or self.default_strategy
        agent_loop = self.agent_loop_factory.create(strategy)
        _ensure_agent_narration_instruction(messages)

        # Build agent context
        agent_context = AgentContext(
            session_id=session.id,
            user_email=session.user_email,
            files=session.context.get("files", {}),
            history=session.history,
            # Mirror the conversation scope regular chat uses so the agentic
            # loop's sequential MCP tool calls reuse one persistent session
            # (ChatService always populates this, falling back to str(session.id)).
            conversation_id=session.context.get("conversation_id", str(session.id)),
            # Trusted compliance level stashed on the session by ChatService.
            compliance_level=session.context.get("compliance_level"),
        )

        # Artifact processor wrapper for handling tool results.
        # The update callback must be a real send_json so the artifact processor
        # can emit the files_update / canvas_files events that surface generated
        # files (e.g. a pptx) into the session and canvas. Passing None here meant
        # agent-mode artifacts were stored (visible in the File library) but never
        # pushed to the canvas/session, unlike standard tools mode.
        send_json = self._get_send_json()

        async def process_artifacts(results):
            if self.artifact_processor:
                await self.artifact_processor(session, results, send_json)

        # Create event relay to map AgentEvents to UI updates
        event_relay = AgentEventRelay(
            event_publisher=self.event_publisher,
            artifact_processor=process_artifacts,
        )

        # Everything the loop appends to history from here on belongs to this
        # turn; remember where it starts so the tool digest covers only it.
        turn_start_index = len(session.history.messages)

        # Run the loop (always streaming final answer). The steering channel
        # is activated around the run so the transport only pushes into it while
        # a loop is actually draining -- a turn that requested agent mode but
        # fell back to a non-agent turn never reaches here, so its channel
        # stays inactive and a later steering message starts a fresh turn
        # instead of being swallowed undrained (issue #824).
        if steering is not None:
            steering.activate()
        try:
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
                steering=steering,
            )
        except asyncio.CancelledError:
            # Stop button, client disconnect, or reset_session (issue #755).
            # The loop has already flushed every completed step's narration and
            # tool_call rows into history, but nothing closes the turn, so the
            # saved conversation would end mid-trajectory and the next turn
            # would see no trace of the work. Append a terminal assistant
            # message carrying the same tool digest a completed turn gets, so
            # the turn is well-formed and the follow-up can pick up from there.
            self._close_turn(
                session,
                turn_start_index,
                content=INTERRUPTED_TURN_CONTENT,
                metadata={"agent_mode": True, "interrupted": True},
            )
            await self._publish_completion(steps=0)
            raise
        except LLMMalformedToolCallError as malformed:
            # Same contract as the interrupted-turn path above: the loop raised
            # before it could append anything for this step, so without a
            # closing message the turn is saved with no assistant reply and the
            # next turn sees no trace of it.
            self._close_turn(
                session,
                turn_start_index,
                content=(
                    MALFORMED_TOOL_CALL_TURN_CONTENT
                    if getattr(malformed, "truncated", False)
                    else MALFORMED_TOOL_CALL_TURN_CONTENT_INVALID_JSON
                ),
                metadata={"agent_mode": True, "incomplete": True},
            )
            await self._publish_completion(steps=0)
            raise
        except Exception:
            # Send agent completion event so the frontend clears agent UI state
            # (currentAgentStep, thinking indicator, etc.) before the error
            # message arrives via the WebSocket error handler.
            await self._publish_completion(steps=0)
            raise
        finally:
            # Always release the steering channel so a later chat on this
            # connection never routes into a queue whose loop has exited
            # (issue #824).
            if steering is not None:
                steering.deactivate()

        # Append final message. Ordering contract with AgenticLoop: the loop
        # is the single owner of narration persistence and has already flushed
        # this turn's intermediate narration and tool_call rows into
        # session.history (per step), so this append must come after run()
        # returns — reloaded history reads user -> intermediate assistant
        # -> tool_call(s) -> assistant. Guarded by
        # TestAgentModeRunnerPersistedOrder in test_tool_call_persistence.py.
        self._close_turn(
            session,
            turn_start_index,
            content=result.final_answer,
            metadata={"agent_mode": True, "steps": result.steps},
        )

        # Completion update
        await self.event_publisher.publish_agent_update(
            update_type="agent_completion",
            steps=result.steps
        )

        # Issue #824: any steering message still queued when the loop stopped
        # draining arrived too late to be injected (the loop had already
        # exited -- e.g. a steer typed during the final-answer tail or after
        # the step budget ran out). The frontend has already shown the user's
        # message, so surface it explicitly rather than silently dropping it.
        if steering is not None:
            leftovers = steering.drain_leftovers()
            if leftovers:
                logger.warning(
                    "Agent turn ended with %d undrained steering message(s); "
                    "notifying the user to resend", len(leftovers),
                )
                try:
                    await self.event_publisher.publish_warning(
                        message=(
                            "Your message arrived as the agent was finishing "
                            "its turn and was not applied to this run. Please "
                            "send it again to start a new turn."
                        ),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Failed to publish steering-leftover warning: %s", exc)

        return event_notifier.create_chat_response(result.final_answer)

    def _close_turn(
        self,
        session: Session,
        turn_start_index: int,
        content: str,
        metadata: Dict[str, Any],
    ) -> Message:
        """Append the turn's closing assistant message, carrying a tool digest.

        The digest (issue #755) is the only model-visible record of what the
        agent ran: the loop's working transcript dies with the turn and the
        persisted ``tool_call`` rows are display-only. Without it a follow-up
        turn re-derives everything the agent already established — measured at
        roughly a fifth of tool calls in ordinary, uninterrupted use.
        """
        try:
            digest = build_tool_digest(session.history.messages, turn_start_index)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to build agent tool digest", exc_info=True)
            digest = None
        if digest:
            metadata = {**metadata, AGENT_TOOL_DIGEST_KEY: digest}

        message = Message(
            role=MessageRole.ASSISTANT,
            content=content,
            metadata=metadata,
        )
        session.history.add_message(message)
        return message

    async def _publish_completion(self, steps: int) -> None:
        """Emit agent_completion, tolerating a dead transport."""
        try:
            await self.event_publisher.publish_agent_update(
                update_type="agent_completion",
                steps=steps,
            )
        except Exception as cleanup_exc:
            logger.warning("Failed to send agent_completion cleanup event: %s", cleanup_exc)
