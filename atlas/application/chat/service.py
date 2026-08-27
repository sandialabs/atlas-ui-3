"""Chat service - core business logic for chat operations."""

import asyncio
import logging
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)
from uuid import UUID, uuid4

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.telemetry import hash_short, start_span
from atlas.core.user_identity import normalize_user_email
from atlas.domain.errors import AuthorizationError, DomainError
from atlas.domain.messages.models import MessageType, ToolResult
from atlas.domain.sessions.models import Session
from atlas.hooks import HookEvent, get_hook_manager
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.sessions import SessionRepository
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.interfaces.transport import ChatConnectionProtocol
from atlas.modules.config import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider

from .agent import AgentLoopFactory
from .modes.agent import AgentModeRunner
from .modes.plain import PlainModeRunner
from .modes.rag import RagModeRunner
from .modes.tools import ToolsModeRunner

# Import new refactored modules
from .policies.tool_authorization import ToolAuthorizationService
from .preprocessors.message_builder import MessageBuilder, build_session_context
from .preprocessors.prompt_override_service import PromptOverrideService

# Import utilities
from .utilities import error_handler, file_processor
from .utilities.conversation_loader import load_messages_into_history
from .utilities.interrupted_turn import close_open_turn

logger = logging.getLogger(__name__)

# Distinguishes "the client did not send this field" from "the client sent
# null"; the two mean different things for the conversation's workspace binding.
# Public so the transport layer can forward the distinction rather than
# collapsing an omitted field into an explicit null before it gets here.
UNSET = object()

# Upper bound on a client-supplied workspace id persisted in conversation metadata.
_MAX_WORKSPACE_ID_LEN = 128


# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


@runtime_checkable
class ConversationOwnerRepository(Protocol):
    """Repository capability required for conversation ownership checks."""

    def get_conversation_owner(self, conversation_id: str) -> Optional[str]:
        """Return the owner email for a conversation, if it exists."""
        ...


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
        conversation_repository: Optional[Any] = None,
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
            from atlas.infrastructure.events.websocket_publisher import WebSocketEventPublisher
            self.event_publisher = WebSocketEventPublisher(connection=self.connection)

        # Initialize or use provided session repository
        if session_repository is not None:
            self.session_repository = session_repository
        else:
            # Create default in-memory repository
            from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
            self.session_repository = InMemorySessionRepository()

        # Chat history persistence (None when feature disabled)
        self.conversation_repository = conversation_repository
        if self.conversation_repository is not None and not callable(
            getattr(self.conversation_repository, "get_conversation_owner", None)
        ):
            # Without get_conversation_owner the per-turn ownership check
            # cannot be evaluated; the runtime path now fails closed but
            # surfacing the misconfiguration here makes it findable in logs
            # before the first chat hits the rejection.
            logger.warning(
                "conversation_repository does not implement get_conversation_owner; "
                "client-supplied conversation_ids will be rejected at runtime"
            )

        # Track incognito sessions
        self._incognito_sessions: set = set()

        # Track, per session, the number of leading messages that were
        # accumulated while the session was incognito. These pre-opt-in
        # messages must never be persisted, even after the user later
        # switches the save mode to 'local'/'server'. The floor is frozen
        # once the user opts in so subsequent turns persist normally.
        self._incognito_save_floor: dict = {}
        self._save_floor_locked: set = set()

        # Initialize refactored services
        self.tool_authorization = ToolAuthorizationService(
            tool_manager=self.tool_manager, config_manager=self.config_manager
        )
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

        # Get default strategy from config. Only the native agentic loop is
        # supported; the config value is honored for backward compatibility but
        # the factory resolves all values to the agentic loop.
        self.default_agent_strategy = "agentic"
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

        # Opt-in fine-tune capture service (lazy; only built when first needed)
        self._capture_service = None

    def _get_capture_service(self):
        """Lazily build the fine-tune capture service when a config is present."""
        if self._capture_service is None and self.config_manager is not None:
            from atlas.application.chat.capture import CaptureService
            self._capture_service = CaptureService(self.config_manager)
        return self._capture_service

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
                config_manager=self.config_manager,
            )
        return self.orchestrator

    async def create_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Session:
        """Create a new chat session."""
        session = Session(id=session_id, user_email=user_email)

        # SessionStart hook (GH #713): opt-in, zero overhead when no hooks.json.
        # Fires on every session creation including restore; a hook can reject
        # the session (deny) or attach metadata to session.context (modify).
        #
        # This runs *before* session_repository.create(). Persisting first would
        # make deny a one-message speed bump: the row would already exist, so the
        # caller's next message would find it via session_repository.get() and be
        # answered normally. Nothing is written until the hook allows the session.
        mgr = get_hook_manager()
        if mgr is not None and mgr.has_hooks(HookEvent.SESSION_START):
            outcome = await mgr.run_event(
                HookEvent.SESSION_START,
                {"session_id": str(session_id)},
                session_context={"session_id": str(session_id), "user_email": user_email},
            )
            if outcome.verdict == "deny":
                logger.warning(
                    "Session %s for user %s blocked by SessionStart hook",
                    sanitize_for_logging(str(session_id)),
                    sanitize_for_logging(user_email),
                )
                raise DomainError(f"Session creation blocked by hook: {outcome.reason}")
            if outcome.modified and isinstance(outcome.payload, dict):
                session.context.update(outcome.payload)

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
        user_email: Optional[str] = None,
        agent_mode: bool = False,
        temperature: float = 0.7,
        update_callback: Optional[UpdateCallback] = None,
        steering: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Handle incoming chat message - thin façade delegating to orchestrator.

        Returns:
            Response dictionary to send to client
        """
        # Log non-sensitive metadata at INFO level for production monitoring
        logger.info(
            f"handle_chat_message called - session_id: {session_id}, "
            f"model: {model}, content_length: {len(content)}, "
            f"selected_tools: {selected_tools}, selected_prompts: {selected_prompts}, selected_data_sources: {selected_data_sources}, "
            f"only_rag: {only_rag}, "
            f"user_email: {sanitize_for_logging(user_email)}, agent_mode: {agent_mode}"
        )

        # Log sensitive content only at DEBUG level for development/testing
        if logger.isEnabledFor(logging.DEBUG):
            content_preview = content[:100] + "..." if len(content) > 100 else content
            sanitized_kwargs = error_handler.sanitize_kwargs_for_logging(kwargs)
            logger.debug(
                f"handle_chat_message content preview: '{sanitize_for_logging(content_preview)}', "
                f"kwargs: {sanitized_kwargs}"
            )

        # Get or create session
        session = await self.session_repository.get(session_id)
        if not session:
            session = await self.create_session(session_id, user_email)

        # Check incognito mode
        _incognito_sentinel = object()
        incognito = kwargs.pop("incognito", _incognito_sentinel)
        if incognito is True:
            self._incognito_sessions.add(session_id)
        elif incognito is False:
            self._incognito_sessions.discard(session_id)

        # Snapshot the save policy for this turn. On the cancellation path the
        # canceller does not await the cancelled task before calling
        # end_session(), which clears _incognito_sessions / the save floor -- so
        # by the time _commit_turn runs, re-reading that state could report a
        # torn-down incognito session as savable. Deciding here, before the turn
        # runs, makes the policy immune to that race (issue #755).
        turn_is_incognito = session_id in self._incognito_sessions
        turn_save_floor = self._incognito_save_floor.get(session_id, 0)

        # Rewind / edit-and-resubmit is the one turn that legitimately ends with
        # fewer messages than are stored: it drops the edited prompt and
        # everything after it, so the repository's no-shrink guard has to let it
        # through. Permission is *earned*, not asserted: these two markers are
        # cleared here and set later by the code that actually did the work, so
        # a rewind index that removed nothing (out of range, non-integer, or a
        # partial session) leaves the guard armed. See _turn_allows_shrink.
        session.context.pop("rewind_removed", None)
        session.context.pop("hydration_failed", None)

        # Default to session_id so MCP tool calls share a persistent session (see MCPSessionManager).
        conversation_id = kwargs.pop("conversation_id", None)
        if isinstance(conversation_id, str):
            conversation_id = conversation_id.strip()
        else:
            conversation_id = None
        if conversation_id:
            self._validate_conversation_id_owner(conversation_id, user_email)
            previous_conversation_id = session.context.get("conversation_id")
            session.context["conversation_id"] = conversation_id
            # An empty history re-attempts the load even when the session is
            # already bound to this conversation. Two cases reach here that
            # way: a brand-new conversation, where the store returns nothing
            # and the lookup costs one miss on the first turn only; and a
            # session whose earlier hydration failed on an unreadable store,
            # which would otherwise be locked out of hydration for the rest of
            # its life while its history slowly grew back toward the stored
            # count -- at which point the no-shrink guard stops refusing and
            # the partial thread replaces the real one.
            if previous_conversation_id != conversation_id or not session.history.messages:
                # This session is not already carrying the conversation the turn
                # names. Load it from the store before running the turn.
                #
                # Without this, persistence depends on the client: a WebSocket
                # gets a fresh session_id per connection, so after a reconnect
                # the server holds an empty history while the browser keeps
                # sending the old conversation_id (its React state outlives the
                # socket). The turn would then run with no context *and*
                # ``_save_conversation`` would write that two-message history
                # over the stored record -- ``save_conversation`` replaces the
                # whole message set, so a 50-turn conversation became 2.
                await self._hydrate_session_from_store(
                    session,
                    conversation_id,
                    user_email,
                    is_incognito=turn_is_incognito,
                )
        elif "conversation_id" not in session.context:
            session.context["conversation_id"] = str(session_id)

        # The active workspace (issue #829): the frontend sends the workspace
        # id whose selections are loaded for this turn so the conversation can
        # be re-bound to its workspace when it is reopened from history. The
        # id is client-supplied and purely advisory -- it is only persisted in
        # the conversation metadata, never used for authorization -- so a null
        # or stale value is harmless. Stored every turn so switching away from
        # a workspace (or starting a turn without one) records the change.
        # An omitted field is *not* an explicit null: clients that never send it
        # (the CLI, a cached bundle, a script) must not strip an existing
        # binding on their next turn, so only assign when a value was supplied.
        workspace_id = kwargs.pop("workspace_id", UNSET)
        if workspace_id is not UNSET:
            if workspace_id is None:
                # An explicit null is the client unbinding the conversation.
                session.context["workspace_id"] = None
            elif (
                isinstance(workspace_id, str)
                and workspace_id.strip()
                and len(workspace_id.strip()) <= _MAX_WORKSPACE_ID_LEN
            ):
                session.context["workspace_id"] = workspace_id.strip()
            else:
                # Malformed: a non-string, a blank string, or one past the length
                # bound. Leave any existing binding alone rather than persisting a
                # cleared one that later well-formed turns could not recover.
                logger.warning(
                    "Ignoring malformed workspace_id on a chat turn for session %s",
                    sanitize_for_logging(str(session_id)),
                )

        # Compliance levels for this turn. Two distinct values are tracked and
        # they must not be conflated:
        #
        #   session.context["compliance_level"]
        #       The *user's* level, validated from the client-supplied filter.
        #       This is the long-standing key that scopes MCP server discovery
        #       (mcp_execution), tool execution (tool_executor) and agent
        #       context. Its meaning is unchanged by this PR.
        #
        #   session.context["model_compliance_level"]
        #       The *model's* configured level, read from server-side config.
        #       This is the trusted boundary used for query-time RAG
        #       enforcement. A client cannot influence it.
        #
        # Both are set every turn so a request that changes model or filter
        # clears any stale value.
        compliance_level_raw = kwargs.pop("compliance_level", None)
        _config_manager = getattr(self, "config_manager", None)
        compliance_enabled = bool(
            _config_manager
            and getattr(
                _config_manager.app_settings,
                "feature_compliance_levels_enabled",
                False,
            ) is True
        )
        trusted_compliance_level = None
        if compliance_enabled:
            from atlas.core.compliance import get_compliance_manager
            compliance_mgr = get_compliance_manager()
            try:
                model_config = _config_manager.llm_config.models.get(model)
                configured_level = (
                    getattr(model_config, "compliance_level", None)
                    if model_config
                    else None
                )
                if isinstance(configured_level, str) and configured_level:
                    # Context is deliberately non-identifying: this warning path
                    # must not carry the model name into logs.
                    trusted_compliance_level = compliance_mgr.validate_compliance_level(
                        configured_level, context="model configuration"
                    )
            except Exception as exc:
                # The realistic failure modes are attribute/lookup errors on
                # _config_manager.llm_config.models (AttributeError, TypeError,
                # KeyError), but the catch stays broad: this guard exists so a
                # broken compliance lookup can never break a chat turn, and
                # narrowing it would reintroduce that risk for exception types
                # not foreseen here. The warning names only the exception type
                # -- never the model identifier, which compliance warning paths
                # deliberately keep out of the log stream.
                logger.warning(
                    "Could not resolve the selected model's compliance level "
                    "(%s); RAG compliance enforcement is disabled for this turn.",
                    type(exc).__name__,
                )
                trusted_compliance_level = None
            session.context["compliance_level"] = (
                compliance_mgr.validate_compliance_level(
                    compliance_level_raw, context="chat request"
                )
                if compliance_level_raw
                else None
            )
            if (
                compliance_level_raw
                and trusted_compliance_level
                and session.context["compliance_level"] != trusted_compliance_level
            ):
                # Expected on the normal path (the client filter and the model's
                # level are separate concepts), so this is not a warning.
                logger.debug(
                    "Client compliance filter differs from the model's configured "
                    "level; RAG enforcement uses the model's level"
                )
        else:
            session.context["compliance_level"] = None
        session.context["model_compliance_level"] = trusted_compliance_level

        # Opt-in fine-tune capture: when both the system flag and this user's
        # consent are on, activate a capture context for the turn so the LLM
        # caller can record full I/O. ``capture_correction`` (set by the rollback
        # flow) marks this turn as a (rejected, chosen) correction pair.
        # ``capture_consent_implied`` (set by the CLI, where the operator who
        # enabled the system flag is the consenting party) treats the system flag
        # alone as sufficient, bypassing the per-user consent record.
        capture_correction = kwargs.pop("capture_correction", None)
        capture_consent_implied = bool(kwargs.pop("capture_consent_implied", False))
        capture_ctx = None
        capture_service = None
        try:
            capture_service = self._get_capture_service()
            if capture_service and capture_service.is_enabled_for(
                user_email, require_consent=not capture_consent_implied
            ):
                capture_ctx = capture_service.build_context(
                    user_email=user_email,
                    conversation_id=session.context.get(
                        "conversation_id", str(session_id)
                    ),
                    model=model,
                    temperature=temperature,
                    correction=capture_correction
                    if isinstance(capture_correction, dict)
                    else None,
                    consent_source="system_flag"
                    if capture_consent_implied
                    else "user_optin",
                )
        except Exception as exc:  # pragma: no cover - capture must never break chat
            logger.debug("Capture setup skipped: %s", exc)
            capture_ctx = None

        turn_id = str(uuid4())
        turn_attrs = {
            "turn_id": turn_id,
            "session_id": str(session_id),
            "user_hash": hash_short(user_email),
            "prompt_hash": hash_short(content),
            "prompt_chars": len(content) if content else 0,
            "prompt_tokens": (len(content) // 4) if content else 0,
            "model": model,
            "agent_mode": bool(agent_mode),
            "only_rag": bool(only_rag),
            "selected_tools_count": len(selected_tools) if selected_tools else 0,
            "selected_prompts_count": len(selected_prompts) if selected_prompts else 0,
            "selected_data_sources_count": (
                len(selected_data_sources) if selected_data_sources else 0
            ),
        }

        # Query-time RAG enforcement engages only when a trusted level actually
        # resolved. If the feature is on but the selected model carries no
        # compliance level (or the lookup failed), enforce=False leaves the
        # pre-existing permissive behaviour intact rather than rejecting every
        # compliance-tagged source.
        compliance_token = None
        if compliance_enabled:
            from atlas.core.compliance import set_active_compliance_context
            compliance_token = set_active_compliance_context(
                trusted_compliance_level,
                enforce=bool(trusted_compliance_level),
            )

        try:
            with start_span("chat.turn", turn_attrs):
                # Delegate to orchestrator. When capture is active, run the turn
                # inside the capture context so the LLM caller records full I/O,
                # then flush the accumulated record to storage afterwards.
                orchestrator = self._get_orchestrator()
                if capture_ctx is not None:
                    from atlas.application.chat.capture import capture_turn
                    with capture_turn(capture_ctx):
                        result = await orchestrator.execute(
                            session_id=session_id,
                            content=content,
                            model=model,
                            user_email=user_email,
                            selected_tools=selected_tools,
                            selected_prompts=selected_prompts,
                            selected_data_sources=selected_data_sources,
                            only_rag=only_rag,
                            agent_mode=agent_mode,
                            temperature=temperature,
                            update_callback=update_callback,
                            steering=steering,
                            **kwargs
                        )
                    try:
                        capture_service.finish_turn(capture_ctx)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Capture flush skipped: %s", exc)
                else:
                    result = await orchestrator.execute(
                        session_id=session_id,
                        content=content,
                        model=model,
                        user_email=user_email,
                        selected_tools=selected_tools,
                        selected_prompts=selected_prompts,
                        selected_data_sources=selected_data_sources,
                        only_rag=only_rag,
                        agent_mode=agent_mode,
                        temperature=temperature,
                        update_callback=update_callback,
                        steering=steering,
                        **kwargs
                    )
            await self._commit_turn(
                session, session_id, user_email, model, update_callback,
                is_incognito=turn_is_incognito, save_floor=turn_save_floor,
                allow_shrink=self._turn_allows_shrink(session),
            )
            return result
        except asyncio.CancelledError:
            # Stop button, client disconnect (#760), or reset_session all land
            # here as a plain task cancel. CancelledError is a BaseException, so
            # without this handler the persistence block above is skipped
            # entirely and the whole interrupted turn -- user message, agent
            # narration, every completed tool call -- is lost on reload
            # (issue #755). Commit what completed, then let the cancel through.
            logger.info("Chat turn cancelled; committing completed work before unwinding")
            try:
                # Agent mode closes its own turn; plain / RAG / tools runners
                # append their assistant message only on success, so without
                # this the saved history would end on the user message and the
                # next request would be user -> user.
                close_open_turn(session.history)
                await self._commit_turn(
                    session, session_id, user_email, model, update_callback,
                    is_incognito=turn_is_incognito, save_floor=turn_save_floor,
                    allow_shrink=self._turn_allows_shrink(session),
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - defensive
                logger.error("Failed to commit cancelled turn: %s", e, exc_info=True)
            raise
        except DomainError:
            # Let domain-level errors (e.g., LLM / rate limit / validation) bubble up
            # so transport layers (WebSocket/HTTP) can handle them consistently.
            raise
        except Exception as e:
            # Fallback for unexpected errors in HTTP-style callers
            return error_handler.handle_chat_message_error(e, "chat message handling")
        finally:
            if compliance_token is not None:
                from atlas.core.compliance import reset_active_compliance_context
                reset_active_compliance_context(compliance_token)

    async def _commit_turn(
        self,
        session: Session,
        session_id: UUID,
        user_email: Optional[str],
        model: str,
        update_callback: Optional[UpdateCallback],
        is_incognito: bool,
        save_floor: int,
        allow_shrink: bool = False,
    ) -> None:
        """Persist the turn just executed and notify the client.

        Shared by the normal completion path and the cancellation path
        (issue #755) so a stopped or disconnected turn is saved exactly the way
        a completed one is.

        ``is_incognito`` / ``save_floor`` are snapshots taken before the turn
        ran rather than live lookups: a cancelled turn's cleanup can resume
        after ``end_session()`` has already discarded this session's incognito
        state, and a live lookup would then read a torn-down incognito session
        as savable.

        ``allow_shrink`` says this turn is permitted to leave the conversation
        shorter than it was (rewind / edit-and-resubmit); every other turn is
        held to the repository's no-shrink guard.
        """
        # Messages accumulated while the session was incognito must never
        # be persisted, even after the user later opts in to saving. Track
        # the high-water mark of the leading incognito messages and freeze
        # it once the user opts in so later turns persist normally.
        if is_incognito:
            if session_id not in self._save_floor_locked:
                self._incognito_save_floor[session_id] = len(session.history.messages)
        else:
            self._save_floor_locked.add(session_id)

        # Persist conversation (if not incognito and feature enabled)
        if (
            self.conversation_repository is not None
            and not is_incognito
            and user_email
        ):
            try:
                saved = self._save_conversation(
                    session,
                    user_email,
                    model,
                    start_index=save_floor,
                    allow_shrink=allow_shrink,
                )
                # Notify frontend only when persistence actually succeeded.
                # When save_conversation returns None (the TOCTOU window
                # between ownership validation and the upsert), surface
                # an error frame instead of falsely confirming the save.
                conv_id = session.context.get("conversation_id", str(session_id))
                if update_callback:
                    if saved:
                        await update_callback({
                            "type": "conversation_saved",
                            "conversation_id": conv_id,
                        })
                    else:
                        await update_callback({
                            "type": "error",
                            "message": "Conversation could not be saved",
                            "error_type": "conversation_save_rejected",
                            "conversation_id": conv_id,
                        })
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Failed to persist conversation: %s", e, exc_info=True)

    def _validate_conversation_id_owner(
        self,
        conversation_id: str,
        user_email: Optional[str],
    ) -> None:
        """Reject client-supplied conversation IDs owned by another user.

        Fails closed when a conversation repository is configured but does
        not expose ``get_conversation_owner``: a repo that cannot answer
        ownership questions cannot be trusted to enforce cross-user
        isolation, so we refuse the client-supplied id rather than letting
        it through.
        """
        if not user_email:
            logger.warning(
                "Rejected chat for conversation %s: missing authenticated user",
                sanitize_for_logging(conversation_id),
            )
            raise AuthorizationError(
                "Conversation not found or access denied",
                code="CONVERSATION_ACCESS_DENIED",
            )

        if self.conversation_repository is None:
            # No persistence layer configured at all. Without it there is
            # nothing to leak through and nothing to validate against.
            return

        owner_lookup = getattr(
            self.conversation_repository, "get_conversation_owner", None
        )
        if not callable(owner_lookup):
            logger.warning(
                "Rejected chat for conversation %s: repository lacks "
                "get_conversation_owner; refusing client-supplied id rather "
                "than allowing cross-user routing",
                sanitize_for_logging(conversation_id),
            )
            raise AuthorizationError(
                "Conversation not found or access denied",
                code="CONVERSATION_ACCESS_DENIED",
            )

        owner = owner_lookup(conversation_id)
        if owner is not None and normalize_user_email(owner) != normalize_user_email(
            user_email
        ):
            logger.warning(
                "Rejected chat for conversation %s: not owned by user %s",
                sanitize_for_logging(conversation_id),
                sanitize_for_logging(user_email),
            )
            raise AuthorizationError(
                "Conversation not found or access denied",
                code="CONVERSATION_ACCESS_DENIED",
            )

    async def handle_restore_conversation(
        self,
        session_id: UUID,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Restore a saved conversation into the current session.

        Resets the session, loads previous messages into history,
        and maps the session to the original conversation_id so
        subsequent saves update the same conversation. When a
        conversation_repository is configured, the canonical message list
        comes from the DB (not the client payload) so a tampered client
        cannot replay forged history into the LLM context and have it
        re-persisted.
        """
        if isinstance(conversation_id, str):
            conversation_id = conversation_id.strip()
        else:
            conversation_id = ""

        # Mirror handle_chat_message: refuse client-supplied conversation_ids
        # without an authenticated user, so the restore path cannot be used to
        # bypass the cross-user check the chat path enforces. Returns an
        # error frame (rather than raising) so the WebSocket receive loop
        # does not need a separate try/except — keeps the transport-layer
        # contract consistent with the not-found case below.
        if not user_email or not conversation_id:
            logger.warning(
                "Rejected restore for conversation %s: missing authenticated user "
                "or empty conversation_id",
                sanitize_for_logging(conversation_id),
            )
            return {
                "type": "error",
                "error": "Conversation not found",
                "message": "Conversation not found or access denied",
                "error_type": "authorization",
            }

        # Validate conversation ownership before restoring. When a
        # repository is configured, prefer its message list as the canonical
        # source — the client-supplied ``messages`` arg is treated as
        # display-only fallback and is NOT persisted back.
        canonical_messages = messages
        stored_workspace_id = None
        if getattr(self, "conversation_repository", None) is not None:
            conv = self.conversation_repository.get_conversation(conversation_id, user_email)
            if conv is None:
                logger.warning(
                    "Rejected restore for conversation %s: not found for user %s",
                    sanitize_for_logging(conversation_id),
                    sanitize_for_logging(user_email),
                )
                return {
                    "type": "error",
                    "error": "Conversation not found",
                    "message": "Conversation not found",
                }
            db_messages = conv.get("messages")
            if isinstance(db_messages, list):
                canonical_messages = db_messages
            conv_metadata = conv.get("metadata")
            if isinstance(conv_metadata, dict):
                stored_workspace_id = conv_metadata.get("workspace_id")

        # Reset the session
        await self.end_session(session_id)
        session = await self.create_session(session_id, user_email)

        # Store the conversation_id mapping and mark as restored
        session.context["conversation_id"] = conversation_id
        session.context["_restored"] = True
        # Carry the stored workspace binding into the fresh session (issue #829)
        # so a client that never sends `workspace_id` (the CLI, a script, an
        # older bundle) re-persists the binding on its next turn instead of
        # clearing it. Assigned unconditionally for the same reason as the
        # rehydrate path: an unbound conversation must read as unbound, never
        # inherit whatever the session was carrying.
        session.context["workspace_id"] = stored_workspace_id

        # Load previous messages into session history for LLM context. Shared
        # with the rehydrate-on-reconnect path so both produce identical
        # history; see utilities/conversation_loader.py.
        loaded = load_messages_into_history(
            session.history, canonical_messages, conversation_id
        )

        logger.info(
            "Restored conversation %s into session %s for user %s (%d messages)",
            sanitize_for_logging(conversation_id),
            sanitize_for_logging(str(session_id)),
            sanitize_for_logging(user_email),
            loaded,
        )

        return {
            "type": "conversation_restored",
            "conversation_id": conversation_id,
            "message_count": loaded,
        }

    async def handle_reset_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle session reset request from frontend.

        Generates a new conversation_id so the next conversation
        does not overwrite the previous one (session_id stays the
        same for the lifetime of the WebSocket connection).
        """
        # Capture the old conversation_id before tearing down the session
        # so we can release any MCP sessions/clients scoped to it.
        old_session = await self.session_repository.get(session_id)
        old_conv_id = old_session.context.get("conversation_id") if old_session else None

        # End the current session
        await self.end_session(session_id)

        # Release MCP sessions and per-conversation clients for the old
        # conversation. Without this, each reset orphans the
        # (user, server, old_conv_id) entries in MCPSessionManager and
        # MCPToolManager._user_clients (cache keys are per-conversation).
        if old_conv_id:
            release_sessions = getattr(self.tool_manager, "release_sessions", None)
            if release_sessions is not None:
                try:
                    await release_sessions(old_conv_id, user_email=user_email)
                except Exception as e:
                    logger.debug("Error releasing MCP sessions on reset: %s", e)

        # Create a new session with a fresh conversation_id
        session = await self.create_session(session_id, user_email)
        new_conv_id = str(uuid4())
        session.context["conversation_id"] = new_conv_id

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
        session = await self.session_repository.get(session_id)
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
                await file_processor.emit_files_update_from_context(
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
            safe_key = s3_key.replace('\n', '').replace('\r', '')
            safe_err = str(e).replace('\n', '').replace('\r', '')
            logger.error(f"Failed to attach file {safe_key} to session {session_id}: {safe_err}")
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
        session = await self.session_repository.get(session_id)
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
                session_context = await file_processor.process_tool_artifacts(
                    session_context=session_context,
                    tool_result=result,
                    file_manager=self.file_manager,
                    update_callback=update_callback
                )

            # Persist updated context back to the session
            session.context.update({k: v for k, v in session_context.items() if k != "session_id"})
        except Exception as e:
            logger.error(f"Failed to update session from tool results: {e}", exc_info=True)

    @staticmethod
    def _turn_allows_shrink(session: Session) -> bool:
        """Whether this turn may leave the stored conversation shorter.

        Only one thing earns it: a rewind / edit-and-resubmit that actually
        removed messages, which the orchestrator records on the session after
        ``truncate_at_user_index`` returns rows. Deriving the permission from
        the work done rather than from the client-supplied
        ``rewind_to_user_index`` field matters -- an out-of-range or malformed
        index is ignored downstream and truncates nothing, and granting the
        permission anyway would hand a shrink exemption to a turn that never
        earned it.

        A failed rehydration revokes it outright. The session's history is then
        partial or empty, so a truncation measured against it says nothing
        about the stored conversation, and the no-shrink guard is the only
        thing standing between that turn and the stored record.
        """
        if session.context.get("hydration_failed"):
            return False
        return bool(session.context.get("rewind_removed"))

    async def _hydrate_session_from_store(
        self,
        session: Session,
        conversation_id: str,
        user_email: Optional[str],
        is_incognito: bool,
    ) -> int:
        """Load a stored conversation into ``session`` before its turn runs.

        Makes the server the authority on what a conversation contains, rather
        than whichever fragment the client happens to be holding. Returns the
        number of messages loaded (0 when there is nothing to load).

        Called only when the session's bound conversation changed, so a
        successful load *replaces* the history rather than extending it; a
        conversation the store does not have leaves the live history alone.

        Skipped for incognito turns: those are never persisted, so there is no
        stored record they could be continuing, and pulling server-side history
        into a session the user asked not to save would be a surprise.

        A failure here is logged and swallowed. The alternative -- failing the
        turn -- would make an unreadable store a total outage, and the
        no-shrink guard in ``ConversationRepository.save_conversation`` already
        prevents an un-hydrated turn from overwriting the stored record.
        """
        if is_incognito or self.conversation_repository is None or not user_email:
            return 0

        # The caller only gets here when the session's bound conversation
        # changed, so whatever binding the session was carrying belongs to the
        # *previous* conversation. Drop it up front, before any early return:
        # an unknown conversation id (a new conversation, or a client-side
        # local_* id) used to leave the old value in place for
        # _save_conversation to stamp onto the new conversation. The stored
        # binding, when there is one, is assigned once the record is in hand.
        previous_workspace_id = session.context.get("workspace_id", UNSET)
        session.context["workspace_id"] = None

        try:
            # Off the event loop: get_conversation issues several synchronous
            # queries and JSON-decodes every message's metadata, and this runs
            # before the model call for every reconnecting client at once after
            # a restart.
            conv = await asyncio.to_thread(
                self.conversation_repository.get_conversation,
                conversation_id,
                user_email,
            )
        except Exception as e:
            # Remember the failure for this turn. The session may hold a
            # partial history (or none at all), so nothing this turn does may
            # be allowed to shorten the stored conversation -- not even a
            # rewind, whose truncation would be measured against the wrong
            # thread. See _turn_allows_shrink.
            session.context["hydration_failed"] = True
            # A transient read failure says nothing about the conversation's
            # workspace, so put back whatever the session was carrying rather
            # than persisting the null cleared above over a good binding.
            if previous_workspace_id is UNSET:
                session.context.pop("workspace_id", None)
            else:
                session.context["workspace_id"] = previous_workspace_id
            logger.error(
                "Could not load conversation %s for rehydration: %s",
                sanitize_for_logging(conversation_id),
                e,
                exc_info=True,
            )
            return 0

        if not conv:
            # A conversation_id the store has never seen: the client is naming a
            # new conversation, which is the normal first-turn case.
            return 0

        # Read the binding as soon as the record is in hand, before the message
        # guards below: a stored conversation with an empty or malformed message
        # list still has a workspace, and returning past this point with the
        # binding cleared would write that null back over the stored id.
        conv_metadata = conv.get("metadata")
        session.context["workspace_id"] = (
            conv_metadata.get("workspace_id")
            if isinstance(conv_metadata, dict)
            else None
        )

        messages = conv.get("messages")
        if not isinstance(messages, list) or not messages:
            return 0

        # Anything already in this session's history belongs to the
        # conversation it was carrying before -- the caller only reaches here
        # when the bound conversation changed. Appending would splice two
        # conversations together and then save the result under this id, so
        # replace rather than extend.
        #
        # Nothing is dropped that is not already stored: the only path that
        # gets here with a non-empty history is a client switching
        # conversations mid-session, and that session's previous turns were
        # persisted under their own id as they ran. A conversation the store
        # does not have (an unsaved id, or a client-side ``local_*`` id)
        # returned above without touching the live history.
        session.history.messages.clear()

        loaded = load_messages_into_history(
            session.history, messages, conversation_id
        )
        if loaded:
            # Mark it restored for the same reason the sidebar restore path
            # does: the title belongs to the conversation's original first
            # prompt, and regenerating it from this turn's prompt would rename
            # the conversation on every reconnect.
            session.context["_restored"] = True
            logger.info(
                "Rehydrated conversation %s into session %s for user %s "
                "(%d messages); the client did not restore it",
                sanitize_for_logging(conversation_id),
                sanitize_for_logging(str(session.id)),
                sanitize_for_logging(user_email),
                loaded,
            )
        return loaded

    def _save_conversation(
        self,
        session: Session,
        user_email: str,
        model: str,
        start_index: int = 0,
        allow_shrink: bool = False,
    ) -> bool:
        """Persist a session's conversation history to the database.

        ``start_index`` excludes leading messages that were accumulated while
        the session was incognito; only messages from that index onward are
        persisted. This prevents pre-opt-in incognito turns from being saved
        when the user later switches to a saving mode.

        ``allow_shrink`` forwards the caller's assertion that this turn may
        legitimately leave the conversation shorter than the stored copy
        (rewind / edit-and-resubmit).

        Returns True on successful upsert, False when the repository
        rejected the write — another user owns the conversation_id (the
        validator's TOCTOU window), or the write would have shrunk the
        conversation without ``allow_shrink``. Callers must not announce
        ``conversation_saved`` when this returns False, otherwise the
        client believes a turn was persisted that was not.
        """
        if not session or not session.history.messages:
            return False

        savable_messages = session.history.messages[start_index:]
        if not savable_messages:
            return False

        messages = []
        for msg in savable_messages:
            msg_dict = msg.to_dict()
            # Preserve message_type from metadata if available
            msg_dict["message_type"] = msg.metadata.get("message_type", "chat")
            messages.append(msg_dict)

        # Use stored conversation_id if set, otherwise use session_id
        conv_id = session.context.get("conversation_id", str(session.id))

        # An incognito interlude splits the thread. ``start_index`` means the
        # messages before it are unsavable, so this write is a *slice* of the
        # session, not the conversation the session is bound to -- and that
        # conversation has stored messages the slice does not contain (that is
        # what ``_restored`` records: the history came from the store). Writing
        # the slice over it would destroy the rest, and the no-shrink guard
        # would otherwise refuse every remaining turn of the session with no
        # way out. Give the resumed segment its own conversation instead. The
        # client adopts the new id from the ``conversation_saved`` frame.
        if start_index > 0 and session.context.get("_restored"):
            conv_id = str(uuid4())
            session.context["conversation_id"] = conv_id
            # The segment is a new conversation, so it gets a title from its
            # own first prompt rather than inheriting the original's.
            session.context.pop("_restored", None)
            logger.info(
                "Conversation resumed after an incognito interlude; the "
                "savable segment is stored as new conversation %s rather than "
                "replacing the conversation it branched from",
                sanitize_for_logging(conv_id),
            )

        # Only generate title for new conversations (not restored ones)
        title = None
        if not session.context.get("_restored"):
            for msg in savable_messages:
                if msg.role.value == "user" and msg.content:
                    title = msg.content[:200]
                    break

        record = self.conversation_repository.save_conversation(
            conversation_id=conv_id,
            user_email=normalize_user_email(user_email),
            title=title,
            model=model,
            messages=messages,
            metadata={
                "agent_mode": bool(session.context.get("agent_mode")),
                "workspace_id": session.context.get("workspace_id"),
            },
            allow_shrink=allow_shrink,
        )
        if record is None:
            logger.warning(
                "Conversation %s save rejected by repository (owned by another "
                "user, or the write would have shrunk it -- see the repository "
                "log for which); not emitting conversation_saved",
                sanitize_for_logging(conv_id),
            )
            return False
        return True

    async def get_session(self, session_id: UUID) -> Optional[Session]:
        """Get session by ID."""
        return await self.session_repository.get(session_id)

    async def end_session(self, session_id: UUID) -> None:
        """End a session."""
        session = await self.session_repository.get(session_id)
        if session is None:
            return
        session.active = False
        await self.session_repository.update(session)
        self._incognito_sessions.discard(session_id)
        self._incognito_save_floor.pop(session_id, None)
        self._save_floor_locked.discard(session_id)
        logger.info(f"Ended session {sanitize_for_logging(str(session_id))}")

        # SessionEnd hook (GH #713): observability/lifecycle. Cannot block (a
        # session already ended); deny is treated as continue with a log so a
        # misconfigured audit hook cannot wedge the teardown path.
        mgr = get_hook_manager()
        if mgr is not None and mgr.has_hooks(HookEvent.SESSION_END):
            try:
                await mgr.run_event(
                    HookEvent.SESSION_END,
                    {"session_id": str(session_id), "user_email": session.user_email},
                    session_context={
                        "session_id": str(session_id),
                        "user_email": session.user_email,
                        "compliance_level": session.context.get("compliance_level"),
                    },
                )
            except Exception as e:
                logger.warning("SessionEnd hook failed for %s: %s", session_id, e)
