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

    async def cleanup(self) -> None:
        """Cleanup MCP connections."""
        if self._factory:
            mcp = self._factory.get_mcp_manager()
            await mcp.cleanup()
