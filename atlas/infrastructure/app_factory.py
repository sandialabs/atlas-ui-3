"""Application factory for dependency injection and wiring."""

import logging
from typing import Any, Dict, Optional

from atlas.application.chat.service import ChatService
from atlas.core.auth import is_user_in_group
from atlas.domain.rag_mcp_service import RAGMCPService
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.infrastructure.sessions.factory import create_session_repository
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
        self.mcp_tools.app_factory = self

        # Only initialize general RAG services when the RAG feature flag is enabled
        if self.config_manager.app_settings.feature_rag_enabled:
            # atlas_rag pseudo-tools/MCP-backed RAG are independently gated.
            if self.config_manager.app_settings.feature_atlas_rag_tools_enabled:
                self.rag_mcp_service = RAGMCPService(
                    mcp_manager=self.mcp_tools,
                    config_manager=self.config_manager,
                    auth_check_func=is_user_in_group,
                )
            else:
                self.rag_mcp_service = None

            # Unified RAG service for HTTP and optional MCP RAG sources (configured via rag-sources.json)
            self.unified_rag_service = UnifiedRAGService(
                config_manager=self.config_manager,
                mcp_manager=self.mcp_tools,
                auth_check_func=is_user_in_group,
                rag_mcp_service=self.rag_mcp_service,
            )
            logger.info(
                "RAG services initialized (FEATURE_RAG_ENABLED=true, FEATURE_ATLAS_RAG_TOOLS_ENABLED=%s)",
                self.config_manager.app_settings.feature_atlas_rag_tools_enabled,
            )
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

        # Shared session repository for all ChatService instances. The
        # implementation is selected by SESSION_REPOSITORY_TYPE (issue #760);
        # the default "memory" store is process-local, so a multi-replica
        # deployment plugs in a distributed store through that setting to
        # remove the undocumented sticky-session requirement.
        # The shared Redis pool exists only when a feature needs it; startup
        # (``start_shared_infrastructure``) verifies it before serving.
        app_settings = self.config_manager.app_settings
        self.redis = None
        if app_settings.session_repository_type == "redis":
            from atlas.infrastructure.redis import RedisClientManager, RedisSettings

            self.redis = RedisClientManager(RedisSettings.from_app_settings(app_settings))
        self.session_repository = create_session_repository(
            app_settings.session_repository_type,
            redis=self.redis,
            session_ttl_seconds=app_settings.session_ttl_seconds,
        )
        # The execution projection shares the session pool. Binding it only
        # when Redis exists leaves a test-injected memory store in place for
        # the single-instance path.
        if self.redis is not None:
            from atlas.application.chat.runs.execution_state import (
                RedisExecutionStore,
                set_execution_store,
            )

            set_execution_store(RedisExecutionStore(self.redis))

        # Outcomes of optional startup steps (issue #992): "ok", "failed" or
        # "disabled" per feature, reported by /api/health. A failure's detail
        # stays server-side, in ``startup_errors`` -- the health endpoint is
        # unauthenticated, so it carries the outcome only.
        self.startup_outcomes: Dict[str, str] = {}
        self.startup_errors: Dict[str, str] = {}

        # Chat history persistence (feature-flagged)
        self.conversation_repository = None
        self.user_prompt_repository = None
        self.workspace_repository = None
        self._init_chat_history()

        logger.info("AppFactory initialized")

    def _init_chat_history(self) -> None:
        """Initialize chat history persistence, recording the outcome (issue #992).

        A failed initialization keeps the server up without history: the
        repositories stay None, so nothing is read or saved, but the process
        is otherwise healthy. The outcome lands in ``startup_outcomes`` for
        /api/health and the error detail in ``startup_errors``.
        """
        if not self.config_manager.app_settings.feature_chat_history_enabled:
            self.startup_outcomes["chat_history"] = "disabled"
            return
        try:
            from atlas.modules.chat_history import (
                ConversationRepository,
                PoolSettings,
                UserPromptRepository,
                WorkspaceRepository,
                get_session_factory,
                init_database,
            )
            app_settings = self.config_manager.app_settings
            init_database(
                app_settings.chat_history_db_url,
                PoolSettings.from_app_settings(app_settings),
            )
            session_factory = get_session_factory()
            self.conversation_repository = ConversationRepository(session_factory)
            self.user_prompt_repository = UserPromptRepository(session_factory)
            self.workspace_repository = WorkspaceRepository(session_factory)
            self.startup_outcomes["chat_history"] = "ok"
            logger.info("Chat history persistence initialized")
        except Exception as e:  # noqa: BLE001
            self.startup_outcomes["chat_history"] = "failed"
            self.startup_errors["chat_history"] = str(e)
            logger.error("Failed to initialize chat history: %s", e, exc_info=True)
            logger.error(
                'Chat history is ENABLED but UNAVAILABLE: conversations will not be '
                'saved or served, and /api/health reports status "degraded" until '
                "the database is fixed and the server restarted."
            )

    def chat_history_unavailable_reason(self) -> str:
        """Why the chat-history repositories are unset, for route error payloads.

        Both a disabled feature and a failed initialization leave the
        repositories None (issue #992), but the messages must differ: disabled
        is a deliberate deployment choice, while a failure means the feature is
        enabled yet its database is unusable. The underlying error stays
        server-side (``startup_errors``); routes return this message only.
        """
        if self.startup_outcomes.get("chat_history") == "failed":
            return "Chat history failed to initialize"
        return "Chat history is not enabled"

    async def initialize(self) -> None:
        """Initialize async resources (MCP clients, tool discovery) for headless use."""
        try:
            await self.mcp_tools.initialize_clients()
            await self.mcp_tools.discover_tools()
            await self.mcp_tools.discover_prompts()
            logger.info("AppFactory async initialization complete")
        except Exception as e:
            logger.warning("MCP initialization failed; continuing without tools: %s", e)

    async def start_shared_infrastructure(self) -> None:
        """Verify shared services before the app takes traffic. Raises on failure."""
        if self.redis is not None:
            await self.redis.start()

    async def stop_shared_infrastructure(self) -> None:
        """Release shared connection pools on shutdown."""
        if self.redis is not None:
            await self.redis.close()

    async def session_store_health(self) -> Dict[str, Any]:
        """Health of the session store, for ``/api/health``."""
        backend = self.config_manager.app_settings.session_repository_type
        report: Dict[str, Any] = {"backend": backend}
        if self.redis is not None:
            report.update(await self.redis.health())
            stats = getattr(self.session_repository, "stats", None)
            if callable(stats):
                report.update(stats())
        else:
            report["status"] = "ok"
        return report

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
            conversation_repository=self.conversation_repository,
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
            conversation_repository=self.conversation_repository,
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
