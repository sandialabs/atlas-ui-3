"""Chat orchestrator - coordinates the full chat request flow."""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.domain.errors import AuthorizationError, SessionNotFoundError
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.hooks import HookEvent, get_hook_manager
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.sessions import SessionRepository
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.config.models import LLMConfig, lookup_model_config
from atlas.modules.config.settings import agent_mode_available, configured_agent_max_steps
from atlas.modules.llm.usage_tracking import usage_scope
from atlas.modules.mcp_tools.atlas_server import (
    SEARCH_TOOL_NAME,
    is_builtin_tool,
    normalize_tool_name,
    search_tools_enabled,
)
from atlas.modules.prompts.prompt_provider import PromptProvider

from .context.compaction_controller import ContextCompactor
from .context.turn_context import TurnContext
from .modes.agent import AgentModeRunner
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner
from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.message_builder import MessageBuilder
from .preprocessors.prompt_override_service import PromptOverrideService
from .utilities import event_notifier, file_processor
from .utilities.effective_tools import (
    has_search_tool,
    resolve_effective_tools,
)

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

        # Context tracking and compaction (issue #877).
        self.turn_context = TurnContext(
            compactor=ContextCompactor(
                llm=llm, config_manager=config_manager, event_publisher=event_publisher,
            ),
            message_builder=self.message_builder,
            tool_manager=tool_manager,
            supports_vision=self._model_supports_vision,
            supports_pdf=self._model_supports_pdf,
        )

    def _model_supports_vision(self, model: str) -> bool:
        """Return True if the named model is configured with supports_vision=True."""
        if not self.config_manager:
            return False
        try:
            model_config = lookup_model_config(self.config_manager.llm_config, model)
            return bool(model_config and getattr(model_config, "supports_vision", False))
        except Exception:
            return False

    def _model_supports_pdf(self, model: str) -> bool:
        """Return True if the named model is configured with supports_pdf=True."""
        if not self.config_manager:
            return False
        try:
            model_config = lookup_model_config(self.config_manager.llm_config, model)
            return bool(model_config and getattr(model_config, "supports_pdf", False))
        except Exception:
            return False

    def _model_supports_tools(self, model: str) -> bool:
        """Return True if the named model is configured with supports_tools=True."""
        if not self.config_manager:
            return True  # Default to True for backward compat
        try:
            model_config = lookup_model_config(self.config_manager.llm_config, model)
            if not model_config:
                return True  # Unknown models default to tool-capable
            return bool(getattr(model_config, "supports_tools", True))
        except Exception:
            return True

    def _agent_mode_available(self) -> bool:
        """Whether the deployment's agent-mode kill switch allows agent runs.

        Delegates to the shared fail-closed predicate
        (``settings.agent_mode_available``) so the WebSocket admission path
        and this downgrade path cannot drift apart (#849 review). A caller
        that genuinely runs without configuration supplies settings that opt
        in explicitly.
        """
        return agent_mode_available(getattr(self.config_manager, "app_settings", None))

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
            configured = configured_agent_max_steps(settings)
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
            models = self.config_manager.llm_config
            if not isinstance(models, LLMConfig):
                models = models.models
        except Exception:
            return
        decision = await check_model_access(models, model, user_email, context="chat")
        if decision is not ModelAccessDecision.DENIED:
            return
        raise AuthorizationError(
            "You are not authorized to use the selected model.",
            code="MODEL_ACCESS_DENIED",
        )

    def _builtin_search_flags_on(self) -> bool:
        """True when both RAG and the built-in ATLAS RAG tools are turned on.

        A turn only reaches the ``atlas_search`` schema when these flags are
        on, so a turn whose sources nothing can read is just as stranded
        under flags-off as one that never named the tool.
        """
        return search_tools_enabled(getattr(self.config_manager, "app_settings", None))

    def _search_allowed_with_outbound_tools(self, selected_tools: Optional[List[str]]) -> bool:
        """Whether the search tool may be implied next to an outbound tool.

        The model reads an internal corpus and can hand what it read to an
        MCP server in the same turn, which no per-user access control
        narrows. A deployment that does not want that can switch the
        implication off for such turns
        (``FEATURE_ATLAS_SEARCH_WITH_OUTBOUND_TOOLS=false``); the tool the
        user ticked in their own right is theirs either way.
        """
        settings = getattr(self.config_manager, "app_settings", None)
        if settings is None:
            return True
        if getattr(settings, "feature_atlas_search_with_outbound_tools", True):
            return True
        return not any(
            not is_builtin_tool(t) for t in (selected_tools or [])
        )

    def _rag_enabled(self) -> bool:
        """Whether this deployment reads data sources at all.

        The RAG runner is the route that reads the sources without the
        built-in search tool. With RAG off there is nothing to read, so a
        turn that carries sources runs as a plain turn instead of pretending
        to have looked. Without settings to consult (a programmatic caller
        that never had any) the historical behaviour stands.
        """
        settings = getattr(self.config_manager, "app_settings", None)
        if settings is None:
            return True
        return bool(getattr(settings, "feature_rag_enabled", False))

    def _sources_message(self, *, read_directly: bool) -> str:
        """What to tell the user about sources the turn could not search.

        ``read_directly`` says whether the route below reads the sources
        without the model's tools; the message must not contradict it. The
        flag named is the one that is actually off.
        """
        if not self._rag_enabled():
            return (
                "**Your data sources could not be read.** RAG is turned off in "
                "this deployment (`FEATURE_RAG_ENABLED`), so nothing could "
                "read the sources you selected. Ask an administrator to turn "
                "it on."
            )
        if not self._builtin_search_flags_on():
            if read_directly:
                return (
                    "**The built-in `atlas_search` tool is turned off.** "
                    "This deployment has `FEATURE_ATLAS_RAG_TOOLS_ENABLED` "
                    "off, so the model could not search the sources itself. "
                    "They were read directly."
                )
            return (
                "**Your data sources could not be searched.** The built-in "
                "`atlas_search` tool is turned off in this deployment "
                "(`FEATURE_ATLAS_RAG_TOOLS_ENABLED`), so the model could not "
                "read the sources you selected. Ask an administrator to turn "
                "it on."
            )
        if read_directly:
            return (
                "**The tools for this turn were not available.** "
                "Authorization removed every selected tool, so the model could "
                "not use them. The sources you selected were read without "
                "them."
            )
        # The tool has no per-user access control of its own, so the reason
        # it is missing is not the user's permissions: a policy hook may have
        # taken it away. Keep the wording neutral.
        return (
            "**Your data sources could not be searched.** The built-in "
            "`atlas_search` tool is not available to this conversation, so the "
            "model could not read the sources you selected."
        )

    async def _warn_if_sources_unsearchable(
        self,
        selected_tools: Optional[List[str]],
        selected_data_sources: Optional[List[str]],
        sources_auto: bool = False,
    ) -> None:
        """Warn when a tools/agent turn carries sources nothing in it can read.

        Called from the two tool-running branches with that branch's final
        tool list -- in the tools branch that is *after* authorization
        filtering, since the ACL can strip ``atlas_search`` and would
        otherwise strand the sources silently; the agent path does no
        authorization filtering today, so its selection is already final.
        RAG-mode turns (no tools, ``only_rag``) read the sources themselves
        and are never routed here.

        Since #1102 a selection of data sources implies the built-in search
        tool, so the ordinary case -- sources selected, search available --
        is silent: the tool is offered to the model and the model decides
        whether to call it. What is left is the case where the search tool
        genuinely cannot run: the deployment has it switched off
        (``FEATURE_ATLAS_RAG_TOOLS_ENABLED`` or ``FEATURE_RAG_ENABLED``), or
        authorization stripped it. Then the turn really does carry sources
        nothing reads, and the user is told so in one line.

        ``sources_auto`` marks sources the client expanded on its own (RAG
        toggle on, none picked): those were never deliberately chosen, so
        they stay silent. ``config_manager`` is None for programmatic
        callers that never had feature flags to consult, so there is nothing
        to report to them either.
        """
        if not selected_data_sources or sources_auto:
            return
        if self.config_manager is None:
            return
        if has_search_tool(selected_tools) and self._builtin_search_flags_on():
            # The turn names the search tool (possibly under its pre-#855
            # name from a saved conversation) and the flags let it through
            # to the schema, so its sources are read.
            return
        logger.warning(
            "Data sources selected but the built-in search tool is not "
            "available to this turn",
        )
        # The tools branch does not read the sources itself, so the message
        # must not claim they were.
        await self.event_publisher.publish_warning(
            message=self._sources_message(read_directly=False),
        )

    async def execute(
        self,
        session_id: UUID,
        content: str,
        model: str,
        user_email: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute a chat request, then report the context it leaves behind.

        Every LLM call of the turn runs inside a usage scope, so the
        provider-reported token counts reach the ``context_usage`` frame sent
        once the turn completes (issue #877). See :meth:`_execute_turn` for the
        arguments.
        """
        turn_state: Dict[str, Any] = {}
        with usage_scope() as sink:
            result = await self._execute_turn(
                session_id, content, model, user_email=user_email,
                _turn_state=turn_state, **kwargs,
            )
        session = turn_state.get("session")
        if session is not None:
            await self.turn_context.report_usage(
                session, model, user_email,
                turn_state.get("tools"),
                kwargs.get("auto_compact"),
                kwargs.get("custom_system_prompt"),
                sink=sink,
            )
        return result

    async def _execute_turn(
        self,
        session_id: UUID,
        content: str,
        model: str,
        user_email: Optional[str] = None,
        selected_tools: Optional[List[str]] = None,
        selected_prompts: Optional[List[str]] = None,
        selected_data_sources: Optional[List[str]] = None,
        only_rag: bool = False,
        data_sources_auto: bool = False,
        agent_mode: bool = False,
        temperature: float = 0.7,
        files: Optional[Dict[str, Any]] = None,
        rewind_to_user_index: Optional[int] = None,
        steering: Optional[Any] = None,
        session: Optional[Session] = None,
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
            data_sources_auto: True when the client expanded the source list
                on its own (RAG toggle on, none hand-picked); such sources
                were never deliberately chosen, so a stranded-sources warning
                would fire on every turn and is suppressed
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
            session: The caller's session object. ChatService passes the one
                it prepared for this turn, so the turn mutates exactly that
                object whatever the repository returns on a fresh ``get``
                (a shared store returns copies; issue #1091). Looked up by
                ``session_id`` when omitted.
            **kwargs: Additional parameters. Context compaction (issue #877)
                reads ``compact_only`` (compact between turns instead of
                running one; ``content`` is ignored and no user turn is
                added), ``compact_focus`` (the user's focus for it) and
                ``auto_compact`` (the conversation's auto toggle; a non-bool
                means the configured default).

        Returns:
            Response dictionary
        """
        if session is None or session.id != session_id:
            session = await self.session_repository.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        # Enforce per-model group access control at request time. The `model`
        # string comes straight off the client, so listing-layer filtering alone
        # is bypassable -- a crafted request must be rejected here too.
        await self._ensure_model_authorized(model, user_email)

        turn_state = kwargs.pop("_turn_state", None)
        if turn_state is None:
            turn_state = {}
        turn_state["session"] = session
        auto_compact = kwargs.get("auto_compact")

        # Manual compaction (issue #877) runs after every check a turn gets --
        # the caller's classification and ownership checks, and the model
        # authorization just above -- and summarizes with that same model, so
        # the checkpoint never reaches a model the conversation is not cleared
        # for. It adds no user message and waits for the user's next turn.
        if kwargs.get("compact_only"):
            outcome = await self.turn_context.compact_manually(
                session, model, user_email,
                kwargs.get("compact_focus"), kwargs.get("custom_system_prompt"),
            )
            await self.event_publisher.publish_response_complete()
            return outcome

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

        # #1102: a selection of data sources implies the built-in search
        # tool. The user does not have to know that "pick a source" and
        # "tick the search tool" are two separate mechanisms: the tool is
        # offered to the model, and the model decides whether to call it.
        # There is no opt-out and nothing persisted -- the implication is a
        # property of the turn, and deselecting every source removes it
        # again. A data source selection also stays the ceiling on what that
        # tool may read; nothing here widens it.
        #
        # Resolved *before* the UserPromptSubmit hook runs, so the hook sees
        # the tools the turn would really offer and can narrow them -- the
        # implied search tool included. A model that cannot call tools gets
        # no implied tool at all: its tools are stripped, so the turn has
        # none and keeps the sources it had.
        #
        # Sources the client expanded on its own (the RAG toggle, or the
        # search tool ticked on its own) do not imply the tool: the user
        # picked nothing, so nothing of theirs is at stake. A search tool
        # the user ticked in their own right is in the selection either way.
        model_supports_tools = self._model_supports_tools(model)
        # Whether the *user* picked the search tool, as opposed to the
        # sources implying it. The hook may take the sources away, and the
        # implied tool goes with them -- but a tool the user ticked is theirs.
        user_selected_search = has_search_tool(selected_tools)
        effective_tools = resolve_effective_tools(
            selected_tools,
            selected_data_sources,
            getattr(self.config_manager, "app_settings", None),
            imply=model_supports_tools
            and not data_sources_auto
            and not only_rag
            and self._search_allowed_with_outbound_tools(selected_tools),
        )

        # UserPromptSubmit hook (GH #713): fires after the user message is added,
        # before file ingestion and mode dispatch. A hook may rewrite/redact the
        # prompt text, narrow selected_tools/data_sources, disable agent_mode,
        # or block the turn with a message. Opt-in; zero overhead without
        # config/hooks.json.
        hook_mgr = get_hook_manager()
        if hook_mgr is not None and hook_mgr.has_hooks(HookEvent.USER_PROMPT_SUBMIT):
            payload = {
                "prompt": content,
                # The effective list, not the raw selection: the hook decides
                # what the turn may offer, so it must see the implied search
                # tool to be able to strip it (#1102).
                "selected_tools": list(effective_tools),
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
                # with what the turn would really offer so a hook cannot grant
                # access to a tool or source the user never chose. An explicitly
                # empty list is preserved (it means "none"), not treated as
                # "unset" -- collapsing [] to None would widen the turn back to
                # the caller's full selection.
                new_tools = outcome.payload.get("selected_tools")
                if isinstance(new_tools, list):
                    allowed_tools = set(effective_tools)
                    effective_tools = [t for t in new_tools if t in allowed_tools]
                new_sources = outcome.payload.get("selected_data_sources")
                if isinstance(new_sources, list):
                    allowed_sources = set(selected_data_sources or [])
                    selected_data_sources = [s for s in new_sources if s in allowed_sources]
                    if allowed_sources and not selected_data_sources:
                        # The hook took every source away, so the search tool
                        # the sources implied goes with them -- unless the
                        # user ticked it in its own right, which is theirs.
                        if not user_selected_search:
                            effective_tools = [
                                t for t in effective_tools
                                if normalize_tool_name(t) != SEARCH_TOOL_NAME
                            ]
                new_agent = outcome.payload.get("agent_mode")
                if isinstance(new_agent, bool):
                    agent_mode = new_agent

        if not self._builtin_search_flags_on():
            # The schema builder drops the search tool under flags-off, so it
            # must not count as a tool the turn can run: an agent turn whose
            # only tool was the search tool would otherwise run with an empty
            # schema, and a model that still calls a tool gets a provider
            # rejection for it.
            effective_tools = [
                t for t in effective_tools
                if normalize_tool_name(t) != SEARCH_TOOL_NAME
            ]

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

        # Automatic compaction before the turn (issue #877): the user message
        # is in history, so the estimate is of the request this turn would
        # really send. A checkpoint written here is applied by the build
        # below; a failure leaves history untouched and the turn runs as is.
        turn_state["tools"] = list(effective_tools) if model_supports_tools else []
        await self.turn_context.compact_before_turn(
            session, model, user_email, turn_state["tools"], auto_compact,
            kwargs.get("custom_system_prompt"),
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
        if not model_supports_tools:
            warnings = []
            if effective_tools:
                logger.warning(
                    "Model %s does not support tool calling; stripping %d selected tools",
                    model,
                    len(effective_tools),
                )
                warnings.append("Your selected tools have been disabled for this request.")
                selected_tools = None
                effective_tools = []
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

        # Admin kill switch (issue #849 review): the flag can still arrive on the
        # wire from a client with a stale config cache, so the server refuses
        # agent mode itself when the deployment has the feature disabled --
        # client-side gating alone would let one cached turn through.
        if agent_mode and not self._agent_mode_available():
            logger.info("Agent mode is disabled by configuration; running as a normal chat turn")
            await self.event_publisher.publish_warning(
                message=(
                    "**Agent mode is disabled.** This deployment has agent mode turned off, "
                    "so this message ran without it."
                ),
            )
            agent_mode = False

        # Agent mode needs at least one tool to act on. With no tools selected
        # the agentic loop has nothing to call, and tool-seeking prompts can
        # drive the model to emit a tool call the provider then rejects
        # ("tool_choice is none, but model called a tool"), which surfaces as an
        # empty/failed response. Downgrade to a normal turn and tell the user
        # instead of failing. The frontend shows a warning while composing but
        # lets the send through, so enforcing it here is what actually keeps
        # the turn working (it also covers API clients and older frontends).
        #
        # The *effective* tool list is what counts: selected data sources
        # imply the built-in search tool, so such a turn does have something
        # to call and does not need downgrading.
        if agent_mode and not effective_tools:
            logger.info("Agent mode requested with no tools selected; running as a normal chat turn")
            if user_selected_search and not self._builtin_search_flags_on():
                # The user did select a tool -- the search one -- but the
                # deployment has it switched off, so there is nothing to call.
                # Say that, rather than claiming nothing was selected.
                message = (
                    "**Agent mode could not use the selected tool.** The "
                    "built-in `atlas_search` tool is turned off in this "
                    "deployment (`FEATURE_ATLAS_RAG_TOOLS_ENABLED`), so this "
                    "message ran without agent mode."
                )
            else:
                message = (
                    "**Agent mode needs at least one tool.** No tools were selected, "
                    "so this message ran without agent mode. Select one or more tools to use agent mode."
                )
            await self.event_publisher.publish_warning(message=message)
            agent_mode = False

        # Route to appropriate mode (always streaming)
        if agent_mode and self.agent_mode:
            await self._warn_if_sources_unsearchable(
                effective_tools, selected_data_sources, sources_auto=data_sources_auto,
            )
            return await self.agent_mode.run(
                session=session,
                model=model,
                messages=messages,
                selected_tools=effective_tools,
                selected_data_sources=selected_data_sources,
                max_steps=self._bounded_agent_steps(kwargs.get("agent_max_steps")),
                temperature=temperature,
                agent_loop_strategy=kwargs.get("agent_loop_strategy"),
                steering=steering,
                compaction=self.turn_context.run_guard(session, model, user_email, auto_compact),
            )

        # The tools branch applies tool authorization first, so the routing
        # below sees the tools the turn may really run. Authorization can
        # take every one of them -- the built-in search tool goes when the
        # deployment has it switched off -- and a turn that carries sources
        # then falls through to the route that reads them rather than
        # dropping them.
        authorized_tools: List[str] = []
        # Whether the fallback below already told the user what happened, so
        # the route that reads the sources does not say it a second time.
        sources_warning_published = False
        if effective_tools and not only_rag:
            authorized_tools = await self.tool_authorization.filter_authorized_tools(
                selected_tools=effective_tools,
                user_email=user_email
            )
            if not authorized_tools:
                logger.info(
                    "Tool authorization removed every selected tool; running "
                    "the turn without tools"
                )
                if (
                    selected_data_sources
                    and not data_sources_auto
                    and self._rag_enabled()
                ):
                    # The turn carries sources the user picked and has no
                    # tool to read them with. It falls through to the route
                    # that reads them, but the user should know the tools
                    # were taken away rather than finding out from the
                    # answer. (With RAG off nothing reads the sources either
                    # way, and the plain route below says so.)
                    await self.event_publisher.publish_warning(
                        message=self._sources_message(read_directly=True),
                    )
                    sources_warning_published = True

        turn_state["tools"] = authorized_tools
        if authorized_tools:
            # After filtering: authorization can strip ``atlas_search`` (the
            # deployment has the search tool switched off), which would
            # strand the sources silently if the check ran any earlier.
            await self._warn_if_sources_unsearchable(
                authorized_tools, selected_data_sources, sources_auto=data_sources_auto,
            )
            return await self.tools_mode.run_streaming(
                session=session,
                model=model,
                messages=messages,
                selected_tools=authorized_tools,
                selected_data_sources=selected_data_sources,
                user_email=user_email,
                update_callback=update_callback,
                temperature=temperature,
            )
        elif selected_data_sources and self._rag_enabled():
            # Reached when the built-in search tool cannot run (the RAG tools
            # feature is off, or authorization took every tool), or when the
            # caller explicitly asked for a RAG-only turn (``only_rag``).
            # With the search tool available, a turn that carries sources has
            # it in its tool list above and runs as a tools turn instead.
            if (
                not sources_warning_published
                and user_selected_search
                and not self._builtin_search_flags_on()
            ):
                # The user asked for the search tool and the deployment has
                # it switched off: the sources are read directly instead, and
                # the user is told that is what happened.
                await self.event_publisher.publish_warning(
                    message=self._sources_message(read_directly=True),
                )
            return await self.rag_mode.run_streaming(
                session=session,
                model=model,
                messages=messages,
                data_sources=selected_data_sources,
                user_email=user_email,
                temperature=temperature,
            )
        else:
            if selected_data_sources:
                # The turn carries sources and nothing can read them: RAG is
                # switched off in this deployment. Say so rather than
                # answering as if the sources had been consulted.
                logger.warning(
                    "Data sources selected but RAG is turned off; this turn "
                    "will not read them",
                )
                await self.event_publisher.publish_warning(
                    message=self._sources_message(read_directly=False),
                )
            return await self.plain_mode.run_streaming(
                session=session,
                model=model,
                messages=messages,
                temperature=temperature,
                user_email=user_email,
            )
