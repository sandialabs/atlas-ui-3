"""Application factory for dependency injection and wiring."""

import logging
from typing import Optional, Union

from application.chat.service import ChatService
from interfaces.transport import ChatConnectionProtocol
from modules.config import ConfigManager
from modules.file_storage import S3StorageClient, FileManager
from modules.file_storage.mock_s3_client import MockS3StorageClient
from modules.llm.litellm_caller import LiteLLMCaller
from modules.mcp_tools import MCPToolManager
from modules.rag import RAGClient
from modules.rag.atlas_rag_client import (
    AtlasRAGClient,
    create_atlas_rag_client_from_config,
)
from domain.rag_mcp_service import RAGMCPService
from domain.unified_rag_service import UnifiedRAGService
from core.auth import is_user_in_group
from infrastructure.sessions.in_memory_repository import InMemorySessionRepository

logger = logging.getLogger(__name__)


class AppFactory:
    """Application factory that wires dependencies (simple in-memory DI)."""

    def __init__(self) -> None:
        # Configuration
        self.config_manager = ConfigManager()

        # Core modules - create RAG client first so LiteLLMCaller can use it
        self.rag_client = self._create_rag_client()
        self.llm_caller = LiteLLMCaller(
            self.config_manager.llm_config,
            debug_mode=self.config_manager.app_settings.debug_mode,
            rag_client=self.rag_client,
        )
        self.mcp_tools = MCPToolManager()
        self.rag_mcp_service = RAGMCPService(
            mcp_manager=self.mcp_tools,
            config_manager=self.config_manager,
            auth_check_func=is_user_in_group,
        )

        # Unified RAG service for HTTP and MCP RAG sources
        self.unified_rag_service = UnifiedRAGService(
            config_manager=self.config_manager,
            mcp_manager=self.mcp_tools,
            auth_check_func=is_user_in_group,
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

    def _create_rag_client(self) -> Union[RAGClient, AtlasRAGClient]:
        """Create the appropriate RAG client based on rag_provider configuration.

        Returns:
            AtlasRAGClient if rag_provider is "atlas",
            otherwise returns the standard RAGClient (for "mock" or "none").
        """
        rag_provider = self.config_manager.app_settings.rag_provider
        if rag_provider == "atlas":
            logger.info("RAG provider: atlas (ATLAS RAG API)")
            return create_atlas_rag_client_from_config(self.config_manager)
        else:
            logger.info("RAG provider: %s (using mock RAGClient)", rag_provider)
            return RAGClient()

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

    # Accessors
    def get_config_manager(self) -> ConfigManager:  # noqa: D401
        return self.config_manager

    def get_llm_caller(self) -> LiteLLMCaller:  # noqa: D401
        return self.llm_caller

    def get_mcp_manager(self) -> MCPToolManager:  # noqa: D401
        return self.mcp_tools

    def get_rag_client(self) -> Union[RAGClient, AtlasRAGClient]:  # noqa: D401
        return self.rag_client

    def get_rag_mcp_service(self) -> RAGMCPService:  # noqa: D401
        return self.rag_mcp_service

    def get_unified_rag_service(self) -> UnifiedRAGService:  # noqa: D401
        return self.unified_rag_service

    def get_file_storage(self) -> S3StorageClient:  # noqa: D401
        return self.file_storage

    def get_file_manager(self) -> FileManager:  # noqa: D401
        return self.file_manager


# Temporary global instance during migration away from singletons
app_factory = AppFactory()
