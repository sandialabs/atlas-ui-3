"""Chat orchestrator - coordinates the full chat request flow."""

import logging
from typing import Dict, Any, List, Optional
from uuid import UUID

from domain.errors import SessionNotFoundError, DomainError
from domain.messages.models import Message, MessageRole
from domain.sessions.models import Session
from interfaces.llm import LLMProtocol
from interfaces.tools import ToolManagerProtocol
from interfaces.events import EventPublisher
from interfaces.sessions import SessionRepository
from modules.prompts.prompt_provider import PromptProvider
from core.security_check import SecurityCheckService, SecurityCheckResult

from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.prompt_override_service import PromptOverrideService
from .preprocessors.message_builder import MessageBuilder
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner
from .modes.agent import AgentModeRunner
from .utilities import file_utils

logger = logging.getLogger(__name__)


class ChatOrchestrator:
    """
    Orchestrates the full chat request flow.
    
    Coordinates preprocessing, policy checks, mode selection, and execution.
    Provides clean separation between request handling and business logic.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        event_publisher: EventPublisher,
        session_repository: SessionRepository,
        tool_manager: Optional[ToolManagerProtocol] = None,
        prompt_provider: Optional[PromptProvider] = None,
        file_manager: Optional[Any] = None,
        artifact_processor: Optional[Any] = None,
        plain_mode: Optional[PlainModeRunner] = None,
        rag_mode: Optional[RagModeRunner] = None,
        tools_mode: Optional[ToolsModeRunner] = None,
        agent_mode: Optional[AgentModeRunner] = None,
        security_check_service: Optional[SecurityCheckService] = None,
    ):
        """
        Initialize chat orchestrator.
        
        Args:
            llm: LLM protocol implementation
            event_publisher: Event publisher for UI updates
            session_repository: Session storage repository
            tool_manager: Optional tool manager
            prompt_provider: Optional prompt provider
            file_manager: Optional file manager
            artifact_processor: Optional artifact processor callback
            plain_mode: Optional pre-configured plain mode runner
            rag_mode: Optional pre-configured RAG mode runner
            tools_mode: Optional pre-configured tools mode runner
            agent_mode: Optional pre-configured agent mode runner
            security_check_service: Optional security check service
        """
        self.llm = llm
        self.event_publisher = event_publisher
        self.session_repository = session_repository
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.file_manager = file_manager
        self.security_check_service = security_check_service
        
        # Initialize services
        self.tool_authorization = ToolAuthorizationService(tool_manager=tool_manager)
        self.prompt_override = PromptOverrideService(tool_manager=tool_manager)
        self.message_builder = MessageBuilder(prompt_provider=prompt_provider)
        
        # Initialize or use provided mode runners
        self.plain_mode = plain_mode or PlainModeRunner(
            llm=llm,
            event_publisher=event_publisher,
        )
        self.rag_mode = rag_mode or RagModeRunner(
            llm=llm,
            event_publisher=event_publisher,
        )
        self.tools_mode = tools_mode or ToolsModeRunner(
            llm=llm,
            tool_manager=tool_manager,
            event_publisher=event_publisher,
            prompt_provider=prompt_provider,
            artifact_processor=artifact_processor,
        )
        self.agent_mode = agent_mode
    
    async def execute(
        self,
        session_id: UUID,
        content: str,
        model: str,
        user_email: Optional[str] = None,
        selected_tools: Optional[List[str]] = None,
        selected_prompts: Optional[List[str]] = None,
        selected_data_sources: Optional[List[str]] = None,
        only_rag: bool = False,
        tool_choice_required: bool = False,
        agent_mode: bool = False,
        temperature: float = 0.7,
        files: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a chat request through the full pipeline.
        
        Args:
            session_id: Session identifier
            content: User message content
            model: LLM model to use
            user_email: Optional user email
            selected_tools: Optional list of tools
            selected_prompts: Optional list of MCP prompts
            selected_data_sources: Optional list of data sources
            only_rag: Whether to use only RAG (no tools)
            tool_choice_required: Whether tool use is required
            agent_mode: Whether to use agent mode
            temperature: LLM temperature
            files: Optional files to attach
            **kwargs: Additional parameters
            
        Returns:
            Response dictionary
        """
        # Get session from repository
        session = await self.session_repository.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")
        
        # Add user message to history
        user_message = Message(
            role=MessageRole.USER,
            content=content,
            metadata={"model": model}
        )
        session.history.add_message(user_message)
        session.update_timestamp()
        
        # Perform input security check if enabled
        if self.security_check_service:
            # Convert message history to list of dicts for API
            message_history = [
                {"role": msg.role.value, "content": msg.content}
                for msg in session.history.messages[:-1]  # Exclude current message
            ]
            
            input_check = await self.security_check_service.check_input(
                content=content,
                message_history=message_history,
                user_email=user_email
            )
            
            if input_check.is_blocked():
                # Content is blocked - return error response
                logger.warning(
                    f"User input blocked by security check for {user_email}: {input_check.message}"
                )
                
                # Send blocked notification to user
                await self.event_publisher.publish_message(
                    message_type="security_warning",
                    content={
                        "type": "input_blocked",
                        "message": input_check.message or "Your input was blocked by content security policy.",
                        "details": input_check.details
                    }
                )
                
                # Remove the blocked message from history
                session.history.messages.pop()
                
                # Return error response
                return {
                    "type": "error",
                    "error": input_check.message or "Input blocked by security policy",
                    "blocked": True
                }
            
            elif input_check.has_warnings():
                # Content has warnings - notify user but allow processing
                logger.info(
                    f"User input has warnings from security check for {user_email}: {input_check.message}"
                )
                
                await self.event_publisher.publish_message(
                    message_type="security_warning",
                    content={
                        "type": "input_warning",
                        "message": input_check.message or "Your input triggered security warnings.",
                        "details": input_check.details
                    }
                )
        
        # Handle file ingestion
        update_callback = kwargs.get("update_callback")
        session.context = await file_utils.handle_session_files(
            session_context=session.context,
            user_email=user_email,
            files_map=files,
            file_manager=self.file_manager,
            update_callback=update_callback
        )
        
        # Build messages with history and files manifest
        messages = await self.message_builder.build_messages(
            session=session,
            include_files_manifest=True
        )
        
        # Apply MCP prompt override
        messages = await self.prompt_override.apply_prompt_override(
            messages=messages,
            selected_prompts=selected_prompts
        )
        
        # Route to appropriate mode and execute
        if agent_mode and self.agent_mode:
            result = await self.agent_mode.run(
                session=session,
                model=model,
                messages=messages,
                selected_tools=selected_tools,
                selected_data_sources=selected_data_sources,
                max_steps=kwargs.get("agent_max_steps", 30),
                temperature=temperature,
                agent_loop_strategy=kwargs.get("agent_loop_strategy"),
            )
        elif selected_tools and not only_rag:
            # Apply tool authorization
            selected_tools = await self.tool_authorization.filter_authorized_tools(
                selected_tools=selected_tools,
                user_email=user_email
            )
            result = await self.tools_mode.run(
                session=session,
                model=model,
                messages=messages,
                selected_tools=selected_tools,
                selected_data_sources=selected_data_sources,
                user_email=user_email,
                tool_choice_required=tool_choice_required,
                update_callback=update_callback,
                temperature=temperature,
            )
        elif selected_data_sources:
            result = await self.rag_mode.run(
                session=session,
                model=model,
                messages=messages,
                data_sources=selected_data_sources,
                user_email=user_email,
                temperature=temperature,
            )
        else:
            result = await self.plain_mode.run(
                session=session,
                model=model,
                messages=messages,
                temperature=temperature,
            )
        
        # Perform output security check if enabled
        if self.security_check_service:
            # Extract the assistant's response content for checking
            assistant_content = self._extract_response_content(session)
            
            if assistant_content:
                # Convert message history to list of dicts for API
                message_history = [
                    {"role": msg.role.value, "content": msg.content}
                    for msg in session.history.messages[:-1]  # Exclude current response
                ]
                
                output_check = await self.security_check_service.check_output(
                    content=assistant_content,
                    message_history=message_history,
                    user_email=user_email
                )
                
                if output_check.is_blocked():
                    # Output is blocked - remove from history and return error
                    logger.warning(
                        f"LLM output blocked by security check for {user_email}: {output_check.message}"
                    )
                    
                    # Remove the blocked response from history
                    session.history.messages.pop()
                    
                    # Send blocked notification to user
                    await self.event_publisher.publish_message(
                        message_type="security_warning",
                        content={
                            "type": "output_blocked",
                            "message": output_check.message or "The response was blocked by content security policy.",
                            "details": output_check.details
                        }
                    )
                    
                    # Return error response
                    return {
                        "type": "error",
                        "error": output_check.message or "Response blocked by security policy",
                        "blocked": True
                    }
                
                elif output_check.has_warnings():
                    # Output has warnings - notify user
                    logger.info(
                        f"LLM output has warnings from security check for {user_email}: {output_check.message}"
                    )
                    
                    await self.event_publisher.publish_message(
                        message_type="security_warning",
                        content={
                            "type": "output_warning",
                            "message": output_check.message or "The response triggered security warnings.",
                            "details": output_check.details
                        }
                    )
        
        return result
    
    def _extract_response_content(self, session: Session) -> Optional[str]:
        """
        Extract the most recent assistant response content from session.
        
        Args:
            session: Chat session
            
        Returns:
            Assistant response content, or None if not found
        """
        if not session.history.messages:
            return None
        
        last_message = session.history.messages[-1]
        if last_message.role == MessageRole.ASSISTANT:
            return last_message.content
        
        return None
