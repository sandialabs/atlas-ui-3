"""Tools mode runner - handles LLM calls with tool execution."""

import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable

from domain.sessions.models import Session
from domain.messages.models import Message, MessageRole, ToolResult
from interfaces.llm import LLMProtocol
from interfaces.tools import ToolManagerProtocol
from interfaces.events import EventPublisher
from modules.prompts.prompt_provider import PromptProvider
from ..utilities import tool_executor, event_notifier, error_handler
from ..preprocessors.message_builder import build_session_context

logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class ToolsModeRunner:
    """
    Runner for tools mode.
    
    Executes LLM calls with tool integration, including tool execution
    and artifact processing.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        tool_manager: ToolManagerProtocol,
        event_publisher: EventPublisher,
        prompt_provider: Optional[PromptProvider] = None,
        artifact_processor: Optional[Callable[[Session, List[ToolResult], Optional[UpdateCallback]], Awaitable[None]]] = None,
        config_manager=None,
    ):
        """
        Initialize tools mode runner.
        
        Args:
            llm: LLM protocol implementation
            tool_manager: Tool manager for tool execution
            event_publisher: Event publisher for UI updates
            prompt_provider: Optional prompt provider
            artifact_processor: Optional callback for processing tool artifacts
            config_manager: Optional config manager for approval settings
        """
        self.llm = llm
        self.tool_manager = tool_manager
        self.event_publisher = event_publisher
        self.prompt_provider = prompt_provider
        self.artifact_processor = artifact_processor
        self.config_manager = config_manager
        self.skip_approval = False

        # Verify event_publisher has send_json for elicitation support
        if hasattr(event_publisher, 'send_json'):
            logger.debug(f"ToolsModeRunner initialized with event_publisher that has send_json: {type(event_publisher)}")
        else:
            logger.warning(f"ToolsModeRunner initialized with event_publisher WITHOUT send_json: {type(event_publisher)}")

    async def run(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: List[str],
        selected_data_sources: Optional[List[str]] = None,
        user_email: Optional[str] = None,
        tool_choice_required: bool = False,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Execute tools mode.
        
        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            selected_tools: List of tools to make available
            selected_data_sources: Optional list of data sources (for RAG+tools)
            user_email: Optional user email for authorization
            tool_choice_required: Whether tool use is required
            update_callback: Optional callback for streaming updates
            temperature: LLM temperature parameter
            
        Returns:
            Response dictionary
        """
        # Resolve tool schemas
        tools_schema = await error_handler.safe_get_tools_schema(self.tool_manager, selected_tools)

        # Call LLM with tools (and RAG if provided)
        llm_response = await error_handler.safe_call_llm_with_tools(
            llm_caller=self.llm,
            model=model,
            messages=messages,
            tools_schema=tools_schema,
            data_sources=selected_data_sources,
            user_email=user_email,
            tool_choice=("required" if tool_choice_required else "auto"),
            temperature=temperature,
        )

        # No tool calls -> treat as plain content
        if not llm_response or not llm_response.has_tool_calls():
            content = llm_response.content if llm_response else ""
            assistant_message = Message(role=MessageRole.ASSISTANT, content=content)
            session.history.add_message(assistant_message)
            
            await self.event_publisher.publish_chat_response(
                message=content,
                has_pending_tools=False,
            )
            await self.event_publisher.publish_response_complete()
            
            return event_notifier.create_chat_response(content)

        # Execute tool workflow
        session_context = build_session_context(session)

        # Ensure update_callback is never None (critical for elicitation)
        effective_callback = update_callback
        if effective_callback is None:
            effective_callback = self._get_send_json()
            logger.debug("Tools mode: update_callback was None, using event_publisher.send_json fallback")

        if effective_callback is None:
            logger.warning("Tools mode: No update callback available - elicitation will not work!")

        final_response, tool_results = await tool_executor.execute_tools_workflow(
            llm_response=llm_response,
            messages=messages,
            model=model,
            session_context=session_context,
            tool_manager=self.tool_manager,
            llm_caller=self.llm,
            prompt_provider=self.prompt_provider,
            update_callback=effective_callback,
            config_manager=self.config_manager,
            skip_approval=self.skip_approval,
        )

        # Process artifacts if handler provided
        if self.artifact_processor:
            await self.artifact_processor(session, tool_results, effective_callback)

        # Add final assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=final_response,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
        )
        session.history.add_message(assistant_message)

        # Emit final chat response
        await self.event_publisher.publish_chat_response(
            message=final_response,
            has_pending_tools=False,
        )
        await self.event_publisher.publish_response_complete()

        return event_notifier.create_chat_response(final_response)
    
    def _get_send_json(self) -> Optional[UpdateCallback]:
        """Get send_json callback from event publisher if available."""
        if hasattr(self.event_publisher, 'send_json'):
            callback = self.event_publisher.send_json
            logger.debug(f"_get_send_json: event_publisher.send_json = {callback is not None}")
            return callback
        logger.warning(f"_get_send_json: event_publisher does not have send_json method. Type: {type(self.event_publisher)}")
        return None
