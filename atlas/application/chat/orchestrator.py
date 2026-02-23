"""Chat orchestrator - coordinates the full chat request flow."""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from atlas.domain.errors import SessionNotFoundError
from atlas.domain.messages.models import Message, MessageRole
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.sessions import SessionRepository
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from .modes.agent import AgentModeRunner
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner
from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.message_builder import MessageBuilder
from .preprocessors.prompt_override_service import PromptOverrideService
from .utilities import file_processor

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
        """
        self.llm = llm
        self.event_publisher = event_publisher
        self.session_repository = session_repository
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.file_manager = file_manager

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

        # Handle file ingestion
        update_callback = kwargs.get("update_callback")
        logger.debug(f"Orchestrator.execute: update_callback present = {update_callback is not None}")
        session.context = await file_processor.handle_session_files(
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

        # Route to appropriate mode (always streaming)
        if agent_mode and self.agent_mode:
            return await self.agent_mode.run(
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
            return await self.tools_mode.run_streaming(
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
            return await self.rag_mode.run_streaming(
                session=session,
                model=model,
                messages=messages,
                data_sources=selected_data_sources,
                user_email=user_email,
                temperature=temperature,
            )
        else:
            return await self.plain_mode.run_streaming(
                session=session,
                model=model,
                messages=messages,
                temperature=temperature,
                user_email=user_email,
            )
