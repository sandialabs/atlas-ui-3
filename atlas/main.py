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
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from atlas.core.auth import get_user_from_header
from atlas.core.domain_whitelist_middleware import DomainWhitelistMiddleware
from atlas.core.log_sanitizer import sanitize_for_logging, summarize_tool_approval_response_for_logging
from atlas.core.metrics_logger import log_metric

# Import from atlas.core (only essential middleware and config)
from atlas.core.middleware import AuthMiddleware
from atlas.core.otel_config import setup_opentelemetry
from atlas.core.rate_limit_middleware import RateLimitMiddleware
from atlas.core.security_headers_middleware import SecurityHeadersMiddleware

# Import domain errors
from atlas.domain.errors import DomainError, LLMAuthenticationError, LLMTimeoutError, RateLimitError, ValidationError

# Import from atlas.infrastructure
from atlas.infrastructure.app_factory import app_factory
from atlas.infrastructure.transport.websocket_connection_adapter import WebSocketConnectionAdapter
from atlas.routes.admin_routes import admin_router

# Import essential routes
from atlas.routes.config_routes import router as config_router
from atlas.routes.conversation_routes import router as conversation_router
from atlas.routes.feedback_routes import feedback_router
from atlas.routes.files_routes import router as files_router
from atlas.routes.health_routes import router as health_router
from atlas.routes.llm_auth_routes import router as llm_auth_router
from atlas.routes.mcp_auth_routes import router as mcp_auth_router
from atlas.version import VERSION

# Load environment variables from the parent directory
load_dotenv(dotenv_path="../.env")

# Setup OpenTelemetry logging
otel_config = setup_opentelemetry("atlas-ui-3-backend", "1.0.0")

logger = logging.getLogger(__name__)


async def websocket_update_callback(websocket: WebSocket, message: dict):
    """
    Callback function to handle websocket updates with logging.
    """
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
    await websocket.send_json(message)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Chat UI Backend with modular architecture")

    # Initialize configuration
    config = app_factory.get_config_manager()

    # SECURITY WARNING: Check for missing proxy secret in production
    if not config.app_settings.debug_mode:
        if not config.app_settings.feature_proxy_secret_enabled:
            logger.warning(
                "SECURITY WARNING: Proxy secret validation is DISABLED in production. "
                "Set FEATURE_PROXY_SECRET_ENABLED=true and PROXY_SECRET to enable."
            )
        elif not config.app_settings.proxy_secret:
            logger.warning(
                "SECURITY WARNING: Proxy secret is ENABLED but PROXY_SECRET is not set. "
                "Authentication will fail for all requests."
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

    yield

    logger.info("Shutting down Chat UI Backend")
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
    auth_redirect_url=config.app_settings.auth_redirect_url
)

# Include essential routes (add files API)
app.include_router(config_router)
app.include_router(admin_router)
app.include_router(files_router)
app.include_router(health_router)
app.include_router(feedback_router)
app.include_router(llm_auth_router)
app.include_router(mcp_auth_router)
app.include_router(conversation_router)

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

    # WebSocket connections must present the shared proxy secret (same as AuthMiddleware)
    if (
        config_manager.app_settings.feature_proxy_secret_enabled
        and config_manager.app_settings.proxy_secret
        and not is_debug_mode
    ):
        proxy_secret_header = config_manager.app_settings.proxy_secret_header
        proxy_secret_value = websocket.headers.get(proxy_secret_header)
        if proxy_secret_value != config_manager.app_settings.proxy_secret:
            logger.warning(
                "WS proxy secret mismatch on %s",
                sanitize_for_logging(websocket.client)
            )
            raise WebSocketException(code=1008, reason="Invalid proxy secret")

    # Authenticate user BEFORE accepting the connection
    user_email = None

    # Check configured auth header first (consistent with AuthMiddleware)
    auth_header_name = config_manager.app_settings.auth_user_header
    x_email_header = websocket.headers.get(auth_header_name)
    if x_email_header:
        user_email = get_user_from_header(x_email_header)

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

    # Now accept the connection (user is authenticated)
    await websocket.accept()
    logger.info(
        "WebSocket authenticated via %s header: %s",
        sanitize_for_logging(auth_header_name),
        sanitize_for_logging(user_email)
    )

    session_id = uuid4()

    # Create connection adapter with authenticated user and chat service
    connection_adapter = WebSocketConnectionAdapter(websocket, user_email)
    chat_service = app_factory.create_chat_service(connection_adapter)

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
                # Handle chat message in background so we can still receive approval responses
                async def handle_chat():
                    try:
                        await chat_service.handle_chat_message(
                            session_id=session_id,
                            content=data.get("content", ""),
                            model=data.get("model", ""),
                            selected_tools=data.get("selected_tools"),
                            selected_prompts=data.get("selected_prompts"),
                            selected_data_sources=data.get("selected_data_sources"),
                            only_rag=data.get("only_rag", False),
                            tool_choice_required=data.get("tool_choice_required", False),
                            user_email=user_email,  # Use authenticated user from connection
                            agent_mode=data.get("agent_mode", False),
                            agent_max_steps=data.get("agent_max_steps", 10),
                            temperature=data.get("temperature", 0.7),
                            agent_loop_strategy=data.get("agent_loop_strategy"),
                            update_callback=lambda message: websocket_update_callback(websocket, message),
                            files=data.get("files"),
                            incognito=data.get("save_mode", "server") != "server" or data.get("incognito", False),
                            conversation_id=data.get("conversation_id"),
                        )
                    except RateLimitError as e:
                        logger.warning(f"Rate limit error in chat handler: {e}")
                        log_metric("error", user_email, error_type="rate_limit")
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "rate_limit"
                        })
                    except LLMTimeoutError as e:
                        logger.warning(f"Timeout error in chat handler: {e}")
                        log_metric("error", user_email, error_type="timeout")
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "timeout"
                        })
                    except LLMAuthenticationError as e:
                        logger.error(f"Authentication error in chat handler: {e}")
                        log_metric("error", user_email, error_type="authentication")
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "authentication"
                        })
                    except ValidationError as e:
                        logger.warning(f"Validation error in chat handler: {e}")
                        log_metric("error", user_email, error_type="validation")
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "validation"
                        })
                    except DomainError as e:
                        logger.error(f"Domain error in chat handler: {e}", exc_info=True)
                        log_metric("error", user_email, error_type="domain")
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e.message if hasattr(e, 'message') else e),
                            "error_type": "domain"
                        })
                    except Exception as e:
                        logger.error(f"Unexpected error in chat handler: {e}", exc_info=True)
                        log_metric("error", user_email, error_type="unexpected")
                        await websocket.send_json({
                            "type": "error",
                            "message": "An unexpected error occurred. Please try again or contact support if the issue persists.",
                            "error_type": "unexpected"
                        })

                # Start chat handling in background
                asyncio.create_task(handle_chat())

            elif message_type == "download_file":
                # Handle file download (use authenticated user from connection)
                response = await chat_service.handle_download_file(
                    session_id=session_id,
                    filename=data.get("filename", ""),
                    user_email=user_email
                )
                await websocket.send_json(response)

            elif message_type == "restore_conversation":
                # Restore a saved conversation into the current session
                response = await chat_service.handle_restore_conversation(
                    session_id=session_id,
                    conversation_id=data.get("conversation_id", ""),
                    messages=data.get("messages", []),
                    user_email=user_email
                )
                await websocket.send_json(response)

            elif message_type == "reset_session":
                # Handle session reset (use authenticated user from connection)
                response = await chat_service.handle_reset_session(
                    session_id=session_id,
                    user_email=user_email
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
                    reason=reason
                )

                logger.info(f"Approval response handled: result={sanitize_for_logging(result)}")
                # No response needed - the approval will unblock the waiting tool execution

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

                result = elicitation_manager.handle_elicitation_response(
                    elicitation_id=elicitation_id,
                    action=action,
                    data=response_data
                )

                logger.info(f"Elicitation response handled: result={sanitize_for_logging(result)}")
                # No response needed - the elicitation will unblock the waiting tool execution

            else:
                logger.warning(f"Unknown message type: {sanitize_for_logging(message_type)}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {sanitize_for_logging(message_type)}"
                })

    except WebSocketDisconnect:
        chat_service.end_session(session_id)
        logger.info(f"WebSocket connection closed for session {session_id}")


if __name__ == "__main__":
    import os

    import uvicorn

    # Use environment variable for host binding, default to localhost for security
    # Set ATLAS_HOST=0.0.0.0 in production environments where needed
    host = os.getenv("ATLAS_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8000))

    uvicorn.run(app, host=host, port=port)
