"""Chat orchestrator - coordinates the full chat request flow."""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.domain.errors import AuthorizationError, SessionNotFoundError
from atlas.domain.messages.models import Message, MessageRole
from atlas.hooks import HookEvent, get_hook_manager
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.sessions import SessionRepository
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.mcp_tools.atlas_server import SEARCH_TOOL_NAME, normalize_tool_name
from atlas.modules.prompts.prompt_provider import PromptProvider

from .modes.agent import AgentModeRunner
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner
from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.message_builder import MessageBuilder
from .preprocessors.prompt_override_service import PromptOverrideService
from .utilities import event_notifier, file_processor
from .utilities.search_tool_selection import with_search_tool

logger = logging.getLogger(__name__)


def _coerce_user_index(value: Any) -> Optional[int]:
    """Coerce an untrusted wire value to a user-message ordinal.

    Returns the value as an ``int`` only when it is a genuine integer; ``None``
    for anything else (str, float, list, dict, or ``bool`` -- which is an ``int``
    subclass in Python but is never a valid ordinal). Callers ignore ``None`` so
    a malformed client payload degrades to "no rewind" instead of crashing the
    chat turn or matching the wrong prompt.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


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
        config_manager: Optional[Any] = None,
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
            config_manager: Optional config manager for model capability lookups
        """
        self.llm = llm
        self.event_publisher = event_publisher
        self.session_repository = session_repository
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.file_manager = file_manager
        self.config_manager = config_manager

        # Initialize services
        self.tool_authorization = ToolAuthorizationService(
            tool_manager=tool_manager, config_manager=config_manager
        )
        self.prompt_override = PromptOverrideService(tool_manager=tool_manager)
        self.message_builder = MessageBuilder(
            prompt_provider=prompt_provider, config_manager=config_manager
        )

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

    def _model_supports_vision(self, model: str) -> bool:
        """Return True if the named model is configured with supports_vision=True."""
        if not self.config_manager:
            return False
        try:
            model_config = self.config_manager.llm_config.models.get(model)
            return bool(model_config and getattr(model_config, "supports_vision", False))
        except Exception:
            return False

    def _model_supports_pdf(self, model: str) -> bool:
        """Return True if the named model is configured with supports_pdf=True."""
        if not self.config_manager:
            return False
        try:
            model_config = self.config_manager.llm_config.models.get(model)
            return bool(model_config and getattr(model_config, "supports_pdf", False))
        except Exception:
            return False

    def _model_supports_tools(self, model: str) -> bool:
        """Return True if the named model is configured with supports_tools=True."""
        if not self.config_manager:
            return True  # Default to True for backward compat
        try:
            model_config = self.config_manager.llm_config.models.get(model)
            if not model_config:
                return True  # Unknown models default to tool-capable
            return bool(getattr(model_config, "supports_tools", True))
        except Exception:
            return True

    def _bounded_agent_steps(self, requested: Any) -> int:
        """Clamp the client-supplied step count to the configured maximum.

        The step count arrives verbatim in the WebSocket payload, and every
        step can hold server-side state (the connection, the session, MCP
        client cache entries, and -- with the built-in sleep tool -- an
        in-process wait). Unbounded, a client can pin that state for as long as
        it likes at no cost, so the server's own AGENT_MAX_STEPS is the ceiling
        rather than a default the client may exceed.
        """
        configured = 10
        settings = getattr(self.config_manager, "app_settings", None)
        if settings is not None:
            try:
                configured = int(getattr(settings, "agent_max_steps", 10) or 10)
            except (TypeError, ValueError):
                configured = 10
        configured = max(configured, 1)

        if requested is None:
            return min(10, configured)
        try:
            value = int(requested)
        except (TypeError, ValueError):
            return min(10, configured)
        if value > configured:
            logger.warning(
                "Requested agent_max_steps=%s exceeds the configured maximum of %s; clamping",
                value, configured,
            )
        return max(1, min(value, configured))

    async def _ensure_model_authorized(self, model: str, user_email: Optional[str]) -> None:
        """Reject the turn if the user may not access the requested model.

        Enforces the per-model ``groups`` access-control list. Models without a
        ``groups`` restriction (the default) are allowed for everyone, so this is
        a no-op unless an operator has opted a model into group restriction.
        Unknown models are left to the downstream caller to reject so behavior is
        unchanged when access control is not configured.
        """
        if not self.config_manager:
            return
        try:
            models = self.config_manager.llm_config.models
        except Exception:
            return
        decision = await check_model_access(models, model, user_email, context="chat")
        if decision is not ModelAccessDecision.DENIED:
            return
        raise AuthorizationError(
            "You are not authorized to use the selected model.",
            code="MODEL_ACCESS_DENIED",
        )

    async def _resolve_search_tool(
        self,
        selected_tools: Optional[List[str]],
        selected_data_sources: Optional[List[str]],
    ) -> Optional[List[str]]:
        """Add the implied ``atlas_search`` tool, or say why it is not there.

        A data source selection scopes search and makes the tool available
        (#862). When the built-in search tool is turned off but general RAG is
        not -- ``FEATURE_RAG_ENABLED=true`` with
        ``FEATURE_ATLAS_RAG_TOOLS_ENABLED=false``, a supported combination -- a
        turn that also has tools selected runs in tools mode, where nothing can
        read those sources any more. Answering anyway without the user's chosen
        evidence is exactly the silence this change set out to remove, so the
        user is told. Returns the tool list to use.
        """
        if not selected_data_sources:
            return selected_tools

        resolved = with_search_tool(
            selected_tools, selected_data_sources, self.config_manager,
        )
        if any(normalize_tool_name(t) == SEARCH_TOOL_NAME for t in resolved):
            # Either the user selected search themselves (possibly under its
            # pre-#855 name) or the sources implied it. Nothing to warn about.
            return resolved

        # No search tool in the turn. That only strands the sources when the
        # turn is going to run in tools/agent mode; with no tools at all it
        # routes to RAG mode, which reads them itself. ``config_manager`` is
        # None for programmatic callers that never had feature flags to consult,
        # so there is nothing to report to them either.
        if resolved and self.config_manager is not None:
            logger.warning(
                "Data sources selected but the built-in search tool is disabled; "
                "this turn will not search them",
            )
            await self.event_publisher.publish_warning(
                message=(
                    "**Your data sources were not searched.** The built-in "
                    "`atlas_search` tool is turned off "
                    "(`FEATURE_ATLAS_RAG_TOOLS_ENABLED`), and searching is now "
                    "something the model asks for rather than something that "
                    "happens automatically. Deselect your tools to use plain RAG "
                    "for this turn, or ask an administrator to enable the "
                    "built-in search tool."
                ),
            )
        return selected_tools

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
        agent_mode: bool = False,
        temperature: float = 0.7,
        files: Optional[Dict[str, Any]] = None,
        rewind_to_user_index: Optional[int] = None,
        steering: Optional[Any] = None,
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
            agent_mode: Whether to use agent mode
            temperature: LLM temperature
            files: Optional files to attach
            rewind_to_user_index: When set, rewind history to this user message
                (0-based ordinal) before adding the new prompt, dropping that
                prompt and everything after it (overwrite-in-place edit/resubmit)
            steering: Optional steering channel (issue #824). Forwarded only to
                agent mode, where the loop drains it at each iteration boundary
                so a user message sent mid-run reaches the LLM as a normal user
                turn without stopping the loop. Other modes ignore it.
            **kwargs: Additional parameters

        Returns:
            Response dictionary
        """
        # Get session from repository
        session = await self.session_repository.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        # Enforce per-model group access control at request time. The `model`
        # string comes straight off the client, so listing-layer filtering alone
        # is bypassable -- a crafted request must be rejected here too.
        await self._ensure_model_authorized(model, user_email)

        # Rewind/edit-and-resubmit: drop the targeted prompt and everything after
        # it so the new content takes its place in a single linear thread.
        if rewind_to_user_index is not None:
            # The index arrives straight off the WebSocket frame, so it may not be
            # a real int (a crafted/buggy client could send a string, list, bool,
            # or float). Coerce defensively: a bad value is ignored rather than
            # crashing the turn on the ``user_index < 0`` comparison or silently
            # matching the wrong prompt (``True`` would compare equal to 1).
            rewind_index = _coerce_user_index(rewind_to_user_index)
            if rewind_index is None:
                logger.warning(
                    "Ignoring rewind request with non-integer index %r",
                    rewind_to_user_index,
                )
            else:
                removed = session.history.truncate_at_user_index(rewind_index)
                if removed:
                    # This turn has genuinely shortened the thread, so the
                    # persistence layer's no-shrink guard must let its save
                    # through. Recorded here, where the truncation actually
                    # happened, rather than inferred from the request field:
                    # the branches below reach this point having removed
                    # nothing, and they must not earn the exemption.
                    session.context["rewind_removed"] = True
                    logger.info(
                        "Rewind to user message %d: removed %d message(s), "
                        "%d remaining before new prompt",
                        rewind_index,
                        len(removed),
                        len(session.history.messages),
                    )
                else:
                    # No user message at that ordinal: the new prompt will simply
                    # be appended. In normal use the frontend and backend agree on
                    # the user-message count, so this signals a FE/BE ordinal
                    # desync rather than routine activity -- surface it at WARNING.
                    logger.warning(
                        "Rewind to user message %d removed nothing (index out of "
                        "range); appending without truncation -- possible "
                        "frontend/backend ordinal desync",
                        rewind_index,
                    )

        # Add user message to history
        user_message = Message(
            role=MessageRole.USER,
            content=content,
            metadata={"model": model}
        )
        session.history.add_message(user_message)
        session.update_timestamp()

        # UserPromptSubmit hook (GH #713): fires after the user message is added,
        # before file ingestion and mode dispatch. A hook may rewrite/redact the
        # prompt text, narrow selected_tools/data_sources, disable agent_mode,
        # or block the turn with a message. Opt-in; zero overhead without
        # config/hooks.json.
        hook_mgr = get_hook_manager()
        if hook_mgr is not None and hook_mgr.has_hooks(HookEvent.USER_PROMPT_SUBMIT):
            payload = {
                "prompt": content,
                "selected_tools": list(selected_tools) if selected_tools else [],
                "selected_data_sources": list(selected_data_sources) if selected_data_sources else [],
                "agent_mode": bool(agent_mode),
            }
            outcome = await hook_mgr.run_event(
                HookEvent.USER_PROMPT_SUBMIT,
                payload,
                session_context={
                    "session_id": str(session_id),
                    "user_email": user_email,
                    "compliance_level": session.context.get("compliance_level"),
                },
            )
            if outcome.verdict == "deny":
                reason = outcome.reason or "Prompt blocked by policy hook."
                block_msg = Message(role=MessageRole.ASSISTANT, content=reason, metadata={"blocked_by_hook": True})
                session.history.add_message(block_msg)
                # Publish the reason before completing the turn: a streaming
                # client renders what it receives, so completing without a
                # message would end the turn silently and look like a hang.
                await self.event_publisher.publish_chat_response(reason)
                await self.event_publisher.publish_response_complete()
                return event_notifier.create_chat_response(reason)
            if outcome.modified:
                new_prompt = outcome.payload.get("prompt")
                if isinstance(new_prompt, str) and new_prompt:
                    content = new_prompt
                    # Reflect the (possibly redacted) prompt in the stored user message
                    user_message.content = content
                # Tools/sources may only be *narrowed*: intersect the hook's list
                # with what the user actually selected so a hook cannot grant
                # access to a tool or source the user never chose. An explicitly
                # empty list is preserved (it means "none"), not treated as
                # "unset" -- collapsing [] to None would widen the turn back to
                # the caller's full selection.
                new_tools = outcome.payload.get("selected_tools")
                if isinstance(new_tools, list):
                    allowed_tools = set(selected_tools or [])
                    selected_tools = [t for t in new_tools if t in allowed_tools]
                new_sources = outcome.payload.get("selected_data_sources")
                if isinstance(new_sources, list):
                    allowed_sources = set(selected_data_sources or [])
                    selected_data_sources = [s for s in new_sources if s in allowed_sources]
                new_agent = outcome.payload.get("agent_mode")
                if isinstance(new_agent, bool):
                    agent_mode = new_agent

        # Handle file ingestion
        update_callback = kwargs.get("update_callback")
        logger.debug(f"Orchestrator.execute: update_callback present = {update_callback is not None}")
        model_supports_vision = self._model_supports_vision(model)
        model_supports_pdf = self._model_supports_pdf(model)
        session.context = await file_processor.handle_session_files(
            session_context=session.context,
            user_email=user_email,
            files_map=files,
            file_manager=self.file_manager,
            update_callback=update_callback,
            model_supports_vision=model_supports_vision,
            model_supports_pdf=model_supports_pdf,
            event_publisher=self.event_publisher,
        )

        # Build messages with history and files manifest. A user-selected custom
        # prompt (issue #153) replaces the default system prompt for this turn.
        messages = await self.message_builder.build_messages(
            session=session,
            include_files_manifest=True,
            model_supports_vision=model_supports_vision,
            model_supports_pdf=model_supports_pdf,
            custom_system_prompt=kwargs.get("custom_system_prompt"),
        )

        # Apply MCP prompt override
        messages = await self.prompt_override.apply_prompt_override(
            messages=messages,
            selected_prompts=selected_prompts,
            user_email=user_email,
            conversation_id=session.context.get("conversation_id", str(session_id)),
        )

        # Strip tools / agent mode and warn if the model does not support tool/function calling
        if not self._model_supports_tools(model):
            warnings = []
            if selected_tools:
                logger.warning(
                    "Model %s does not support tool calling; stripping %d selected tools",
                    model,
                    len(selected_tools),
                )
                warnings.append("Your selected tools have been disabled for this request.")
                selected_tools = None
            if agent_mode:
                logger.warning(
                    "Model %s does not support tool calling; disabling agent mode",
                    model,
                )
                warnings.append("Agent mode has been disabled for this request.")
                agent_mode = False
            if warnings:
                await self.event_publisher.publish_warning(
                    message=(
                        f"**Note:** The model `{model}` does not support tool/function calling. "
                        + " ".join(warnings)
                        + " Please switch to a tool-capable model."
                    ),
                )

        # #862: retrieval is an explicit ``atlas_search`` call, and a data source
        # selection implies that tool. Resolved HERE, before the agent-mode guard
        # below -- otherwise "sources selected, no other tool" is downgraded to a
        # plain chat turn before the tool it implies exists.
        selected_tools = await self._resolve_search_tool(
            selected_tools, selected_data_sources,
        )

        # Agent mode needs at least one tool to act on. With no tools selected
        # the agentic loop has nothing to call, and tool-seeking prompts can
        # drive the model to emit a tool call the provider then rejects
        # ("tool_choice is none, but model called a tool"), which surfaces as an
        # empty/failed response. Fall back to a normal chat turn and tell the
        # user instead of failing. The frontend guards this too, but enforcing
        # it here covers API clients and older frontends.
        if agent_mode and not selected_tools:
            logger.info("Agent mode requested with no tools selected; running as a normal chat turn")
            await self.event_publisher.publish_warning(
                message=(
                    "**Agent mode needs at least one tool.** No tools were selected, "
                    "so this message ran as a normal chat. Select one or more tools to use agent mode."
                ),
            )
            agent_mode = False

        # Route to appropriate mode (always streaming)
        if agent_mode and self.agent_mode:
            return await self.agent_mode.run(
                session=session,
                model=model,
                messages=messages,
                selected_tools=selected_tools,
                selected_data_sources=selected_data_sources,
                max_steps=self._bounded_agent_steps(kwargs.get("agent_max_steps")),
                temperature=temperature,
                agent_loop_strategy=kwargs.get("agent_loop_strategy"),
                steering=steering,
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
