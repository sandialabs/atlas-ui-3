"""Application factory for dependency injection and wiring."""

import logging
from typing import Optional

from atlas.application.chat.service import ChatService
from atlas.core.auth import is_user_in_group
from atlas.domain.rag_mcp_service import RAGMCPService
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.interfaces.transport import ChatConnectionProtocol
from atlas.modules.config import ConfigManager
from atlas.modules.file_storage import FileManager, S3StorageClient
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.mcp_tools import MCPToolManager

logger = logging.getLogger(__name__)


class AppFactory:
    """Application factory that wires dependencies (simple in-memory DI)."""

    def __init__(self) -> None:
        # Configuration
        self.config_manager = ConfigManager()

        # MCP tools manager
        self.mcp_tools = MCPToolManager()

        # Only initialize RAG services when the RAG feature flag is enabled
        if self.config_manager.app_settings.feature_rag_enabled:
            # RAG MCP service for MCP-based RAG servers (create first for dependency injection)
            self.rag_mcp_service = RAGMCPService(
                mcp_manager=self.mcp_tools,
                config_manager=self.config_manager,
                auth_check_func=is_user_in_group,
            )

            # Unified RAG service for HTTP and MCP RAG sources (configured via rag-sources.json)
            # Includes rag_mcp_service for routing MCP queries
            self.unified_rag_service = UnifiedRAGService(
                config_manager=self.config_manager,
                mcp_manager=self.mcp_tools,
                auth_check_func=is_user_in_group,
                rag_mcp_service=self.rag_mcp_service,
            )
            logger.info("RAG services initialized (FEATURE_RAG_ENABLED=true)")
        else:
            self.rag_mcp_service = None
            self.unified_rag_service = None
            logger.info("RAG services disabled (FEATURE_RAG_ENABLED=false)")

        # LLM caller with unified RAG service for RAG queries (None when RAG disabled)
        self.llm_caller = LiteLLMCaller(
            self.config_manager.llm_config,
            debug_mode=self.config_manager.app_settings.debug_mode,
            rag_service=self.unified_rag_service,
        )

        # File storage & manager
        if self.config_manager.app_settings.use_mock_s3:
            logger.info("Using MockS3StorageClient (in-process, no Docker required)")
            self.file_storage = MockS3StorageClient()
        else:
            logger.info("Using S3StorageClient (MinIO/AWS S3)")
            self.file_storage = S3StorageClient()
        self.file_manager = FileManager(self.file_storage)

        # Shared session repository for all ChatService instances
        self.session_repository = InMemorySessionRepository()

        logger.info("AppFactory initialized")

    async def initialize(self) -> None:
        """Initialize async resources (MCP clients, tool discovery) for headless use."""
        try:
            await self.mcp_tools.initialize_clients()
            await self.mcp_tools.discover_tools()
            await self.mcp_tools.discover_prompts()
            logger.info("AppFactory async initialization complete")
        except Exception as e:
            logger.warning("MCP initialization failed; continuing without tools: %s", e)

    def create_chat_service(
        self, connection: Optional[ChatConnectionProtocol] = None
    ) -> ChatService:
        return ChatService(
            llm=self.llm_caller,
            tool_manager=self.mcp_tools,
            connection=connection,
            config_manager=self.config_manager,
            file_manager=self.file_manager,
            session_repository=self.session_repository,
        )

    def create_headless_chat_service(
        self, event_publisher=None
    ) -> ChatService:
        """Create a ChatService for headless/CLI use with a custom event publisher."""
        return ChatService(
            llm=self.llm_caller,
            tool_manager=self.mcp_tools,
            connection=None,
            config_manager=self.config_manager,
            file_manager=self.file_manager,
            session_repository=self.session_repository,
            event_publisher=event_publisher,
        )

    # Accessors
    def get_config_manager(self) -> ConfigManager:  # noqa: D401
        return self.config_manager

    def get_llm_caller(self) -> LiteLLMCaller:  # noqa: D401
        return self.llm_caller

    def get_mcp_manager(self) -> MCPToolManager:  # noqa: D401
        return self.mcp_tools

    def get_rag_mcp_service(self) -> Optional[RAGMCPService]:  # noqa: D401
        return self.rag_mcp_service

    def get_unified_rag_service(self) -> Optional[UnifiedRAGService]:  # noqa: D401
        return self.unified_rag_service

    def get_file_storage(self) -> S3StorageClient:  # noqa: D401
        return self.file_storage

    def get_file_manager(self) -> FileManager:  # noqa: D401
        return self.file_manager


# Temporary global instance during migration away from singletons
app_factory = AppFactory()
