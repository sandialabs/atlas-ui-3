"""Application factory for dependency injection and wiring."""

import logging
from typing import Optional

from application.chat.service import ChatService
from interfaces.transport import ChatConnectionProtocol
from modules.config import ConfigManager
from modules.file_storage import S3StorageClient, FileManager
from modules.llm.litellm_caller import LiteLLMCaller
from modules.mcp_tools import MCPToolManager
from modules.rag import RAGClient
from domain.rag_mcp_service import RAGMCPService
from core.auth import is_user_in_group

logger = logging.getLogger(__name__)


class AppFactory:
    """Application factory that wires dependencies (simple in-memory DI)."""

    def __init__(self) -> None:
        # Configuration
        self.config_manager = ConfigManager()

        # Core modules
        self.llm_caller = LiteLLMCaller(
            self.config_manager.llm_config,
            debug_mode=self.config_manager.app_settings.debug_mode,
        )
        self.mcp_tools = MCPToolManager()
        self.rag_client = RAGClient()
        self.rag_mcp_service = RAGMCPService(
            mcp_manager=self.mcp_tools,
            config_manager=self.config_manager,
            auth_check_func=is_user_in_group,
        )

        # File storage & manager
        self.file_storage = S3StorageClient()
        self.file_manager = FileManager(self.file_storage)

        logger.info("AppFactory initialized")

    def create_chat_service(
        self, connection: Optional[ChatConnectionProtocol] = None
    ) -> ChatService:
        return ChatService(
            llm=self.llm_caller,
            tool_manager=self.mcp_tools,
            connection=connection,
            config_manager=self.config_manager,
            file_manager=self.file_manager,
        )

    # Accessors
    def get_config_manager(self) -> ConfigManager:  # noqa: D401
        return self.config_manager

    def get_llm_caller(self) -> LiteLLMCaller:  # noqa: D401
        return self.llm_caller

    def get_mcp_manager(self) -> MCPToolManager:  # noqa: D401
        return self.mcp_tools

    def get_rag_client(self) -> RAGClient:  # noqa: D401
        return self.rag_client

    def get_rag_mcp_service(self) -> RAGMCPService:  # noqa: D401
        return self.rag_mcp_service

    def get_file_storage(self) -> S3StorageClient:  # noqa: D401
        return self.file_storage

    def get_file_manager(self) -> FileManager:  # noqa: D401
        return self.file_manager


# Temporary global instance during migration away from singletons
app_factory = AppFactory()
