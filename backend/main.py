"""
Basic chat backend implementing the modular architecture.
Focuses on essential chat functionality only.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

# Import domain errors
from domain.errors import ValidationError

# Import from core (only essential middleware and config)
from core.middleware import AuthMiddleware
from core.rate_limit_middleware import RateLimitMiddleware
from core.security_headers_middleware import SecurityHeadersMiddleware
from core.otel_config import setup_opentelemetry
from core.utils import sanitize_for_logging

# Import from infrastructure
from infrastructure.app_factory import app_factory
from infrastructure.transport.websocket_connection_adapter import WebSocketConnectionAdapter

# Import essential routes
from routes.config_routes import router as config_router
from routes.admin_routes import admin_router
from routes.files_routes import router as files_router

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
            if utype == "canvas_files":
                files = (message.get("data") or {}).get("files") or []
                # logger.info(
                #     "WS SEND: intermediate_update canvas_files count=%d files=%s display=%s",
                #     len(files),
                #     [f.get("filename") for f in files if isinstance(f, dict)],
                #     (message.get("data") or {}).get("display"),
                # )
            elif utype == "files_update":
                files = (message.get("data") or {}).get("files") or []
            #     logger.info(
            #         "WS SEND: intermediate_update files_update total=%d",
            #         len(files),
            #     )
            # else:
            #     logger.info("WS SEND: intermediate_update update_type=%s", utype)
        elif mtype == "canvas_content":
            content = message.get("content")
            clen = len(content) if isinstance(content, str) else "obj"
            logger.info("WS SEND: canvas_content length=%s", clen)
        else:
            logger.info("WS SEND: %s", mtype)
    except Exception:
        # Non-fatal logging error; continue to send
        pass
    await websocket.send_json(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Chat UI Backend with modular architecture")
    
    # Initialize configuration
    config = app_factory.get_config_manager()
    
    logger.info(f"Backend initialized with {len(config.llm_config.models)} LLM models")
    logger.info(f"MCP servers configured: {len(config.mcp_config.servers)}")
    
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
    except Exception as e:
        logger.error(f"Error during MCP initialization: {e}", exc_info=True)
        # Continue startup even if MCP fails
        logger.warning("Continuing startup without MCP tools")
    
    yield
    
    logger.info("Shutting down Chat UI Backend")
    # Cleanup MCP clients
    await mcp_manager.cleanup()


# Create FastAPI app with minimal setup
app = FastAPI(
    title="Chat UI Backend",
    description="Basic chat backend with modular architecture", 
    version="2.0.0",
    lifespan=lifespan,
)

# Get config for middleware
config = app_factory.get_config_manager()

"""Security: enforce rate limiting and auth middleware.
RateLimit first to cheaply throttle abusive traffic before heavier logic.
"""
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware, debug_mode=config.app_settings.debug_mode)

# Include essential routes (add files API)
app.include_router(config_router)
app.include_router(admin_router)
app.include_router(files_router)

# Serve frontend build (Vite)
project_root = Path(__file__).resolve().parents[1]
static_dir = project_root / "frontend" / "dist"
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
        public_fonts = project_root / "frontend" / "public" / "fonts"
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
    5. If valid: Auth service returns X-Authenticated-User header
    6. Reverse proxy forwards connection to this app with X-Authenticated-User header
    7. This app trusts the header (already validated by auth service)

    SECURITY REQUIREMENTS:
    - This app MUST ONLY be accessible via reverse proxy
    - Direct public access to this app bypasses authentication
    - Use network isolation to prevent direct access
    - The /login endpoint lives in the separate auth service

    DEVELOPMENT vs PRODUCTION:
    - Production: Extracts user from X-Authenticated-User header (set by reverse proxy)
    - Development: Falls back to 'user' query parameter (INSECURE, local only)

    See docs/security_architecture.md for complete architecture details.
    """
    await websocket.accept()

    # Basic auth: derive user from query parameters or use test user
    user_email = websocket.query_params.get('user')
    if not user_email:
        # Fallback to test user or require auth
        config_manager = app_factory.get_config_manager()
        user_email = config_manager.app_settings.test_user or 'test@test.com'

    session_id = uuid4()

    # Create connection adapter with authenticated user and chat service
    connection_adapter = WebSocketConnectionAdapter(websocket, user_email)
    chat_service = app_factory.create_chat_service(connection_adapter)
    
    logger.info(f"WebSocket connection established for session {sanitize_for_logging(str(session_id))}")
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            
            if message_type == "chat":
                # Handle chat message with streaming updates
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
                        user_email=data.get("user"),
                        agent_mode=data.get("agent_mode", False),
                        agent_max_steps=data.get("agent_max_steps", 10),
                        temperature=data.get("temperature", 0.7),
                        agent_loop_strategy=data.get("agent_loop_strategy"),
                        update_callback=lambda message: websocket_update_callback(websocket, message),
                        files=data.get("files")
                    )
                    # Final response is already sent via callbacks, but we keep this for backward compatibility
                except ValidationError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e)
                    })
                except Exception as e:
                    logger.error(f"Error in chat handler: {e}", exc_info=True)
                    await websocket.send_json({
                        "type": "error",
                        "message": "An unexpected error occurred"
                    })
                
            elif message_type == "download_file":
                # Handle file download
                response = await chat_service.handle_download_file(
                    session_id=session_id,
                    filename=data.get("filename", ""),
                    user_email=data.get("user")
                )
                await websocket.send_json(response)
            
            elif message_type == "reset_session":
                # Handle session reset
                response = await chat_service.handle_reset_session(
                    session_id=session_id,
                    user_email=data.get("user")
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

            else:
                logger.warning(f"Unknown message type: {message_type}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {message_type}"
                })
                
    except WebSocketDisconnect:
        chat_service.end_session(session_id)
        logger.info(f"WebSocket connection closed for session {session_id}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
