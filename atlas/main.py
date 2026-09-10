"""
Basic chat backend implementing the modular architecture.
Focuses on essential chat functionality only.
"""

# Suppress LiteLLM verbose logging BEFORE any transitive import of litellm.
# litellm._logging reads LITELLM_LOG at import time and defaults to DEBUG.
# This must happen before any other imports that might load litellm.
import os
from pathlib import Path as _Path

from dotenv import dotenv_values as _dotenv_values

# Load .env values without setting them in os.environ yet (just to read feature flag)
_env_path = _Path(__file__).parent.parent / ".env"
_env_values = _dotenv_values(_env_path) if _env_path.exists() else {}

# Check feature flag: FEATURE_SUPPRESS_LITELLM_LOGGING (default: true)
_suppress_litellm = _env_values.get("FEATURE_SUPPRESS_LITELLM_LOGGING", "true").lower() in ("true", "1", "yes")

if _suppress_litellm and "LITELLM_LOG" not in os.environ:
    os.environ["LITELLM_LOG"] = "ERROR"

# Clean up temporary imports
del _Path, _dotenv_values, _env_path, _env_values, _suppress_litellm

# Standard imports follow - must come after LiteLLM logging suppression above
# ruff: noqa: E402
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from atlas.application.chat.agent.steering import SteeringChannel, should_steer
from atlas.application.chat.runs import (
    ConcurrencyLimitError,
    ConversationBusyError,
    RunRegistryError,
    RunStatus,
    get_run_registry,
)
from atlas.application.chat.runs.context import set_current_run
from atlas.application.chat.runs.eligibility import turn_is_eligible_for_background_run
from atlas.application.chat.service import UNSET
from atlas.core.auth import resolve_user_from_auth_header_async
from atlas.core.domain_whitelist_middleware import DomainWhitelistMiddleware
from atlas.core.log_sanitizer import sanitize_for_logging, summarize_tool_approval_response_for_logging
from atlas.core.metrics_logger import log_metric

# Import from atlas.core (only essential middleware and config)
from atlas.core.middleware import AuthMiddleware
from atlas.core.otel_config import setup_opentelemetry
from atlas.core.rate_limit_middleware import RateLimitMiddleware
from atlas.core.security_headers_middleware import SecurityHeadersMiddleware
from atlas.core.session_middleware import SessionMiddleware
from atlas.core.websocket_origin import origin_is_allowed, parse_allowed_hosts

# Import domain errors
from atlas.domain.errors import (
    AuthorizationError,
    ContextWindowExceededError,
    DomainError,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMMalformedToolCallError,
    LLMTimeoutError,
    RateLimitError,
    ValidationError,
)

# Import from atlas.infrastructure
from atlas.infrastructure.app_factory import app_factory
from atlas.infrastructure.transport.websocket_connection_adapter import WebSocketConnectionAdapter
from atlas.routes.admin_routes import admin_router
from atlas.routes.agent_portal_availability import load_agent_portal_router

# Import essential routes
from atlas.routes.capture_routes import capture_router
from atlas.routes.config_routes import router as config_router
from atlas.routes.conversation_routes import router as conversation_router
from atlas.routes.feedback_routes import feedback_router
from atlas.routes.files_routes import (
    find_oversized_inline_file,
    get_file_upload_limit_config,
    mcp_files_router,
    router as files_router,
)
from atlas.routes.globus_auth_routes import api_router as globus_api_router
from atlas.routes.globus_auth_routes import browser_router as globus_browser_router
from atlas.routes.health_routes import router as health_router
from atlas.routes.llm_auth_routes import router as llm_auth_router
from atlas.routes.persona_routes import router as persona_router
from atlas.routes.mcp_auth_routes import router as mcp_auth_router
from atlas.routes.oidc_auth_routes import api_router as oidc_api_router
from atlas.routes.oidc_auth_routes import browser_router as oidc_browser_router
from atlas.routes.suggestion_routes import suggestion_router
from atlas.routes.telemetry_routes import telemetry_router
from atlas.routes.user_prompt_routes import router as user_prompt_router
from atlas.routes.workspace_routes import router as workspace_router
from atlas.version import VERSION

# Load environment variables from the parent directory
load_dotenv(dotenv_path="../.env")

# Setup OpenTelemetry logging
otel_config = setup_opentelemetry("atlas-ui-3-backend", "1.0.0")

logger = logging.getLogger(__name__)


async def websocket_update_callback(websocket: WebSocket, message: dict):
    """
    Callback function to handle websocket updates with logging.

    Drops the message if the socket is no longer connected.  Producers deep in
    the chat pipeline (file ingest, canvas updates, tool notifications) call
    this on every update; without the guard a client that disconnects mid-turn
    turns each one into a raised WebSocketDisconnect that either spams the log
    with tracebacks or aborts an in-progress operation partway through.
    """
    if websocket.client_state != WebSocketState.CONNECTED:
        logger.debug("Dropping %s update; websocket not connected", message.get("type"))
        return
    try:
        mtype = message.get("type")
        if mtype == "intermediate_update":
            utype = message.get("update_type") or message.get("data", {}).get("update_type")
            # Handle specific update types (canvas_files, files_update)
            # Logging disabled for these message types - see git history if needed
            if utype in ("canvas_files", "files_update"):
                pass
        elif mtype == "canvas_content":
            content = message.get("content")
            clen = len(content) if isinstance(content, str) else "obj"
            logger.debug("WS SEND: canvas_content length=%s", clen)
        else:
            logger.debug("WS SEND: %s", mtype)
    except Exception:
        # Non-fatal logging error; continue to send
        pass
    try:
        await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError) as e:
        # The socket closed between the state check above and the send (or the
        # server already sent its close frame).  Nothing to deliver -- the
        # disconnect handler owns cleanup.
        logger.debug("Websocket closed before update could be sent: %s", e)


# Events that mean "the tool this run was paused on has settled". Used to clear
# a stale waiting_for_input status; see the run update callback.
_TOOL_SETTLED_EVENTS = frozenset(
    {"tool_complete", "tool_error", "tool_interrupted", "tool_result"}
)


def tag_run_event(message: dict, run_id: str, conversation_id: str) -> dict:
    """Stamp an outbound event with the run that produced it (issue #884).

    With several conversations executing at once, a bare event is ambiguous:
    the client cannot tell whether a token, tool row, file, or completion
    belongs to the conversation on screen or to one running in the background.
    Every event a tracked run emits carries both ids so the client can route it
    -- and, crucially, so it can *drop* events for a conversation it is not
    displaying instead of splicing them into the visible transcript.

    Existing ids are never overwritten: a producer deeper in the pipeline that
    already knows its own conversation is more authoritative than the run
    envelope.
    """
    if not isinstance(message, dict):
        return message
    tagged = dict(message)
    tagged.setdefault("run_id", run_id)
    tagged.setdefault("conversation_id", conversation_id)
    return tagged


async def _release_finished_run(
    chat_service,
    run_registry,
    run_id,
    session_id,
    conversation_id,
    user_email,
):
    """Free what a finished run owned (issue #884).

    Every tracked run gets its own ``Session``, so without this each completed
    run leaves an entry behind in the process-wide session repository -- a slow
    leak that a long-lived server would never recover from. The conversation's
    MCP sessions go too, but only once no *other* run still owns that
    conversation: the whole point of the earlier changes is that navigation and
    disconnect no longer tear those down while work is in flight, and this must
    not reintroduce that by the back door.

    Never raises: this runs in the cleanup path of a task that may already be
    unwinding from a cancellation, and a failure to tidy up must not replace
    the outcome the user is waiting to see.
    """
    try:
        if session_id is not None:
            await chat_service.end_session(session_id)
            # end_session only marks the session inactive. For a connection's
            # session that is right -- it is reused for the life of the socket
            # -- but a run's session is single-use: every run gets a fresh id,
            # so nothing can ever reach this one again and leaving the record
            # behind grows the repository by one entry per run, forever.
            delete = getattr(chat_service.session_repository, "delete", None)
            if delete is not None:
                await delete(session_id)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Error ending session for run %s: %s", run_id, e)

    try:
        if conversation_id and run_registry.active_for_conversation(
            conversation_id, user_email
        ) is None:
            from atlas.modules.mcp_tools import mcp_tool_manager
            await mcp_tool_manager.release_sessions(conversation_id, user_email=user_email)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Error releasing MCP sessions for run %s: %s", run_id, e)


def _cancel_addressed_run(run_registry, user_email: str, data: dict) -> bool:
    """Cancel the run a stop frame names, if it names one.

    Resolution order is ``run_id`` first, then ``conversation_id``: the id is
    unambiguous, while the conversation is what an older or simpler client
    knows. Both go through the registry's ownership check, so a stop frame can
    only ever reach the sender's own run.

    Returns whether the frame *addressed a tracked run at all* -- not whether
    the cancel took effect. The caller uses this to decide whether to fall back
    to cancelling the connection's untracked task, and those are different
    questions: a stale stop frame naming a run that already finished addressed
    a run, and must not be allowed to cancel an unrelated untracked turn that
    happens to be in flight on this socket.
    """
    addressed_run_id = data.get("run_id")
    record = run_registry.get_for_user(addressed_run_id, user_email)
    if record is None:
        record = run_registry.active_for_conversation(
            data.get("conversation_id"), user_email
        )
    if record is None:
        # A frame naming a run id we cannot resolve still addressed a run (it
        # is terminal, reaped, or another user's). Treat it as addressed so the
        # untracked fallback stays reserved for clients that name nothing.
        return bool(addressed_run_id)
    logger.info("Cancelling run %s on user request", record.run_id)
    run_registry.cancel(record.run_id, user_email)
    return True


def _download_session_candidates(run_registry, session_id, user_email: str, data: dict):
    """Sessions to search for a downloadable file, most likely first.

    The connection session comes first. After it come the sessions of this
    user's runs -- the run the frame names, if it names one, then the rest --
    because a file a background run produced is registered on the run's own
    session. Every candidate is a run owned by ``user_email`` (the registry
    lookups are ownership-checked) and ``handle_download_file`` re-checks the
    user against the file itself, so widening the search does not widen access.
    """
    candidates = [session_id]

    def _add(record):
        if record is not None and record.session_id not in candidates:
            candidates.append(record.session_id)

    _add(run_registry.get_for_user(data.get("run_id"), user_email))
    _add(run_registry.active_for_conversation(data.get("conversation_id"), user_email))
    for record in run_registry.records_for_user(user_email or ""):
        _add(record)
    return candidates


async def _seed_run_session_files(
    chat_service, connection_session_id, run_session_id, user_email: str
) -> None:
    """Give a tracked run's session the files attached to the connection.

    ``attach_file`` writes into the connection session's ``context["files"]``.
    A tracked run executes against its own Session (so two conversations never
    share one history object), which would otherwise start with no file map at
    all, making a just-attached file invisible to the very turn that was sent
    to act on it.
    """
    if run_session_id == connection_session_id:
        return
    try:
        connection_session = await chat_service.session_repository.get(
            connection_session_id
        )
        files = (connection_session.context.get("files") if connection_session else None)
        run_session = await chat_service.session_repository.get(run_session_id)
        if run_session is None:
            run_session = await chat_service.create_session(run_session_id, user_email)
        if files:
            run_session.context.setdefault("files", {}).update(dict(files))
    except Exception as e:  # pragma: no cover - defensive
        # A missing file map must not stop the run from starting; the turn
        # simply behaves as it did before this seeding existed.
        logger.warning("Could not seed run session files: %s", e)


def _resume_waiting_run(run_registry, user_email: str, data: dict) -> None:
    """Move a run back to ``running`` after the input it was waiting on arrives.

    The response frame may name its run; when it does not (older clients, and
    MCP elicitations that only carry an elicitation id) fall back to the user's
    single waiting run. Falling back only when exactly one run is waiting keeps
    the guess safe: with two paused runs the status simply stays as it is until
    the run itself reports progress, rather than clearing the wrong one.
    """
    record = run_registry.get_for_user(data.get("run_id"), user_email)
    if record is None:
        record = run_registry.active_for_conversation(
            data.get("conversation_id"), user_email
        )
    if record is None:
        waiting = [
            r
            for r in run_registry.active_for_user(user_email)
            if r.status == RunStatus.WAITING_FOR_INPUT
        ]
        if len(waiting) != 1:
            return
        record = waiting[0]
    if record.status == RunStatus.WAITING_FOR_INPUT:
        run_registry.set_status(record.run_id, RunStatus.RUNNING)


async def cleanup_disconnected_session(
    chat_service,
    session_id,
    user_email,
    active_chat_task,
    connection_run_ids=None,
):
    """Tear down a session whose websocket has closed.

    Cancels the in-flight *untracked* turn before releasing resources: without
    this the background chat task keeps streaming tokens, calling tools and
    holding MCP sessions against a session that end_session() is about to tear
    down -- work nobody can receive.  Mirrors the stop_streaming/reset_session
    paths.

    Tracked runs (issue #884) are the deliberate exception. A run owns its own
    Session, its own conversation and its own MCP sessions, and it is not owned
    by this socket: closing the tab must not stop the agent. Those runs are
    marked detached and left executing, and this function must not release
    resources belonging to a conversation one of them still owns -- otherwise
    the run would keep going with its MCP clients pulled out from under it.
    """
    registry = get_run_registry()
    surviving = [
        r for r in registry.active_for_user(user_email or "")
        if connection_run_ids is None or r.run_id in connection_run_ids
    ]
    for record in surviving:
        registry.mark_detached(record.run_id)
    if surviving:
        logger.info(
            "Client disconnected; leaving %d run(s) executing in the background",
            len(surviving),
        )

    task = active_chat_task.get("task")
    if task and not task.done():
        logger.info("Cancelling active chat task (client disconnected)")
        task.cancel()

    # Release MCP sessions for this conversation, unless a surviving run still
    # owns that conversation.
    session = await chat_service.session_repository.get(session_id)
    if session:
        conv_id = session.context.get("conversation_id", str(session_id))
        # Ask the registry, not this connection's run set: a run started in
        # another tab owns this conversation just as much, and closing *this*
        # tab must not pull its MCP sessions out from under it. `surviving`
        # is connection-scoped and would be empty in exactly that case.
        still_owned = (
            registry.active_for_conversation(conv_id, user_email or "") is not None
        )
        if still_owned:
            logger.info(
                "Keeping MCP sessions for conversation %s; a run still owns it",
                sanitize_for_logging(str(conv_id)),
            )
        else:
            try:
                from atlas.modules.mcp_tools import mcp_tool_manager
                await mcp_tool_manager.release_sessions(conv_id, user_email=user_email)
            except Exception as e:
                logger.warning("Error releasing MCP sessions on disconnect: %s", e)
    await chat_service.end_session(session_id)
    logger.info(f"WebSocket connection closed for session {session_id}")


def _ensure_feedback_directory():
    """Ensure feedback storage directory exists at startup."""
    config = app_factory.get_config_manager()
    if config.app_settings.runtime_feedback_dir:
        feedback_dir = Path(config.app_settings.runtime_feedback_dir)
    else:
        project_root = Path(__file__).resolve().parents[1]
        feedback_dir = project_root / "runtime" / "feedback"
    try:
        feedback_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Feedback directory ready: {feedback_dir}")
    except Exception as e:
        logger.warning(f"Could not create feedback directory {feedback_dir}: {e}")


# How often the run sweeper checks for over-budget runs. A run's wall-clock
# limit is measured in minutes to hours, so a coarse tick is plenty and keeps
# an idle server genuinely idle.
RUN_SWEEP_INTERVAL_SECONDS = 30


async def _run_limit_sweeper(config):
    """Enforce the per-run wall-clock limit and reap old run records (#884)."""
    registry = get_run_registry()
    while True:
        try:
            await asyncio.sleep(RUN_SWEEP_INTERVAL_SECONDS)
            registry.enforce_wall_clock(config.app_settings.max_run_wall_clock_seconds)
            registry.reap_terminal()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the sweeper must never die
            logger.warning("Run limit sweeper iteration failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Chat UI Backend with modular architecture")

    # Initialize configuration
    config = app_factory.get_config_manager()

    # CONFIG: Validate the MCP token encryption key at startup.
    #
    # The key is only resolved lazily, inside request handlers such as
    # GET /api/config. Without this check a server started with a missing
    # key (or with the .env.example placeholder still in place) boots
    # "healthy" — /api/health and /api/config/shell both return 200 — and
    # then answers /api/config with a bare 500 whose cause is invisible to
    # the frontend. Fail here instead, with the actionable message.
    from atlas.modules.mcp_tools.token_storage import resolve_encryption_key
    try:
        resolve_encryption_key(app_settings=config.app_settings)
    except RuntimeError as e:
        logger.error("STARTUP FAILED: %s", e)
        raise

    # SECURITY WARNING: Check for missing proxy secret in production
    if not config.app_settings.debug_mode:
        if not config.app_settings.feature_proxy_secret_enabled:
            logger.warning(
                "SECURITY WARNING: Proxy secret validation is DISABLED in production. "
                "Without it, anyone with direct backend access can spoof the %s header. "
                "Set FEATURE_PROXY_SECRET_ENABLED=true and PROXY_SECRET to enable, "
                "or ensure the backend is only reachable through a trusted reverse proxy.",
                config.app_settings.auth_user_header
            )
        elif not config.app_settings.proxy_secret:
            logger.error(
                "SECURITY ERROR: Proxy secret is ENABLED but PROXY_SECRET is not set. "
                "All requests will be rejected (fail-closed). "
                "Set PROXY_SECRET to a strong random value in .env."
            )

    # SECURITY: Validate Globus session secret (runtime check, complements module-level gate)
    if config.app_settings.feature_globus_auth_enabled:
        _globus_secret = config.app_settings.globus_session_secret
        _GLOBUS_PLACEHOLDER = "atlas-globus-session-change-me"
        if not _globus_secret or _globus_secret == _GLOBUS_PLACEHOLDER:
            logger.error(
                "SECURITY ERROR: Globus auth is enabled but GLOBUS_SESSION_SECRET "
                "is not set (or is still the old placeholder value). "
                "Globus auth routes will reject requests. "
                "Set GLOBUS_SESSION_SECRET to a strong random value in .env."
            )

    logger.info(f"Backend initialized with {len(config.llm_config.models)} LLM models")
    logger.info(f"MCP servers configured: {len(config.mcp_config.servers)}")

    # Ensure feedback directory exists
    _ensure_feedback_directory()

    # Initialize MCP tools manager
    logger.info("Initializing MCP tools manager...")
    mcp_manager = app_factory.get_mcp_manager()

    try:
        logger.info("Step 1: Initializing MCP clients...")
        await mcp_manager.initialize_clients()
        logger.info("Step 1 complete: MCP clients initialized")

        logger.info("Step 2: Discovering tools...")
        await mcp_manager.discover_tools()
        logger.info("Step 2 complete: Tool discovery finished")

        logger.info("Step 3: Discovering prompts...")
        await mcp_manager.discover_prompts()
        logger.info("Step 3 complete: Prompt discovery finished")

        logger.info("MCP tools manager initialization complete")

        # Start auto-reconnect background task if enabled
        logger.info("Step 4: Starting MCP auto-reconnect (if enabled)...")
        await mcp_manager.start_auto_reconnect()
        logger.info("Step 4 complete: Auto-reconnect task started (if enabled)")

    except Exception as e:
        logger.error(f"Error during MCP initialization: {e}", exc_info=True)
        # Continue startup even if MCP fails
        logger.warning("Continuing startup without MCP tools")

    # The user-client cache sweeper must run even when MCP discovery
    # failed above: any per-user HTTP clients created later (e.g. on
    # reconnect or partial init) still need bounded eviction, otherwise
    # the leak guard this PR adds is silently disabled in degraded
    # startup.
    try:
        logger.info("Step 5: Starting MCP user client cache sweeper...")
        await mcp_manager.start_user_client_cache_sweeper()
        logger.info("Step 5 complete: User client cache sweeper started")
    except Exception as e:
        logger.error(f"Failed to start MCP user client cache sweeper: {e}", exc_info=True)

    # Preconfigured personas (issue #880): read the markdown folder once here so
    # a malformed file surfaces in the startup log rather than on a user's first
    # request, and so listing them never touches the filesystem per request.
    try:
        from atlas.modules.prompts.persona_library import get_persona_library

        personas = get_persona_library().reload()
        logger.info("Loaded %d preconfigured persona(s)", len(personas))
    except Exception as e:
        logger.error(f"Failed to load preconfigured personas: {e}", exc_info=True)

    # Issue #884: runs outlive the socket that started them, so nothing else
    # would ever stop a detached run whose agent loop never terminates. This
    # sweeper is the wall-clock backstop, and it also reaps run records that
    # have aged past their retention window.
    run_reaper_task = asyncio.create_task(_run_limit_sweeper(config))

    yield

    logger.info("Shutting down Chat UI Backend")
    run_reaper_task.cancel()
    # Stop auto-reconnect task
    await mcp_manager.stop_auto_reconnect()
    # Cleanup MCP clients
    await mcp_manager.cleanup()


# Create FastAPI app with minimal setup
app = FastAPI(
    title="Chat UI Backend",
    description="Basic chat backend with modular architecture",
    version=VERSION,
    lifespan=lifespan,
)

# Get config for middleware
config = app_factory.get_config_manager()

"""Security: enforce rate limiting and auth middleware.
RateLimit first to cheaply throttle abusive traffic before heavier logic.
"""
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
# Security: refuse to enable Globus auth with a missing/placeholder session secret
_GLOBUS_PLACEHOLDER_SECRET = "atlas-globus-session-change-me"
if config.app_settings.feature_globus_auth_enabled:
    _globus_secret = config.app_settings.globus_session_secret
    if not _globus_secret or _globus_secret == _GLOBUS_PLACEHOLDER_SECRET:
        logger.error(
            "SECURITY: Globus auth DISABLED — GLOBUS_SESSION_SECRET is not set "
            "(or is the old placeholder). Set a strong random value in .env."
        )
        config.app_settings.feature_globus_auth_enabled = False

# Security: OIDC login needs a signed session cookie to carry the opaque
# session id and the in-flight PKCE state. Refuse to enable it without one.
if config.app_settings.feature_oidc_auth_enabled:
    if not config.app_settings.oidc_session_secret:
        logger.error(
            "SECURITY: OIDC auth DISABLED — OIDC_SESSION_SECRET is not set. "
            "Set a strong random value in .env."
        )
        config.app_settings.feature_oidc_auth_enabled = False
    elif not config.app_settings.oidc_issuer or not config.app_settings.oidc_client_id:
        logger.error(
            "OIDC auth DISABLED — OIDC_ISSUER and OIDC_CLIENT_ID are both required."
        )
        config.app_settings.feature_oidc_auth_enabled = False
# Domain whitelist check (if enabled) - add before Auth so it runs after
if config.app_settings.feature_domain_whitelist_enabled:
    app.add_middleware(
        DomainWhitelistMiddleware,
        auth_redirect_url=config.app_settings.auth_redirect_url
    )
app.add_middleware(
    AuthMiddleware,
    debug_mode=config.app_settings.debug_mode,
    auth_header_name=config.app_settings.auth_user_header,
    auth_header_type=config.app_settings.auth_user_header_type,
    auth_aws_expected_alb_arn=config.app_settings.auth_aws_expected_alb_arn,
    auth_aws_region=config.app_settings.auth_aws_region,
    proxy_secret_enabled=config.app_settings.feature_proxy_secret_enabled,
    proxy_secret_header=config.app_settings.proxy_secret_header,
    proxy_secret=config.app_settings.proxy_secret,
    auth_redirect_url=config.app_settings.auth_redirect_url,
    oidc_enabled=config.app_settings.feature_oidc_auth_enabled,
)

# Session middleware backing the Globus OAuth state, the OIDC login session,
# and the in-flight state of the MCP OAuth authorization flow.
#
# Registered *after* AuthMiddleware on purpose: Starlette runs the most
# recently added middleware outermost, so this ordering is what makes
# ``request.session`` populated by the time AuthMiddleware looks for an OIDC
# login session. Registering it earlier (as the Globus-only version did) left
# the session unavailable to authentication and readable only inside routes
# that skip auth entirely.
_session_secret = None
if config.app_settings.feature_oidc_auth_enabled:
    _session_secret = config.app_settings.oidc_session_secret
elif config.app_settings.feature_globus_auth_enabled:
    _session_secret = config.app_settings.globus_session_secret
# The MCP OAuth connect flow needs a browser session to hold its PKCE verifier
# and single-use state, and it is independent of how users log in to Atlas. A
# deployment using header auth with an OAuth-protected MCP server would
# otherwise have no session at all, so a dedicated secret can supply one.
if not _session_secret:
    _session_secret = config.app_settings.mcp_oauth_session_secret
if _session_secret:
    # The session cookie is the login credential in OIDC mode, so it must carry
    # Secure on any https deployment: a hostname with an http listener (an
    # http-to-https redirect, typically) would otherwise leak it in plaintext
    # before the redirect fires. Derived from the configured redirect URI so the
    # common cases need no extra setting, with OIDC_COOKIE_SECURE to override --
    # a hard "on" would break local http development.
    _cookie_secure = config.app_settings.oidc_cookie_secure
    if _cookie_secure is None:
        # Every https URL that identifies this deployment counts, not just the
        # OIDC one. The MCP OAuth flow puts CSRF state in this cookie on
        # header-auth deployments, where oidc_redirect_uri is unset -- deriving
        # the flag from that alone would ship the cookie without Secure over
        # https for exactly the deployment this feature enables.
        _secure_candidates = (
            config.app_settings.oidc_redirect_uri,
            config.app_settings.mcp_oauth_redirect_base_url,
            config.app_settings.backend_public_url,
        )
        _cookie_secure = any(
            (candidate or "").startswith("https://") for candidate in _secure_candidates
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret,
        https_only=_cookie_secure,
        same_site="lax",
        max_age=config.app_settings.oidc_session_max_age_seconds,
    )

# Include essential routes (add files API)
app.include_router(config_router)
app.include_router(admin_router)
app.include_router(telemetry_router)
app.include_router(files_router)
app.include_router(mcp_files_router)
app.include_router(health_router)
app.include_router(feedback_router)
app.include_router(capture_router)
app.include_router(llm_auth_router)
app.include_router(mcp_auth_router)
app.include_router(conversation_router)
app.include_router(user_prompt_router)
app.include_router(persona_router)
app.include_router(workspace_router)
app.include_router(suggestion_router)
agent_portal_router = load_agent_portal_router()
if agent_portal_router is not None:
    app.include_router(agent_portal_router)
# Globus OAuth routes (browser-facing login/callback + JSON API)
app.include_router(globus_browser_router)
app.include_router(globus_api_router)
# OIDC login routes (browser-facing login/callback/logout + JSON status API)
app.include_router(oidc_browser_router)
app.include_router(oidc_api_router)

# Serve frontend build (Vite)
# PyPI package bundles frontend into atlas/static/; local dev uses frontend/dist/
_package_static = Path(__file__).resolve().parent / "static"
_dev_static = Path(__file__).resolve().parents[1] / "frontend" / "dist"
static_dir = _package_static if _package_static.exists() else _dev_static
if static_dir.exists():
    # Serve the SPA entry
    @app.get("/")
    async def read_root():
        return FileResponse(str(static_dir / "index.html"))

    # Serve hashed asset files under /assets (CSS/JS/images from Vite build)
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Serve webfonts from Vite build (placed via frontend/public/fonts)
    fonts_dir = static_dir / "fonts"
    if fonts_dir.exists():
        app.mount("/fonts", StaticFiles(directory=fonts_dir), name="fonts")
    else:
        # Fallback to unbuilt public fonts if dist/fonts is missing
        public_fonts = Path(__file__).resolve().parents[1] / "frontend" / "public" / "fonts"
        if public_fonts.exists():
            app.mount("/fonts", StaticFiles(directory=public_fonts), name="fonts")

    # Common top-level static files in the Vite build
    @app.get("/favicon.ico")
    async def favicon():
        path = static_dir / "favicon.ico"
        return FileResponse(str(path))

    @app.get("/vite.svg")
    async def vite_svg():
        path = static_dir / "vite.svg"
        return FileResponse(str(path))

    @app.get("/logo.png")
    async def logo_png():
        path = static_dir / "logo.png"
        return FileResponse(str(path))

    @app.get("/sandia-powered-by-atlas.png")
    async def logo2_png():
        path = static_dir / "sandia-powered-by-atlas.png"
        return FileResponse(str(path))


# Serve images referenced from help.md
# Search order: project_root/config/help-images/ (user override)
# then atlas/config/help-images/ (shipped defaults)
_atlas_root = Path(__file__).resolve().parent
_project_root = _atlas_root.parent
_help_image_roots = [
    (_project_root / "config" / "help-images").resolve(),
    (_atlas_root / "config" / "help-images").resolve(),
]


@app.get("/help-images/{path:path}")
async def help_image(path: str):
    """Serve images referenced from help.md.

    Resolves ``path`` against the configured help-image roots and rejects
    traversal attempts. Use ``![alt](/help-images/filename.png)`` in help.md
    and drop files into ``config/help-images/`` (user override) or
    ``atlas/config/help-images/`` (shipped default).
    """
    for root in _help_image_roots:
        if not root.exists():
            continue
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            # Path traversal attempt — skip this root
            continue
        if candidate.is_file():
            return FileResponse(str(candidate))
    raise HTTPException(status_code=404, detail="Help image not found")


def _websocket_origin_allowed(websocket: WebSocket, app_settings) -> bool:
    """Decide whether a chat WebSocket upgrade may proceed, by Origin.

    An absent Origin is allowed. Browsers always send the header on an
    upgrade, so its absence means a non-browser client -- a CLI, a test
    harness, an MCP server -- and those carry no ambient cookies for an
    attacker page to borrow. Cross-site WebSocket hijacking is a browser
    attack; rejecting header-less clients would break legitimate integrations
    without closing anything.

    When the header is present it must name loopback (only for a loopback
    target), this deployment's own host, or an entry in
    WEBSOCKET_ALLOWED_ORIGINS.

    Both settings are read as direct attributes rather than through getattr
    with a default: if either is ever renamed, this should fail loudly instead
    of silently disabling the check or emptying the allowlist.
    """
    if not app_settings.feature_websocket_origin_check_enabled:
        return True

    origin = websocket.headers.get("origin")
    if not origin:
        return True

    return origin_is_allowed(
        origin,
        parse_allowed_hosts(app_settings.websocket_allowed_origins),
        request_host=websocket.headers.get("host"),
    )


def _resolve_oidc_websocket_user(websocket, app_settings) -> Optional[str]:
    """Resolve the OIDC login session behind a WebSocket handshake.

    Starlette's SessionMiddleware populates ``scope["session"]`` for websocket
    scopes as well as HTTP ones, so the browser's existing login cookie
    authenticates the socket without a second mechanism. Returns None whenever
    OIDC login is disabled, the session middleware is absent, or the cookie
    does not resolve to a live session -- the caller then falls through to the
    header-based path unchanged.
    """
    if not getattr(app_settings, "feature_oidc_auth_enabled", False):
        return None
    from atlas.core.oidc.session import SESSION_COOKIE_KEY, get_session_store

    try:
        session_id = websocket.session.get(SESSION_COOKIE_KEY)
    except (AssertionError, KeyError):
        return None
    oidc_session = get_session_store().get(session_id)
    return oidc_session.user_id if oidc_session else None


# WebSocket endpoint for chat
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main chat WebSocket endpoint using new architecture.

    SECURITY NOTE - Production Architecture:
    ==========================================
    This endpoint appears to lack authentication when viewed in isolation,
    but in production it sits behind a reverse proxy with a separate
    authentication service. The authentication flow is:

    1. Client connects to WebSocket endpoint
    2. Reverse proxy intercepts WebSocket handshake (HTTP Upgrade request)
    3. Reverse proxy delegates to authentication service
    4. Auth service validates JWT/session from cookies or headers
    5. If valid: Auth service returns authenticated user header
    6. Reverse proxy forwards connection to this app with authenticated user header
    7. This app trusts the header (already validated by auth service)

    The header name is configurable via AUTH_USER_HEADER environment variable
    (default: X-User-Email). This allows flexibility for different reverse proxy setups.

    SECURITY REQUIREMENTS:
    - This app MUST ONLY be accessible via reverse proxy
    - Direct public access to this app bypasses authentication
    - Use network isolation to prevent direct access
    - The /login endpoint lives in the separate auth service
    - Reverse proxy MUST strip client-provided X-User-Email headers before adding its own
      (otherwise attackers can inject headers: X-User-Email: admin@company.com)

    DEVELOPMENT vs PRODUCTION:
    - Production: Extracts user from configured auth header (set by reverse proxy)
    - Development: Falls back to 'user' query parameter (INSECURE, local only)

    See docs/security_architecture.md for complete architecture details.
    """
    # Extract user email using the same authentication flow as HTTP requests
    # Priority: 1) configured auth header (production), 2) query param (dev), 3) test user (dev fallback)
    config_manager = app_factory.get_config_manager()

    is_debug_mode = config_manager.app_settings.debug_mode

    # Origin check: a WS upgrade is not preflighted, so the same-origin policy
    # does not apply. Without this, any page the user visits can open a socket
    # here; the proxy authenticates it from their cookies and hands the
    # attacker a live session. Runs before every other check so a rejected
    # origin never reaches authentication.
    if not _websocket_origin_allowed(websocket, config_manager.app_settings):
        logger.warning(
            "WS rejected disallowed origin=%s client=%s",
            sanitize_for_logging(websocket.headers.get("origin") or ""),
            sanitize_for_logging(websocket.client),
        )
        raise WebSocketException(code=1008, reason="Origin not allowed")

    # An established OIDC login session authenticates the socket on its own,
    # exactly as it does for HTTP in AuthMiddleware. Checked before the proxy
    # secret because an OIDC deployment may have no reverse proxy at all.
    oidc_ws_user = _resolve_oidc_websocket_user(websocket, config_manager.app_settings)

    # WebSocket connections must present the shared proxy secret (same as AuthMiddleware)
    if (
        oidc_ws_user is None
        and config_manager.app_settings.feature_proxy_secret_enabled
        and not is_debug_mode
    ):
        if not config_manager.app_settings.proxy_secret:
            # Fail closed: proxy secret is required but not configured
            logger.error("Proxy secret validation enabled but PROXY_SECRET is not set — rejecting WebSocket")
            raise WebSocketException(code=1008, reason="Server misconfigured: proxy secret not set")

        proxy_secret_header = config_manager.app_settings.proxy_secret_header
        proxy_secret_value = websocket.headers.get(proxy_secret_header)
        if proxy_secret_value != config_manager.app_settings.proxy_secret:
            logger.warning(
                "WS proxy secret mismatch on %s",
                sanitize_for_logging(websocket.client)
            )
            raise WebSocketException(code=1008, reason="Invalid proxy secret")

    # Authenticate user BEFORE accepting the connection
    user_email = oidc_ws_user

    # Check configured auth header first, through the same resolver
    # AuthMiddleware uses. Calling get_user_from_header directly here would
    # skip JWT verification whenever AUTH_USER_HEADER_TYPE is aws-alb-jwt,
    # letting any non-empty header value authenticate the socket.
    app_settings = config_manager.app_settings
    auth_header_name = app_settings.auth_user_header
    x_email_header = websocket.headers.get(auth_header_name)
    if not user_email and x_email_header:
        # Async form: JWT verification can fetch the ALB public key with a
        # blocking 5s httpx call, which would stall the whole event loop.
        user_email = await resolve_user_from_auth_header_async(
            x_email_header,
            header_type=app_settings.auth_user_header_type,
            expected_alb_arn=app_settings.auth_aws_expected_alb_arn,
            aws_region=app_settings.auth_aws_region,
        )

    # Fallback to query parameter (development/testing ONLY)
    if not user_email and is_debug_mode:
        user_email = websocket.query_params.get('user')
        if user_email:
            logger.info(
                "WebSocket authenticated via query parameter (debug mode): %s",
                sanitize_for_logging(user_email)
            )

    # Final fallback to test user (development mode ONLY)
    if not user_email and is_debug_mode:
        user_email = config_manager.app_settings.test_user or 'test@test.com'
        logger.info(
            "WebSocket using fallback test user (debug mode): %s",
            sanitize_for_logging(user_email)
        )

    # PRODUCTION: Reject unauthenticated connections
    if not user_email:
        logger.warning(
            "WebSocket authentication failed - no user found in %s header. Client: %s",
            sanitize_for_logging(auth_header_name),
            sanitize_for_logging(websocket.client)
        )
        raise WebSocketException(
            code=1008,
            reason="Authentication required. Please ensure you are accessing this application through the configured reverse proxy."
        )

    # Capture the Wormhole subtoken (if present) from the handshake headers so
    # it can be forwarded to Wormhole-enabled MCP servers for this user/session.
    # No-ops when the Wormhole feature is disabled.
    try:
        from atlas.modules.mcp_tools.wormhole_token_store import capture_subtoken_from_headers
        capture_subtoken_from_headers(websocket.headers, user_email)
    except Exception:  # pragma: no cover - never block a connection on this
        logger.debug("Failed to capture Wormhole subtoken from WebSocket headers", exc_info=True)

    # Now accept the connection (user is authenticated)
    await websocket.accept()
    logger.info(
        "WebSocket authenticated via %s header: %s",
        sanitize_for_logging(auth_header_name),
        sanitize_for_logging(user_email)
    )

    session_id = uuid4()
    # ``active_chat_task`` tracks the in-flight turn for this connection. The
    # ``steering`` channel (issue #824) lets a second chat message arrive while
    # an agent loop is still running: instead of starting a second concurrent
    # turn (which would race on the same session history), its content is
    # pushed onto the running loop's channel and injected as a normal user turn
    # at the next iteration boundary.
    active_chat_task = {"task": None, "steering": None, "conversation_id": None}

    # Create connection adapter with authenticated user and chat service
    connection_adapter = WebSocketConnectionAdapter(websocket, user_email)
    chat_service = app_factory.create_chat_service(connection_adapter)

    # Parallel conversation runs (issue #884). The registry is process-wide and
    # outlives this socket; ``connection_run_ids`` is just this connection's
    # view of the runs it started, used on disconnect. Runs the user started
    # from *another* tab are still visible to this one through the status
    # listener below, which is what lets any open tab render the
    # "still running" indicator in conversation history.
    run_registry = get_run_registry()
    run_registry.set_max_concurrent_runs_per_user(
        config_manager.app_settings.max_concurrent_runs_per_user
    )
    connection_run_ids: set = set()
    # Strong references to in-flight status sends: asyncio only holds weak
    # references to tasks, so without this a status frame can be garbage
    # collected before it is delivered.
    status_send_tasks: set = set()

    def _publish_run_status(record) -> None:
        """Push one run's state to this socket.

        Called synchronously from the registry on every transition, including
        transitions caused by other connections, so this must never block or
        raise -- the registry is mid-bookkeeping for every other listener.
        """
        if websocket.client_state != WebSocketState.CONNECTED:
            return
        try:
            task = asyncio.create_task(
                websocket_update_callback(
                    websocket, {"type": "run_status", "run": record.to_public_dict()}
                )
            )
        except RuntimeError:  # pragma: no cover - no running loop
            return
        status_send_tasks.add(task)
        task.add_done_callback(status_send_tasks.discard)

    unsubscribe_run_status = run_registry.add_listener(user_email, _publish_run_status)

    logger.info(f"WebSocket connection established for session {sanitize_for_logging(str(session_id))}")

    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")

            # Debug: Log ALL incoming messages
            logger.debug(
                "WS RECEIVED message_type=[%s], data keys=%s",
                sanitize_for_logging(message_type),
                [f"[{sanitize_for_logging(key)}]" for key in data.keys()]
            )

            if message_type == "chat":
                # Issue #824: if an agent loop is already running for this
                # connection, steer it instead of starting a second concurrent
                # turn (which would race on the same session history). The
                # user's message is injected as a normal user turn at the next
                # iteration boundary; the loop is not stopped. ``should_steer``
                # also gates on conversation identity so a message typed after
                # the user switched conversations starts a fresh turn in the
                # new conversation rather than being injected into the old
                # one's context. The channel's ``active`` flag is set by the
                # agent runner, so this only routes when a loop is genuinely
                # consuming -- a turn that requested agent mode but fell back
                # to a non-agent turn never activates the channel.
                # Issue #884: a tracked run owns its conversation. A second
                # message for that conversation is steered into the running
                # loop, exactly as #824 defines -- never started as a second
                # concurrent turn, because two turns writing the same history
                # would interleave their writes.
                frame_conversation_id = data.get("conversation_id")
                tracked_run = run_registry.active_for_conversation(
                    frame_conversation_id, user_email
                )
                if tracked_run is not None:
                    steering = tracked_run.steering
                    if steering is not None and steering.active:
                        try:
                            steering.queue.put_nowait(data.get("content", ""))
                        except asyncio.QueueFull:
                            await websocket.send_json({
                                "type": "error",
                                "message": (
                                    "The agent steering queue is full. Please wait "
                                    "for the agent to make progress before sending "
                                    "another steering message."
                                ),
                                "error_type": "steering_queue_full",
                                "run_id": tracked_run.run_id,
                                "conversation_id": tracked_run.conversation_id,
                            })
                            continue
                        await websocket.send_json({
                            "type": "agent_update",
                            "update_type": "steering_queued",
                            "run_id": tracked_run.run_id,
                            "conversation_id": tracked_run.conversation_id,
                        })
                        continue
                    # The run exists but its loop is not draining yet (it is
                    # starting up, or paused waiting for a tool approval).
                    # Refusing is the honest answer: queueing into a channel
                    # nobody is guaranteed to drain would silently lose the
                    # message, and starting a parallel turn would corrupt the
                    # transcript.
                    await websocket.send_json({
                        "type": "error",
                        "message": (
                            "This conversation already has a run in progress. "
                            "Wait for it to reach its next step, or stop it, "
                            "before sending another message."
                        ),
                        "error_type": "conversation_busy",
                        "run_id": tracked_run.run_id,
                        "conversation_id": tracked_run.conversation_id,
                    })
                    continue

                if should_steer(active_chat_task, data.get("conversation_id")):
                    content = data.get("content", "")
                    steering = active_chat_task["steering"]
                    try:
                        steering.queue.put_nowait(content)
                    except asyncio.QueueFull:
                        # Bounded queue (STEERING_QUEUE_MAXSIZE): a flood of
                        # steers would otherwise stack into history and every
                        # later prompt. Tell the user instead of blocking the
                        # receive loop on a full put.
                        await websocket.send_json({
                            "type": "error",
                            "message": (
                                "The agent steering queue is full. Please wait "
                                "for the agent to make progress before sending "
                                "another steering message."
                            ),
                            "error_type": "steering_queue_full",
                        })
                        continue
                    logger.info("Steered running agent loop with a new user message")
                    # Acknowledge the steer so "queued" is distinguishable from
                    # "dropped" at the client (issue #824 review). The frontend
                    # can render this; unknown update types are ignored.
                    await websocket.send_json({
                        "type": "agent_update",
                        "update_type": "steering_queued",
                    })
                    continue

                # Authoritative server-side gate for system-prompt overrides.
                # The frontend already withholds custom_system_prompt when the
                # feature is disabled, but a stale or hand-crafted client could
                # still send one inline -- the flag is the single source of
                # truth for whether a user-supplied prompt replaces the default.
                # A preconfigured persona (issue #880) is admin-authored content
                # on the server, not user-supplied text, so it is deliberately
                # outside the custom-prompt/chat-history flags: the client sends
                # only an id and the text is resolved after re-checking the
                # persona's access group for this user and the turn's compliance
                # filter, which also means a hand-crafted client cannot use
                # persona_id to smuggle in a prompt of its own.
                from atlas.modules.prompts.persona_library import (
                    resolve_chat_system_prompt,
                )

                custom_system_prompt = await resolve_chat_system_prompt(
                    data.get("custom_system_prompt"),
                    data.get("persona_id"),
                    user_email,
                    config_manager.app_settings.custom_prompts_effective,
                    compliance_level_filter=data.get("compliance_level_filter"),
                )

                try:
                    oversized_file = find_oversized_inline_file(data.get("files"))
                except HTTPException as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e.detail),
                        "error_type": "file_upload"
                    })
                    continue
                if oversized_file:
                    filename, _ = oversized_file
                    limit = get_file_upload_limit_config()
                    await websocket.send_json({
                        "type": "error",
                        "message": (
                            f"File '{filename}' is too large. "
                            f"Maximum size is {limit['max_file_size_mb']}MB."
                        ),
                        "error_type": "file_upload"
                    })
                    continue

                # Handle chat message in background so we can still receive approval responses
                # Issue #824: create a steering channel for agent-mode turns so a
                # chat the user sends while the loop is running is injected as a
                # steering message rather than starting a concurrent turn. The
                # conversation_id is recorded so a later steer that arrives for
                # a different conversation (after a restore/reset) starts a
                # fresh turn instead of injecting into the old one's context.
                is_agent_turn = bool(data.get("agent_mode", False))
                steering_channel = SteeringChannel() if is_agent_turn else None

                # Issue #884: decide whether this turn is a *tracked run* --
                # an execution that owns its own Session, may run alongside
                # other conversations, and survives this socket closing -- or
                # a plain turn with the historical one-at-a-time semantics.
                # Everything downstream keys off ``run_record`` being set, so
                # a deployment without chat history (or a user in local /
                # incognito save mode) follows exactly the pre-#884 path.
                # A brand-new chat has no conversation id yet: the client only
                # learns one from `conversation_saved`, which arrives *after*
                # the turn. Requiring the client to supply one would exclude
                # the single most common case -- the first agent turn in a new
                # conversation -- from background execution entirely. Mint one
                # here instead and tell the client (see `run_started` below);
                # the turn is then saved under the same id the run is keyed by.
                turn_conversation_id = (data.get("conversation_id") or "").strip() or None
                if turn_conversation_id is None and is_agent_turn:
                    turn_conversation_id = str(uuid4())
                if turn_conversation_id:
                    data["conversation_id"] = turn_conversation_id

                run_record = None
                if turn_is_eligible_for_background_run(
                    chat_history_enabled=config_manager.app_settings.feature_chat_history_enabled,
                    save_mode=data.get("save_mode"),
                    incognito=data.get("incognito", False),
                    agent_mode=is_agent_turn,
                    selected_tools=data.get("selected_tools"),
                    conversation_id=turn_conversation_id,
                ):
                    try:
                        run_record = run_registry.start(
                            conversation_id=turn_conversation_id,
                            user_email=user_email,
                            steering=steering_channel,
                        )
                    except ConcurrencyLimitError as e:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                            "error_type": "run_limit_exceeded",
                            "conversation_id": data.get("conversation_id"),
                        })
                        continue
                    except ConversationBusyError as e:
                        # Lost a race with another socket of the same user
                        # between the steer check above and here.
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                            "error_type": "conversation_busy",
                            "conversation_id": data.get("conversation_id"),
                        })
                        continue
                    except RunRegistryError as e:  # pragma: no cover - defensive
                        logger.warning("Run could not be started: %s", e)
                        run_record = None

                if run_record is None:
                    # Untracked turn: keep the single-slot behaviour, including
                    # steering, cancel-on-reset and cancel-on-disconnect.
                    active_chat_task["steering"] = steering_channel
                    active_chat_task["conversation_id"] = data.get("conversation_id")
                    turn_session_id = session_id

                    async def turn_update_callback(message):
                        await websocket_update_callback(websocket, message)
                else:
                    connection_run_ids.add(run_record.run_id)
                    # The run gets its own session id, so two conversations
                    # executing at once never share one Session/history object.
                    turn_session_id = run_record.session_id
                    # Files attached via `attach_file` land on the *connection*
                    # session's context, which the run's fresh session cannot
                    # see -- the tool that needs them would report the file as
                    # missing. Create the run session here (the same path
                    # handle_chat_message would take lazily) and carry the file
                    # map across. Copied, not shared, so the run and the
                    # connection cannot mutate each other's state.
                    await _seed_run_session_files(
                        chat_service, session_id, turn_session_id, user_email
                    )

                    async def turn_update_callback(
                        message,
                        _run_id=run_record.run_id,
                        _conv=run_record.conversation_id,
                    ):
                        # A run that asks for tool approval is paused, not
                        # working: reflect that in its status so the client can
                        # show "waiting for you" on a conversation the user is
                        # not currently looking at.
                        message_type_out = message.get("type") if isinstance(message, dict) else None
                        if message_type_out in ("tool_approval_request", "elicitation_request"):
                            run_registry.set_status(
                                _run_id,
                                RunStatus.WAITING_FOR_INPUT,
                                waiting_on=message_type_out,
                            )
                            # Keep the frame itself. If the user is looking at
                            # another conversation (or is not here at all) the
                            # client discards it, and nothing else holds the
                            # request id and arguments needed to answer it.
                            run_registry.set_pending_request(
                                _run_id, tag_run_event(message, _run_id, _conv)
                            )
                        elif message_type_out in _TOOL_SETTLED_EVENTS:
                            # The tool the run was paused on has settled one way
                            # or another. Resolving the pause here as well as on
                            # the response frame covers the case where nobody
                            # ever answers and the request times out -- the run
                            # carries on working, and would otherwise be stuck
                            # showing "Needs approval" for the rest of its life.
                            record = run_registry.get(_run_id)
                            if record is not None and record.status == RunStatus.WAITING_FOR_INPUT:
                                run_registry.set_status(_run_id, RunStatus.RUNNING)
                        await websocket_update_callback(
                            websocket, tag_run_event(message, _run_id, _conv)
                        )

                # Bind the per-turn values as defaults: the loop reassigns them
                # on the next message, and a still-running task must keep the
                # session and callback it was started with.
                # Collects the error type of a domain failure that handle_chat
                # reports to the client and then swallows. Without it every one
                # of those turns would be recorded as a *completed* run, and a
                # user who reconnects could not tell a finished background run
                # from one that died on a rate limit.
                turn_failure = []

                # Every frame this coroutine emits -- the error branches and the
                # cancellation flush included -- goes through
                # `turn_update_callback`, never `websocket.send_json` directly.
                # The callback is what stamps `run_id`/`conversation_id` on a
                # tracked run's frames; a raw send carries no run identity, so a
                # background run's rate-limit or timeout error would be rendered
                # into whichever conversation the user happens to be looking at.
                async def handle_chat(
                    turn_failure=turn_failure,
                    turn_session_id=turn_session_id,
                    steering_channel=steering_channel,
                    turn_update_callback=turn_update_callback,
                    data=data,
                    custom_system_prompt=custom_system_prompt,
                ):
                    try:
                        await chat_service.handle_chat_message(
                            session_id=turn_session_id,
                            content=data.get("content", ""),
                            model=data.get("model", ""),
                            selected_tools=data.get("selected_tools"),
                            selected_prompts=data.get("selected_prompts"),
                            selected_data_sources=data.get("selected_data_sources"),
                            only_rag=data.get("only_rag", False),
                            user_email=user_email,  # Use authenticated user from connection
                            agent_mode=data.get("agent_mode", False),
                            agent_max_steps=data.get("agent_max_steps", 10),
                            temperature=data.get("temperature", 0.7),
                            agent_loop_strategy=data.get("agent_loop_strategy"),
                            # User's active compliance level (trusted server-side
                            # once validated in the service layer, not by the model).
                            compliance_level=data.get("compliance_level_filter"),
                            custom_system_prompt=custom_system_prompt,
                            update_callback=turn_update_callback,
                            files=data.get("files"),
                            incognito=data.get("save_mode", "server") != "server" or data.get("incognito", False),
                            conversation_id=data.get("conversation_id"),
                            rewind_to_user_index=data.get("rewind_to_user_index"),
                            capture_correction=data.get("capture_correction"),
                            # Forward the omitted-vs-null distinction intact
                            # (issue #829): `.get(...)` alone would turn an
                            # omitted field into an explicit null and clear the
                            # conversation's workspace binding for any client
                            # that does not send it.
                            workspace_id=data.get("workspace_id", UNSET),
                            steering=steering_channel,
                        )
                    except RateLimitError as e:
                        logger.warning(f"Rate limit error in chat handler: {e}")
                        turn_failure.append("rate_limit")
                        log_metric("error", user_email, error_type="rate_limit")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "rate_limit"
                        })
                    except LLMTimeoutError as e:
                        logger.warning(f"Timeout error in chat handler: {e}")
                        turn_failure.append("timeout")
                        log_metric("error", user_email, error_type="timeout")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "timeout"
                        })
                    except LLMAuthenticationError as e:
                        logger.error(f"Authentication error in chat handler: {e}")
                        turn_failure.append("authentication")
                        log_metric("error", user_email, error_type="authentication")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "authentication"
                        })
                    except ContextWindowExceededError as e:
                        logger.warning(f"Context window exceeded in chat handler: {e}")
                        turn_failure.append("context_window_exceeded")
                        log_metric("error", user_email, error_type="context_window_exceeded")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "context_window_exceeded"
                        })
                    except LLMMalformedToolCallError as e:
                        logger.warning(f"Model returned an unusable tool call in chat handler: {e}")
                        turn_failure.append("malformed_tool_call")
                        log_metric("error", user_email, error_type="malformed_tool_call")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "malformed_tool_call"
                        })
                    except LLMBadRequestError as e:
                        logger.warning(f"Provider rejected the request in chat handler: {e}")
                        turn_failure.append("bad_request")
                        log_metric("error", user_email, error_type="bad_request")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "bad_request"
                        })
                    except ValidationError as e:
                        logger.warning(f"Validation error in chat handler: {e}")
                        turn_failure.append("validation")
                        log_metric("error", user_email, error_type="validation")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "validation"
                        })
                    except AuthorizationError as e:
                        logger.warning(f"Authorization error in chat handler: {e}")
                        turn_failure.append("authorization")
                        log_metric("error", user_email, error_type="authorization")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "authorization"
                        })
                    except asyncio.CancelledError:
                        logger.info("Chat task cancelled by user (stop_streaming)")
                        try:
                            await turn_update_callback({
                                "type": "token_stream",
                                "token": "",
                                "is_first": False,
                                "is_last": True,
                            })
                            await turn_update_callback({
                                "type": "response_complete",
                            })
                        except Exception:
                            pass  # WebSocket may already be closed
                        return
                    except DomainError as e:
                        logger.error(f"Domain error in chat handler: {e}", exc_info=True)
                        turn_failure.append("domain")
                        log_metric("error", user_email, error_type="domain")
                        await turn_update_callback({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "domain"
                        })
                    except Exception as e:
                        logger.error(f"Unexpected error in chat handler: {e}", exc_info=True)
                        turn_failure.append("unexpected")
                        log_metric("error", user_email, error_type="unexpected")
                        await turn_update_callback({
                            "type": "error",
                            "message": "An unexpected error occurred. Please try again or contact support if the issue persists.",
                            "error_type": "unexpected"
                        })

                async def handle_chat_guarded(
                    run_id=(run_record.run_id if run_record else None),
                    run_conversation_id=(run_record.conversation_id if run_record else None),
                    run_session_id=(run_record.session_id if run_record else None),
                ):
                    """Run handle_chat so a dead socket cannot orphan the task.

                    Every error branch in handle_chat reports back over the
                    websocket.  If the client is already gone that send raises a
                    *second* exception from inside the except block, which
                    escapes the task entirely and surfaces only as an
                    "exception was never retrieved" warning at GC time.  The
                    error metric is logged before each send, so nothing is lost
                    by absorbing it here.
                    """
                    outcome = RunStatus.COMPLETED
                    error_message = None
                    if run_id is not None:
                        # Bind before the first await so every frame the agent
                        # loop publishes -- including the ones that go through
                        # the shared event publisher rather than this turn's
                        # callback -- carries this run's identity.
                        set_current_run(run_id, run_conversation_id)
                    try:
                        await handle_chat()
                    except asyncio.CancelledError:
                        outcome = RunStatus.CANCELLED
                        raise
                    except (WebSocketDisconnect, RuntimeError) as e:
                        logger.info("Chat handler ended; websocket already closed: %s", e)
                    except Exception as e:  # pragma: no cover - handle_chat absorbs its own
                        outcome = RunStatus.FAILED
                        error_message = str(e)
                        raise
                    finally:
                        # A tracked run reaches a terminal state here, and it
                        # has to be in a ``finally``: a run left non-terminal
                        # because its task died in an unexpected way would hold
                        # a slot against the user's concurrency cap forever,
                        # and its conversation's one-run-at-a-time lock with
                        # it. ``set_status`` keeps terminal states sticky, so a
                        # run the user already stopped is not reported as
                        # completed by its own unwinding task.
                        if outcome == RunStatus.COMPLETED and turn_failure:
                            # handle_chat reported this to the client and
                            # returned normally; the run still did not succeed.
                            outcome = RunStatus.FAILED
                            error_message = turn_failure[0]
                        if run_id is not None:
                            run_registry.set_status(run_id, outcome, error=error_message)
                            # This `finally` often runs while the task is
                            # already unwinding from a cancellation. A *second*
                            # cancellation delivered during the await below
                            # would abandon the release half-done, leaking the
                            # run's Session and leaving the conversation's MCP
                            # sessions held. Shield it so the cleanup always
                            # runs to completion, and swallow the
                            # CancelledError the shield re-raises here -- the
                            # run's terminal status is already recorded, so
                            # there is nothing further this task owes anyone.
                            release = asyncio.shield(
                                asyncio.ensure_future(
                                    _release_finished_run(
                                        chat_service,
                                        run_registry,
                                        run_id,
                                        run_session_id,
                                        run_conversation_id,
                                        user_email,
                                    )
                                )
                            )
                            try:
                                await release
                            except asyncio.CancelledError:
                                pass

                # Start chat handling in background
                chat_task = asyncio.create_task(handle_chat_guarded())
                if run_record is None:
                    active_chat_task["task"] = chat_task
                else:
                    run_registry.attach_task(run_record.run_id, chat_task)
                    await websocket.send_json({
                        "type": "run_started",
                        "run_id": run_record.run_id,
                        "conversation_id": run_record.conversation_id,
                    })

            elif message_type == "download_file":
                # Handle file download (use authenticated user from connection).
                # A file produced by a tracked run lives in that run's session
                # file map, not the connection's, so searching only the
                # connection session would fail every download of a background
                # run's output. Try the connection session first (the common
                # case and the cheapest), then the sessions of this user's runs.
                filename = data.get("filename", "")
                response = None
                for candidate_session_id in _download_session_candidates(
                    run_registry, session_id, user_email, data
                ):
                    response = await chat_service.handle_download_file(
                        session_id=candidate_session_id,
                        filename=filename,
                        user_email=user_email,
                    )
                    if not response.get("error"):
                        break
                # Echo the run identity the client addressed so a client that
                # routes frames by conversation can place the reply.
                if response is not None:
                    for key in ("run_id", "conversation_id"):
                        if data.get(key) and not response.get(key):
                            response[key] = data.get(key)
                await websocket.send_json(response)

            elif message_type == "restore_conversation":
                # Issue #824: dropping the steering channel here means a
                # message typed after switching conversations starts a fresh
                # turn in the new conversation instead of being injected into
                # the previous conversation's (possibly still-running) agent.
                active_chat_task["steering"] = None
                active_chat_task["conversation_id"] = None

                # Release MCP sessions for the current conversation before restoring
                session = await chat_service.session_repository.get(session_id)
                if session:
                    old_conv_id = session.context.get("conversation_id")
                    # Issue #884: navigation controls what is *visible*, not
                    # what is allowed to execute. Releasing the MCP sessions of
                    # a conversation that still has a live run would pull the
                    # tool clients out from under an agent that is mid-step,
                    # just because the user looked at something else.
                    if old_conv_id and run_registry.active_for_conversation(
                        old_conv_id, user_email
                    ) is not None:
                        logger.info(
                            "Keeping MCP sessions for conversation %s; a run still owns it",
                            sanitize_for_logging(str(old_conv_id)),
                        )
                    elif old_conv_id:
                        try:
                            from atlas.modules.mcp_tools import mcp_tool_manager
                            await mcp_tool_manager.release_sessions(old_conv_id, user_email=user_email)
                        except Exception as e:
                            logger.debug("Error releasing MCP sessions on restore: %s", e)

                # Restore a saved conversation into the current session.
                # The handler returns an error frame on access denied, but a
                # DomainError still propagates as a transport invariant
                # safety net so a future raise cannot tear down the
                # WebSocket here (matches the chat-handler contract).
                try:
                    response = await chat_service.handle_restore_conversation(
                        session_id=session_id,
                        conversation_id=data.get("conversation_id", ""),
                        messages=data.get("messages", []),
                        user_email=user_email
                    )
                except DomainError as e:
                    logger.warning(
                        "Domain error in restore_conversation: %s", e
                    )
                    log_metric("error", user_email, error_type="domain")
                    response = {
                        "type": "error",
                        "message": str(e.message if hasattr(e, "message") else e),
                        "error_type": (
                            "authorization"
                            if isinstance(e, AuthorizationError)
                            else "domain"
                        ),
                    }
                await websocket.send_json(response)

                # Issue #884: if a run in the conversation the user just opened
                # is blocked on an approval, re-send the request now. It was
                # dropped when it first arrived (the user was elsewhere), and
                # without this replay the run stays blocked until it times out
                # with no way for anyone to answer it.
                for pending in run_registry.pending_requests_for_conversation(
                    data.get("conversation_id", ""), user_email
                ):
                    await websocket.send_json(pending)

            elif message_type == "reset_session":
                # Issue #884: only the *untracked* turn is cancelled here.
                # Tracked runs are not owned by the visible conversation, so
                # "New Chat" must not stop one -- that was precisely the
                # behaviour this issue removes. They keep executing, keep their
                # MCP sessions (see below), and keep reporting status to the
                # client through run_status frames.
                #
                # If a chat is still generating when the user starts a new chat,
                # cancel it first so tokens don't keep streaming into the fresh
                # session (defense-in-depth; the frontend also sends
                # stop_streaming before reset_session when it knows generation
                # is in progress).
                task = active_chat_task.get("task")
                if task and not task.done():
                    logger.info("Cancelling active chat task (reset_session)")
                    task.cancel()
                    # Let the cancelled turn finish committing before the reset
                    # (issue #755). Its cleanup now persists the interrupted
                    # turn and emits conversation_saved for the *old*
                    # conversation; arriving after session_reset, that event
                    # would re-point the fresh empty chat at the abandoned
                    # conversation id. Bounded so a wedged task cannot block the
                    # reset the user asked for.
                    try:
                        await asyncio.wait([task], timeout=5)
                    except Exception as e:  # pragma: no cover - defensive
                        logger.warning("Error waiting for cancelled chat task: %s", e)

                # Issue #824: drop the steering channel so a message typed after
                # the reset steers the next turn (or starts fresh) rather than a
                # channel whose loop was just cancelled.
                active_chat_task["steering"] = None
                active_chat_task["conversation_id"] = None

                # Handle session reset (use authenticated user from connection).
                # handle_reset_session itself releases the old conversation's
                # MCP sessions before generating a new conversation_id.
                # A tracked run may own the conversation this session is
                # currently pointing at; handle_reset_session would otherwise
                # release its MCP sessions on the way to a fresh conversation.
                reset_session_record = await chat_service.session_repository.get(session_id)
                reset_conv_id = (
                    reset_session_record.context.get("conversation_id")
                    if reset_session_record
                    else None
                )
                preserve_resources = (
                    run_registry.active_for_conversation(reset_conv_id, user_email)
                    is not None
                )
                response = await chat_service.handle_reset_session(
                    session_id=session_id,
                    user_email=user_email,
                    preserve_conversation_resources=preserve_resources,
                )
                await websocket.send_json(response)

            elif message_type == "attach_file":
                # Handle file attachment to session (use authenticated user, not client-sent)
                response = await chat_service.handle_attach_file(
                    session_id=session_id,
                    s3_key=data.get("s3_key"),
                    user_email=user_email,  # Use authenticated user from connection
                    update_callback=lambda message: websocket_update_callback(websocket, message)
                )
                await websocket.send_json(response)

            elif message_type == "tool_approval_response":
                # Handle tool approval response
                from atlas.application.chat.approval_manager import get_approval_manager
                approval_manager = get_approval_manager()

                tool_call_id = data.get("tool_call_id")
                approved = data.get("approved", False)
                arguments = data.get("arguments")
                reason = data.get("reason")

                # SECURITY: Never log tool arguments at INFO level (they may include sensitive user data).
                # Log a conservative summary instead.
                logger.info(
                    "Received tool approval response: %s",
                    summarize_tool_approval_response_for_logging(data),
                )

                logger.info(f"Processing approval: tool_call_id={sanitize_for_logging(tool_call_id)}, approved={approved}")

                result = approval_manager.handle_approval_response(
                    tool_call_id=tool_call_id,
                    approved=approved,
                    arguments=arguments,
                    reason=reason,
                    user_email=user_email,
                )

                # The run that was paused on this approval is working again.
                _resume_waiting_run(run_registry, user_email, data)

                logger.info(f"Approval response handled: result={sanitize_for_logging(result)}")
                # No response needed - the approval will unblock the waiting tool execution

            elif message_type == "stop_streaming":
                # Issue #884: Stop addresses one run. A client that names a
                # run_id (or the conversation it is looking at) stops exactly
                # that run and nothing else -- stopping A must never stop B.
                # A frame with neither falls back to the untracked slot, which
                # is what an older client sends.
                if not _cancel_addressed_run(run_registry, user_email, data):
                    task = active_chat_task.get("task")
                    if task and not task.done():
                        logger.info("Cancelling active chat task (stop_streaming)")
                        task.cancel()

            elif message_type == "agent_control":
                # `agent_control` with action="stop" cancels the active agent
                # run. The native agentic loop has no user-input-wait branch, so
                # the message is always handled here as a plain task cancel.
                action = data.get("action")
                if action == "stop":
                    if not _cancel_addressed_run(run_registry, user_email, data):
                        task = active_chat_task.get("task")
                        if task and not task.done():
                            logger.info("Cancelling active chat task (agent_control stop)")
                            task.cancel()

            elif message_type == "elicitation_response":
                # Handle elicitation response
                from atlas.application.chat.elicitation_manager import get_elicitation_manager
                elicitation_manager = get_elicitation_manager()

                elicitation_id = data.get("elicitation_id")
                action = data.get("action", "cancel")
                response_data = data.get("data")

                logger.info(
                    f"Received elicitation response: id={sanitize_for_logging(elicitation_id)}, "
                    f"action={action}"
                )

                # Fail-closed ownership check lives in the manager: a bound
                # elicitation only accepts a response from the user it was
                # created for, so an id leaked to another session cannot inject
                # data into this tool execution.
                result = elicitation_manager.handle_elicitation_response(
                    elicitation_id=elicitation_id,
                    action=action,
                    data=response_data,
                    user_email=user_email,
                )

                _resume_waiting_run(run_registry, user_email, data)

                logger.info(f"Elicitation response handled: result={sanitize_for_logging(result)}")
                # No response needed - the elicitation will unblock the waiting tool execution

            elif message_type == "list_runs":
                # Issue #884: how a freshly opened (or reconnected) client
                # learns which conversations are still working. Without this a
                # user who closed the tab while an agent was running would
                # reopen to a history list with no indication that anything is
                # in flight.
                await websocket.send_json({
                    "type": "runs_snapshot",
                    "runs": run_registry.snapshot_for_user(user_email),
                    "max_concurrent_runs_per_user": run_registry.max_concurrent_runs_per_user,
                })
                # Replay whatever the currently open conversation is blocked on,
                # so a reconnect into that conversation can answer it.
                for pending in run_registry.pending_requests_for_conversation(
                    data.get("conversation_id"), user_email
                ):
                    await websocket.send_json(pending)

            else:
                logger.warning(f"Unknown message type: {sanitize_for_logging(message_type)}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {sanitize_for_logging(message_type)}"
                })

    except WebSocketDisconnect:
        await cleanup_disconnected_session(
            chat_service, session_id, user_email, active_chat_task, connection_run_ids
        )
    finally:
        # Stop feeding run-status frames to a socket that is gone; the runs
        # themselves keep going.
        unsubscribe_run_status()


if static_dir.exists():
    # Known SPA frontend routes from frontend/src/App.jsx. F5 on any of
    # these (or a deeper React Router subroute) should serve index.html.
    # Anything else falls through to a real 404 instead of being silently
    # masked by the SPA — important because `/help-images/...`, `/admin/
    # telemetry/turn/{id}`, etc. have legitimate non-SPA handlers whose
    # 404 / validation responses must not be hidden.
    _SPA_ROUTE_PREFIXES = (
        "marketplace",
        "help",
        "admin",
        "files",
        "agent-portal",
    )

    @app.get("/{full_path:path}")
    async def spa_catchall(full_path: str):
        if ".." in full_path.split("/"):
            raise HTTPException(status_code=404, detail="Not Found")
        first = full_path.split("/", 1)[0]
        if first not in _SPA_ROUTE_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import os

    import uvicorn

    # Use environment variable for host binding, default to localhost for security
    # Set ATLAS_HOST=0.0.0.0 in production environments where needed
    host = os.getenv("ATLAS_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        app,
        host=host,
        port=port,
        ws_ping_interval=config.app_settings.websocket_keepalive_interval_seconds,
        ws_ping_timeout=config.app_settings.websocket_keepalive_interval_seconds,
    )
