"""Chat service - core business logic for chat operations."""

import logging
import json
import asyncio
from typing import Any, Dict, List, Optional, Callable, Awaitable
from uuid import UUID

from domain.messages.models import (
    Message,
    MessageRole,
    MessageType,
    ToolResult
)
from domain.sessions.models import Session
from interfaces.llm import LLMProtocol, LLMResponse
from interfaces.events import EventPublisher
from interfaces.sessions import SessionRepository
from modules.config import ConfigManager
from modules.prompts.prompt_provider import PromptProvider
from interfaces.tools import ToolManagerProtocol
from interfaces.transport import ChatConnectionProtocol

# Import utilities
from .utilities import tool_utils, file_utils, notification_utils, error_utils
from .agent import AgentLoopFactory
from .agent.protocols import AgentContext, AgentEvent
from core.auth_utils import create_authorization_manager
from core.utils import sanitize_for_logging

# Import new refactored modules
from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.prompt_override_service import PromptOverrideService
from .preprocessors.message_builder import MessageBuilder, build_session_context
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner
from .modes.agent import AgentModeRunner




logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class ChatService:
    """
    Core chat service that orchestrates chat operations.
    Transport-agnostic, testable business logic.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        tool_manager: Optional[ToolManagerProtocol] = None,
        connection: Optional[ChatConnectionProtocol] = None,
        config_manager: Optional[ConfigManager] = None,
        file_manager: Optional[Any] = None,
        agent_loop_factory: Optional[AgentLoopFactory] = None,
        event_publisher: Optional[EventPublisher] = None,
        session_repository: Optional[SessionRepository] = None,
    ):
        """
        Initialize chat service with dependencies.

        Args:
            llm: LLM protocol implementation
            tool_manager: Optional tool manager
            connection: Optional connection for sending updates
            config_manager: Configuration manager
            file_manager: File manager for S3 operations
            agent_loop_factory: Factory for creating agent loops (optional)
            event_publisher: Event publisher for UI updates (optional, will create default)
            session_repository: Session storage repository (optional, will create default)
        """
        self.llm = llm
        self.tool_manager = tool_manager
        self.connection = connection
        self.config_manager = config_manager
        self.prompt_provider: Optional[PromptProvider] = (
            PromptProvider(self.config_manager) if self.config_manager else None
        )
        self.file_manager = file_manager

        # Initialize or use provided event publisher
        if event_publisher is not None:
            self.event_publisher = event_publisher
        else:
            # Create default WebSocket publisher
            from infrastructure.events.websocket_publisher import WebSocketEventPublisher
            self.event_publisher = WebSocketEventPublisher(connection=self.connection)
        
        # Initialize or use provided session repository
        if session_repository is not None:
            self.session_repository = session_repository
        else:
            # Create default in-memory repository
            from infrastructure.sessions.in_memory_repository import InMemorySessionRepository
            self.session_repository = InMemorySessionRepository()
        
        # Legacy sessions dict - deprecated, use session_repository instead
        # Kept temporarily for backward compatibility
        self.sessions: Dict[UUID, Session] = {}

        # Initialize refactored services
        self.tool_authorization = ToolAuthorizationService(tool_manager=self.tool_manager)
        self.prompt_override = PromptOverrideService(tool_manager=self.tool_manager)
        self.message_builder = MessageBuilder()

        # Initialize mode runners
        self.plain_mode = PlainModeRunner(
            llm=self.llm,
            event_publisher=self.event_publisher,
        )
        self.rag_mode = RagModeRunner(
            llm=self.llm,
            event_publisher=self.event_publisher,
        )
        self.tools_mode = ToolsModeRunner(
            llm=self.llm,
            tool_manager=self.tool_manager,
            event_publisher=self.event_publisher,
            prompt_provider=self.prompt_provider,
            artifact_processor=self._update_session_from_tool_results,
            config_manager=self.config_manager,
        )



        # Agent loop factory - create if not provided
        if agent_loop_factory is not None:
            self.agent_loop_factory = agent_loop_factory
        else:
            self.agent_loop_factory = AgentLoopFactory(
                llm=self.llm,
                tool_manager=self.tool_manager,
                prompt_provider=self.prompt_provider,
                connection=self.connection,
                config_manager=self.config_manager,
            )

        # Get default strategy from config
        self.default_agent_strategy = "think-act"
        try:
            if self.config_manager:
                config_strategy = self.config_manager.app_settings.agent_loop_strategy
                if config_strategy:
                    self.default_agent_strategy = config_strategy.lower()
        except Exception:
            # Ignore config errors - fall back to default strategy
            pass
        
        # Initialize agent mode runner (after agent_loop_factory is set)
        self.agent_mode = AgentModeRunner(
            agent_loop_factory=self.agent_loop_factory,
            event_publisher=self.event_publisher,
            artifact_processor=self._update_session_from_tool_results,
            default_strategy=self.default_agent_strategy,
        )
        
        # Initialize orchestrator
        self.orchestrator = None  # Will be initialized lazily to avoid circular dependency

    def _get_orchestrator(self):
        """Lazy initialization of orchestrator."""
        if self.orchestrator is None:
            from .orchestrator import ChatOrchestrator
            self.orchestrator = ChatOrchestrator(
                llm=self.llm,
                event_publisher=self.event_publisher,
                session_repository=self.session_repository,
                tool_manager=self.tool_manager,
                prompt_provider=self.prompt_provider,
                file_manager=self.file_manager,
                artifact_processor=self._update_session_from_tool_results,
                plain_mode=self.plain_mode,
                rag_mode=self.rag_mode,
                tools_mode=self.tools_mode,
                agent_mode=self.agent_mode,
            )
        return self.orchestrator

    async def create_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Session:
        """Create a new chat session."""
        session = Session(id=session_id, user_email=user_email)
        
        # Store in both legacy dict and new repository
        self.sessions[session_id] = session
        await self.session_repository.create(session)
        
        logger.info(f"Created session {sanitize_for_logging(str(session_id))} for user {sanitize_for_logging(user_email)}")
        return session
    
    async def handle_chat_message(
        self,
        session_id: UUID,
        content: str,
        model: str,
        selected_tools: Optional[List[str]] = None,
        selected_prompts: Optional[List[str]] = None,
        selected_data_sources: Optional[List[str]] = None,
        only_rag: bool = False,
        tool_choice_required: bool = False,
        user_email: Optional[str] = None,
        agent_mode: bool = False,
        temperature: float = 0.7,
        update_callback: Optional[UpdateCallback] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Handle incoming chat message - thin façade delegating to orchestrator.
        
        Returns:
            Response dictionary to send to client
        """
        # Log input arguments with content trimmed
        content_preview = content[:100] + "..." if len(content) > 100 else content
        sanitized_kwargs = error_utils.sanitize_kwargs_for_logging(kwargs)
        
        logger.info(
            f"handle_chat_message called - session_id: {session_id}, "
            f"content: '{content_preview}', model: {model}, "
            f"selected_tools: {selected_tools}, selected_prompts: {selected_prompts}, selected_data_sources: {selected_data_sources}, "
            f"only_rag: {only_rag}, tool_choice_required: {tool_choice_required}, "
            f"user_email: {user_email}, agent_mode: {agent_mode}, "
            f"kwargs: {sanitized_kwargs}"
        )

        # Get or create session
        session = self.sessions.get(session_id)
        if not session:
            # Try session repository
            session = await self.session_repository.get(session_id)
            if not session:
                await self.create_session(session_id, user_email)
            else:
                # Sync to legacy dict
                self.sessions[session_id] = session
        
        try:
            # Delegate to orchestrator
            orchestrator = self._get_orchestrator()
            return await orchestrator.execute(
                session_id=session_id,
                content=content,
                model=model,
                user_email=user_email,
                selected_tools=selected_tools,
                selected_prompts=selected_prompts,
                selected_data_sources=selected_data_sources,
                only_rag=only_rag,
                tool_choice_required=tool_choice_required,
                agent_mode=agent_mode,
                temperature=temperature,
                update_callback=update_callback,
                **kwargs
            )
        except Exception as e:
            return error_utils.handle_chat_message_error(e, "chat message handling")
            
    async def handle_reset_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle session reset request from frontend."""
        # End the current session
        self.end_session(session_id)

        # Create a new session
        await self.create_session(session_id, user_email)

        logger.info(f"Reset session {sanitize_for_logging(str(session_id))} for user {sanitize_for_logging(user_email)}")
        
        return {
            "type": "session_reset",
            "session_id": str(session_id),
            "message": "New session created"
        }

    async def handle_attach_file(
        self,
        session_id: UUID,
        s3_key: str,
        user_email: Optional[str] = None,
        update_callback: Optional[UpdateCallback] = None
    ) -> Dict[str, Any]:
        """Attach a file from library to the current session."""
        session = self.sessions.get(session_id)
        if not session:
            session = await self.create_session(session_id, user_email)

        # Verify the file exists and belongs to the user
        if not self.file_manager or not user_email:
            return {
                "type": "file_attach",
                "s3_key": s3_key,
                "success": False,
                "error": "File manager not available or no user email"
            }

        try:
            # Get file metadata
            file_result = await self.file_manager.s3_client.get_file(user_email, s3_key)
            if not file_result:
                return {
                    "type": "file_attach",
                    "s3_key": s3_key,
                    "success": False,
                    "error": "File not found"
                }

            filename = file_result.get("filename")
            if not filename:
                return {
                    "type": "file_attach",
                    "s3_key": s3_key,
                    "success": False,
                    "error": "Invalid file metadata"
                }

            # Add file reference directly to session context (file already exists in S3)
            session.context.setdefault("files", {})[filename] = {
                "key": s3_key,
                "content_type": file_result.get("content_type"),
                "size": file_result.get("size"),
                "source": "user",
                "last_modified": file_result.get("last_modified"),
            }

            sanitized_s3_key = s3_key.replace('\r', '').replace('\n', '')
            logger.info(f"Attached file ({sanitized_s3_key}) to session {session_id}")

            # Emit files_update to notify UI
            if update_callback:
                await file_utils.emit_files_update_from_context(
                    session_context=session.context,
                    file_manager=self.file_manager,
                    update_callback=update_callback
                )

            return {
                "type": "file_attach",
                "s3_key": s3_key,
                "filename": filename,
                "success": True,
                "message": f"File {filename} attached to session"
            }

        except Exception as e:
            logger.error(f"Failed to attach file {s3_key.replace('\n', '').replace('\r', '')} to session {session_id}: {str(e).replace('\n', '').replace('\r', '')}")
            return {
                "type": "file_attach",
                "s3_key": s3_key,
                "success": False,
                "error": str(e)
            }

    async def handle_download_file(
        self,
        session_id: UUID,
        filename: str,
        user_email: Optional[str]
    ) -> Dict[str, Any]:
        """Download a file by original filename (within session context)."""
        session = self.sessions.get(session_id)
        if not session or not self.file_manager or not user_email:
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": "Session or file manager not available"
            }
        ref = session.context.get("files", {}).get(filename)
        if not ref:
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": "File not found in session"
            }
        try:
            content_b64 = await self.file_manager.get_file_content(
                user_email=user_email,
                filename=filename,
                s3_key=ref.get("key")
            )
            if not content_b64:
                return {
                    "type": MessageType.FILE_DOWNLOAD.value,
                    "filename": filename,
                    "error": "Unable to retrieve file content"
                }
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "content_base64": content_b64
            }
        except Exception as e:
            logger.error(f"Download failed for {filename}: {e}")
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": str(e)
            }
    
    async def _update_session_from_tool_results(
        self,
        session: Session,
        tool_results: List[ToolResult],
        update_callback: Optional[UpdateCallback]
    ) -> None:
        """Persist tool artifacts, update session context, and notify UI for canvas."""
        if not tool_results:
            return

        if not self.file_manager:
            logger.info("No file_manager configured; skipping artifact ingestion")
            return

        # Build a working session context including user email
        session_context: Dict[str, Any] = build_session_context(session)

        try:
            for result in tool_results:
                # Ingest v2 artifacts and emit files_update + canvas_files (with display hints)
                session_context = await file_utils.process_tool_artifacts(
                    session_context=session_context,
                    tool_result=result,
                    file_manager=self.file_manager,
                    update_callback=update_callback
                )

            # Persist updated context back to the session
            session.context.update({k: v for k, v in session_context.items() if k != "session_id"})
        except Exception as e:
            logger.error(f"Failed to update session from tool results: {e}", exc_info=True)

    def get_session(self, session_id: UUID) -> Optional[Session]:
        """Get session by ID."""
        return self.sessions.get(session_id)
    
    def end_session(self, session_id: UUID) -> None:
        """End a session."""
        if session_id in self.sessions:
            self.sessions[session_id].active = False
            logger.info(f"Ended session {sanitize_for_logging(str(session_id))}")
