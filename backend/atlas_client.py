"""Python client API for Atlas chat -- headless, non-interactive usage."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from infrastructure.events.cli_event_publisher import CLIEventPublisher

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """Structured result from a chat call."""

    message: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    files: Dict[str, Any] = field(default_factory=dict)
    canvas_content: Optional[str] = None
    session_id: Optional[UUID] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "tool_calls": self.tool_calls,
            "files": self.files,
            "canvas_content": self.canvas_content,
            "session_id": str(self.session_id) if self.session_id else None,
        }


class AtlasClient:
    """
    Headless Python client for Atlas chat.

    Wraps AppFactory + ChatService for programmatic one-shot or
    multi-turn LLM conversations with MCP tools, RAG, and agent mode.
    """

    def __init__(self) -> None:
        self._factory = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the backend (MCP discovery, etc.)."""
        if self._initialized:
            return

        from infrastructure.app_factory import AppFactory

        self._factory = AppFactory()

        mcp_manager = self._factory.get_mcp_manager()
        try:
            await mcp_manager.initialize_clients()
            await mcp_manager.discover_tools()
            await mcp_manager.discover_prompts()
        except Exception:
            logger.warning("MCP initialization failed; continuing without tools")

        # LiteLLMCaller sets litellm.set_verbose = debug_mode, which causes
        # litellm to print() debug info to stdout. Force it off for CLI use.
        import litellm as _litellm
        _litellm.set_verbose = False
        _litellm.suppress_debug_info = True

        self._initialized = True

    async def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        agent_mode: bool = False,
        selected_tools: Optional[List[str]] = None,
        selected_data_sources: Optional[List[str]] = None,
        only_rag: bool = False,
        user_email: Optional[str] = None,
        session_id: Optional[UUID] = None,
        max_steps: int = 10,
        temperature: float = 0.7,
        streaming: bool = False,
        quiet: bool = False,
    ) -> ChatResult:
        """
        Send a chat message and return the result.

        Args:
            prompt: User message text.
            model: LLM model name. Uses config default if not specified.
            agent_mode: Enable agent loop for multi-step tool use.
            selected_tools: List of tool names to enable.
            selected_data_sources: List of RAG data source names to query.
            only_rag: If True, use only RAG without tools (RAG-only mode).
            user_email: User identity for auth-filtered tools/RAG.
            session_id: Reuse an existing session for multi-turn.
            max_steps: Max agent iterations.
            temperature: LLM temperature.
            streaming: If True, stream tokens to stdout as they arrive.
            quiet: Suppress status output on stderr (only affects streaming mode).

        Returns:
            ChatResult with assistant message, tool calls, files, etc.
        """
        await self.initialize()

        if session_id is None:
            session_id = uuid4()

        if model is None:
            models = self._factory.get_config_manager().llm_config.models
            if models:
                # models is a dict of {display_name: ModelConfig}
                first_key = next(iter(models))
                model = first_key
            else:
                model = "gpt-4o"

        if user_email is None:
            cfg = self._factory.get_config_manager()
            user_email = cfg.app_settings.test_user or "cli@atlas.local"

        event_publisher = CLIEventPublisher(streaming=streaming, quiet=quiet)
        chat_service = self._factory.create_chat_service(connection=None)
        # Replace the default event publisher with our CLI one
        chat_service.event_publisher = event_publisher
        # Re-initialize mode runners with the new publisher
        chat_service.plain_mode.event_publisher = event_publisher
        chat_service.rag_mode.event_publisher = event_publisher
        chat_service.tools_mode.event_publisher = event_publisher
        chat_service.tools_mode.skip_approval = True
        chat_service.agent_mode.event_publisher = event_publisher
        chat_service.agent_mode.agent_loop_factory.skip_approval = True

        await chat_service.handle_chat_message(
            session_id=session_id,
            content=prompt,
            model=model,
            selected_tools=selected_tools,
            selected_data_sources=selected_data_sources,
            only_rag=only_rag,
            agent_mode=agent_mode,
            agent_max_steps=max_steps,
            user_email=user_email,
            temperature=temperature,
        )

        collected = event_publisher.get_result()
        return ChatResult(
            message=collected.message,
            tool_calls=collected.tool_calls,
            files=collected.files,
            canvas_content=collected.canvas_content,
            session_id=session_id,
        )

    def chat_sync(self, prompt: str, **kwargs) -> ChatResult:
        """Synchronous wrapper around chat()."""
        return asyncio.run(self.chat(prompt, **kwargs))

    async def list_data_sources(self, user_email: Optional[str] = None) -> Dict[str, Any]:
        """Discover and list available RAG data sources.

        Calls the RAG discovery mechanism to get actual available sources
        with their qualified IDs (format: server:source_id).

        Args:
            user_email: User identity for auth-filtered sources.

        Returns:
            Dict with 'servers' (config info) and 'sources' (discovered qualified IDs).
        """
        await self.initialize()
        cfg = self._factory.get_config_manager()

        # Return empty results when RAG feature is disabled
        if not cfg.app_settings.feature_rag_enabled:
            logger.info("RAG discovery skipped (FEATURE_RAG_ENABLED=false)")
            return {"servers": {}, "sources": []}

        if user_email is None:
            user_email = cfg.app_settings.test_user or "cli@atlas.local"

        # Get server config info
        servers = {}
        for name, source in cfg.rag_sources_config.sources.items():
            if source.enabled:
                servers[name] = {
                    "type": source.type,
                    "display_name": source.display_name or name,
                    "description": source.description,
                }

        discovered_sources: List[str] = []
        rag_service = self._factory.get_unified_rag_service()

        # Best-effort discovery across HTTP sources
        if rag_service:
            try:
                rag_servers = await rag_service.discover_data_sources(username=user_email)
                for server in rag_servers:
                    server_name = server.get("server")
                    for src in server.get("sources", []) or []:
                        source_id = src.get("id")
                        if server_name and source_id:
                            discovered_sources.append(f"{server_name}:{source_id}")
            except Exception as e:
                logger.warning("HTTP RAG discovery failed: %s", e)

        # Best-effort discovery across MCP RAG sources
        if rag_service and getattr(rag_service, "rag_mcp_service", None):
            try:
                mcp_sources = await rag_service.rag_mcp_service.discover_data_sources(user_email)
                if mcp_sources:
                    discovered_sources.extend(mcp_sources)
            except Exception as e:
                logger.warning("MCP RAG discovery failed: %s", e)

        # Deduplicate while preserving order
        seen = set()
        deduped: List[str] = []
        for s in discovered_sources:
            if s not in seen:
                seen.add(s)
                deduped.append(s)

        return {
            "servers": servers,
            "sources": deduped,
        }

    async def cleanup(self) -> None:
        """Cleanup MCP connections."""
        if self._factory:
            mcp = self._factory.get_mcp_manager()
            await mcp.cleanup()
