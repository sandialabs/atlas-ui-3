"""FastMCP client for connecting to MCP servers and managing tools."""

import asyncio
import contextvars
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.metrics_logger import log_metric
from atlas.domain.messages.models import ToolCall, ToolResult
from atlas.modules.config import config_manager
from atlas.modules.config.config_manager import resolve_env_var
from atlas.modules.mcp_tools.token_storage import AuthenticationRequiredException

logger = logging.getLogger(__name__)

# Type alias for log callback function
LogCallback = Callable[[str, str, str, Dict[str, Any]], Awaitable[None]]


class _ElicitationRoutingContext:
    def __init__(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ):
        self.server_name = server_name
        self.tool_call = tool_call
        self.update_cb = update_cb

# Context-local override used to route MCP logs to the *current* request/session.
# This prevents cross-user log leakage when MCPToolManager is shared across connections.
_ACTIVE_LOG_CALLBACK: contextvars.ContextVar[Optional[LogCallback]] = contextvars.ContextVar(
    "mcp_active_log_callback",
    default=None,
)

# Dictionary-based routing for elicitation so a shared Client can still deliver
# elicitation requests to the correct user's WebSocket.
# Key: (server_name, tool_call_id) tuple to avoid collisions with concurrent tool calls
# Note: Cannot use contextvars.ContextVar because MCP receive loop runs in a different task
_ELICITATION_ROUTING: Dict[tuple, _ElicitationRoutingContext] = {}

# Dictionary-based routing for sampling requests (similar to elicitation)
# Key: (server_name, tool_call_id) tuple to avoid collisions with concurrent tool calls
_SAMPLING_ROUTING: Dict[tuple, "_SamplingRoutingContext"] = {}


class _SamplingRoutingContext:
    """Context for routing sampling requests to the correct tool execution."""
    def __init__(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ):
        self.server_name = server_name
        self.tool_call = tool_call
        self.update_cb = update_cb

# Mapping from MCP log levels to Python logging levels
MCP_TO_PYTHON_LOG_LEVEL = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "alert": logging.CRITICAL,
    "critical": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


class MCPToolManager:
    """Manager for MCP servers and their tools.

    Default config path now points to config/ (or env override APP_CONFIG_DIR) with package fallback.

    Supports:
    - Hot-reloading configuration from disk via reload_config()
    - Tracking failed server connections for retry
    - Auto-reconnect with exponential backoff (when feature flag is enabled)
    """

    def __init__(self, config_path: Optional[str] = None, log_callback: Optional[LogCallback] = None):
        if config_path is None:
            # Use config manager to get config path
            app_settings = config_manager.app_settings
            config_root = Path(app_settings.app_config_dir)

            # If relative, resolve from project root
            if not config_root.is_absolute():
                atlas_root = Path(__file__).parent.parent.parent
                project_root = atlas_root.parent
                config_root = project_root / config_root

            candidate = config_root / "mcp.json"
            if not candidate.exists():
                # Fall back to package defaults
                atlas_root = Path(__file__).parent.parent.parent
                candidate = atlas_root / "config" / "mcp.json"
            self.config_path = str(candidate)
            # Use default config manager when no path specified
            mcp_config = config_manager.mcp_config
            self.servers_config = {name: server.model_dump() for name, server in mcp_config.servers.items()}
        else:
            # Load config from the specified path
            self.config_path = config_path
            config_file = Path(config_path)
            if config_file.exists():
                from atlas.modules.config.config_manager import MCPConfig
                data = json.loads(config_file.read_text())
                # Convert flat structure to nested structure for Pydantic
                servers_data = {"servers": data}
                mcp_config = MCPConfig(**servers_data)
                self.servers_config = {name: server.model_dump() for name, server in mcp_config.servers.items()}
            else:
                logger.warning(f"Custom config path specified but file not found: {config_path}")
                self.servers_config = {}
        self.clients = {}
        self.available_tools = {}
        self.available_prompts = {}

        # Track failed servers for reconnection with backoff
        self._failed_servers: Dict[str, Dict[str, Any]] = {}
        # {server_name: {"last_attempt": timestamp, "attempt_count": int, "error": str}}

        # Reconnect task reference (used by auto-reconnect background task)
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_running = False

        # Default log callback (used when no request-scoped callback is active).
        # Signature: (server_name, level, message, extra_data) -> None
        self._default_log_callback = log_callback

        # Get configured log level for filtering
        self._min_log_level = self._get_min_log_level()

        # Per-user client cache for servers requiring user-specific authentication
        # Key: (user_email, server_name), Value: FastMCP Client instance
        self._user_clients: Dict[tuple, Client] = {}
        self._user_clients_lock = asyncio.Lock()

    def _get_min_log_level(self) -> int:
        """Get the minimum log level from environment or config."""
        try:
            app_settings = config_manager.app_settings
            raw_level_name = getattr(app_settings, "log_level", None)
            if not isinstance(raw_level_name, str):
                raise TypeError("log_level must be a string")
            level_name = raw_level_name.upper()
        except Exception:
            level_name = os.getenv("LOG_LEVEL", "INFO").upper()

        level = getattr(logging, level_name, None)
        return level if isinstance(level, int) else logging.INFO

    def _create_log_handler(self, server_name: str):
        """Create a log handler for an MCP server.

        This handler forwards MCP server logs to the backend logger and optionally to the UI.
        Logs are filtered based on the configured LOG_LEVEL.

        Args:
            server_name: Name of the MCP server

        Returns:
            An async function that handles LogMessage objects from fastmcp
        """
        async def log_handler(message) -> None:
            """Handle log messages from MCP server."""
            try:
                # Import here to avoid circular dependency

                # Handle both LogMessage objects and dict-like structures
                if hasattr(message, 'level'):
                    log_level_str = message.level.lower()
                    log_data = message.data if hasattr(message, 'data') else {}
                else:
                    # Fallback for dict-like messages
                    log_level_str = message.get('level', 'info').lower()
                    log_data = message.get('data', {})

                msg = log_data.get('msg', '') if isinstance(log_data, dict) else str(log_data)
                extra = log_data.get('extra', {}) if isinstance(log_data, dict) else {}

                # Convert MCP log level to Python logging level
                python_log_level = MCP_TO_PYTHON_LOG_LEVEL.get(log_level_str, logging.INFO)

                # Filter based on configured minimum log level
                if python_log_level < self._min_log_level:
                    return

                # Backend log noise reduction: tool servers can be very chatty at INFO.
                # Keep their INFO messages available at LOG_LEVEL=DEBUG, but avoid flooding
                # app logs at LOG_LEVEL=INFO. Warnings/errors still surface at INFO.
                backend_log_level = python_log_level if python_log_level >= logging.WARNING else logging.DEBUG

                # Log to backend logger with server context
                logger.log(
                    backend_log_level,
                    f"[MCP:{sanitize_for_logging(server_name)}] {sanitize_for_logging(msg)}",
                    extra={"mcp_server": server_name, "mcp_extra": extra}
                )

                # Forward to the active (request-scoped) callback when present,
                # otherwise fall back to the default callback.
                callback = _ACTIVE_LOG_CALLBACK.get() or self._default_log_callback
                if callback is not None:
                    await callback(server_name, log_level_str, msg, extra)

            except Exception as e:
                logger.warning(f"Error handling log from MCP server {server_name}: {e}")

        return log_handler

    def set_log_callback(self, callback: Optional[LogCallback]) -> None:
        """Set or update the log callback for forwarding MCP server logs to UI.

        Args:
            callback: Async function that receives (server_name, level, message, extra_data)
        """
        self._default_log_callback = callback

    @asynccontextmanager
    async def _use_log_callback(self, callback: Optional[LogCallback]) -> AsyncIterator[None]:
        """Temporarily set a request-scoped log callback.

        This is used to bind MCP server logs to the current tool execution so they
        are forwarded only to the correct user's WebSocket connection.
        """
        token = _ACTIVE_LOG_CALLBACK.set(callback)
        try:
            yield
        finally:
            _ACTIVE_LOG_CALLBACK.reset(token)

    @asynccontextmanager
    async def _use_elicitation_context(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ) -> AsyncIterator[None]:
        """
        Set up elicitation routing for a tool call.
        Uses dictionary-based routing (not contextvars) because MCP receive loop runs in a different task.
        Key is (server_name, tool_call.id) to avoid collisions with concurrent tool calls.
        """
        routing = _ElicitationRoutingContext(server_name, tool_call, update_cb)
        routing_key = (server_name, tool_call.id)
        _ELICITATION_ROUTING[routing_key] = routing
        try:
            yield
        finally:
            _ELICITATION_ROUTING.pop(routing_key, None)

    def _create_elicitation_handler(self, server_name: str):
        """
        Create an elicitation handler for a specific MCP server.

        Returns a handler function that captures the server_name,
        allowing dictionary-based routing that works across async tasks.
        """
        async def handler(message, response_type, params, _context):
            """Per-server elicitation handler with captured server_name."""
            from fastmcp.client.elicitation import ElicitResult
            from mcp.types import ElicitRequestFormParams

            # Find routing context for this server (keyed by (server_name, tool_call_id))
            routing = None
            for (srv, _tcid), ctx in _ELICITATION_ROUTING.items():
                if srv == server_name:
                    routing = ctx
                    break
            if routing is None:
                logger.warning(
                    f"Elicitation request for server '{server_name}' but no routing context - "
                    f"elicitation cancelled. Message: {message[:50]}..."
                )
                return ElicitResult(action="cancel", content=None)
            if routing.update_cb is None:
                logger.warning(
                    f"Elicitation request for server '{server_name}', tool '{routing.tool_call.name}' "
                    f"but update_cb is None - elicitation cancelled. Message: {message[:50]}..."
                )
                return ElicitResult(action="cancel", content=None)

            response_schema: Dict[str, Any] = {}
            if isinstance(params, ElicitRequestFormParams):
                response_schema = params.requestedSchema or {}

            try:
                import uuid

                from atlas.application.chat.elicitation_manager import get_elicitation_manager

                elicitation_id = str(uuid.uuid4())
                elicitation_manager = get_elicitation_manager()

                request = elicitation_manager.create_elicitation_request(
                    elicitation_id=elicitation_id,
                    tool_call_id=routing.tool_call.id,
                    tool_name=routing.tool_call.name,
                    message=message,
                    response_schema=response_schema,
                )

                logger.debug(f"Sending elicitation_request to frontend for server '{server_name}'")
                await routing.update_cb(
                    {
                        "type": "elicitation_request",
                        "elicitation_id": elicitation_id,
                        "tool_call_id": routing.tool_call.id,
                        "tool_name": routing.tool_call.name,
                        "message": message,
                        "response_schema": response_schema,
                    }
                )

                try:
                    response = await request.wait_for_response(timeout=300.0)
                finally:
                    elicitation_manager.cleanup_request(elicitation_id)

                action = response.get("action", "cancel")
                data = response.get("data")

                if action != "accept":
                    return ElicitResult(action=action, content=None)

                # Approval-only elicitation (response_type=None) must return an empty object.
                # Some UIs send placeholder payloads like {'none': ''}; don't forward them.
                if response_type is None:
                    return ElicitResult(action="accept", content={})

                if data is None:
                    return ElicitResult(action="accept", content=None)

                # FastMCP requires elicitation response content to be a JSON object.
                if not isinstance(data, dict):
                    props: Dict[str, Any] = {}
                    if isinstance(response_schema, dict):
                        props = response_schema.get("properties") or {}
                    if list(props.keys()) == ["value"]:
                        data = {"value": data}
                    else:
                        data = {"value": data}

                return ElicitResult(action="accept", content=data)

            except asyncio.TimeoutError:
                logger.warning(f"Elicitation timeout for server '{server_name}'")
                return ElicitResult(action="cancel", content=None)
            except Exception as e:
                logger.error(f"Error handling elicitation for server '{server_name}': {e}", exc_info=True)
                return ElicitResult(action="cancel", content=None)

        return handler

    @asynccontextmanager
    async def _use_sampling_context(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ) -> AsyncIterator[None]:
        """
        Set up sampling routing for a tool call.
        Uses dictionary-based routing (not contextvars) because MCP receive loop runs in a different task.
        Key is (server_name, tool_call.id) to avoid collisions with concurrent tool calls.
        """
        routing = _SamplingRoutingContext(server_name, tool_call, update_cb)
        routing_key = (server_name, tool_call.id)
        _SAMPLING_ROUTING[routing_key] = routing
        try:
            yield
        finally:
            _SAMPLING_ROUTING.pop(routing_key, None)

    def _create_sampling_handler(self, server_name: str):
        """
        Create a sampling handler for a specific MCP server.

        This handler intercepts MCP sampling requests and routes them to the LLM.
        Returns a handler function that captures the server_name for routing.
        """
        async def handler(messages, params=None, context=None):
            """Per-server sampling handler with captured server_name."""
            from mcp.types import CreateMessageResult, SamplingMessage, TextContent

            # Find routing context for this server (keyed by (server_name, tool_call_id))
            routing = None
            for (srv, _tcid), ctx in _SAMPLING_ROUTING.items():
                if srv == server_name:
                    routing = ctx
                    break
            if routing is None:
                logger.warning(
                    f"Sampling request for server '{server_name}' but no routing context - "
                    f"sampling cancelled."
                )
                raise Exception("No routing context for sampling request")

            try:
                message_dicts = []
                for msg in messages:
                    if isinstance(msg, SamplingMessage):
                        text = ""
                        if isinstance(msg.content, TextContent):
                            text = msg.content.text
                        elif isinstance(msg.content, list):
                            for item in msg.content:
                                if isinstance(item, TextContent):
                                    text += item.text
                        else:
                            text = str(msg.content)
                        message_dicts.append({
                            "role": msg.role,
                            "content": text
                        })
                    elif isinstance(msg, str):
                        message_dicts.append({
                            "role": "user",
                            "content": msg
                        })
                    else:
                        message_dicts.append(msg)

                system_prompt = getattr(params, 'systemPrompt', None) if params else None
                temperature = getattr(params, 'temperature', None) if params else None
                max_tokens = getattr(params, 'maxTokens', 512) if params else 512
                model_preferences_raw = getattr(params, 'modelPreferences', None) if params else None

                model_preferences = None
                if model_preferences_raw:
                    if isinstance(model_preferences_raw, str):
                        model_preferences = [model_preferences_raw]
                    elif isinstance(model_preferences_raw, list):
                        model_preferences = model_preferences_raw

                if system_prompt:
                    message_dicts.insert(0, {
                        "role": "system",
                        "content": system_prompt
                    })

                logger.info(
                    f"Sampling request from server '{server_name}' tool '{routing.tool_call.name}': "
                    f"{len(message_dicts)} messages, temperature={temperature}, max_tokens={max_tokens}"
                )

                from atlas.modules.config import config_manager
                from atlas.modules.llm.litellm_caller import LiteLLMCaller

                llm_caller = LiteLLMCaller()

                llm_config = config_manager.llm_config
                model_name = None

                if model_preferences:
                    for pref in model_preferences:
                        if pref in llm_config.models:
                            model_name = pref
                            break
                        for name, model_config in llm_config.models.items():
                            if model_config.model_name == pref:
                                model_name = name
                                break
                        if model_name:
                            break

                if not model_name:
                    model_name = next(iter(llm_config.models.keys()))

                logger.debug(
                    f"Using model '{model_name}' for sampling "
                    f"(preferences: {model_preferences})"
                )

                response = await llm_caller.call_plain(
                    model_name=model_name,
                    messages=message_dicts,
                    temperature=temperature,
                    max_tokens=max_tokens
                )

                logger.info(
                    f"Sampling completed for server '{server_name}': "
                    f"response_length={len(response) if response else 0}"
                )

                return CreateMessageResult(
                    role="assistant",
                    content=TextContent(type="text", text=response),
                    model=model_name
                )

            except Exception as e:
                logger.error(f"Error handling sampling for server '{server_name}': {e}", exc_info=True)
                raise

        return handler

    def reload_config(self) -> Dict[str, Any]:
        """Reload MCP server configuration from disk.

        This re-reads the mcp.json configuration file and updates servers_config.
        Call initialize_clients() and discover_tools()/discover_prompts() afterward
        to apply the changes.

        Returns:
            Dict with previous and new server lists for comparison
        """
        previous_servers = set(self.servers_config.keys())

        # Reload from config manager (which reads from disk)
        new_mcp_config = config_manager.reload_mcp_config()
        self.servers_config = {
            name: server.model_dump()
            for name, server in new_mcp_config.servers.items()
        }

        new_servers = set(self.servers_config.keys())

        # Clear failed servers tracking for removed servers
        removed_servers = previous_servers - new_servers
        for server_name in removed_servers:
            self._failed_servers.pop(server_name, None)

        added_servers = new_servers - previous_servers
        unchanged_servers = previous_servers & new_servers

        logger.info(
            f"MCP config reloaded: added={list(added_servers)}, "
            f"removed={list(removed_servers)}, unchanged={list(unchanged_servers)}"
        )

        return {
            "previous_servers": list(previous_servers),
            "new_servers": list(new_servers),
            "added": list(added_servers),
            "removed": list(removed_servers),
            "unchanged": list(unchanged_servers)
        }

    def get_failed_servers(self) -> Dict[str, Dict[str, Any]]:
        """Get information about servers that failed to connect.

        Returns:
            Dict mapping server name to failure info including last_attempt time,
            attempt_count, and error message.
        """
        return dict(self._failed_servers)

    def _record_server_failure(self, server_name: str, error: str) -> None:
        """Record a server connection failure for tracking."""
        if server_name in self._failed_servers:
            self._failed_servers[server_name]["attempt_count"] += 1
            self._failed_servers[server_name]["last_attempt"] = time.time()
            self._failed_servers[server_name]["error"] = error
        else:
            self._failed_servers[server_name] = {
                "last_attempt": time.time(),
                "attempt_count": 1,
                "error": error
            }

    def _clear_server_failure(self, server_name: str) -> None:
        """Clear failure tracking for a server after successful connection."""
        self._failed_servers.pop(server_name, None)

    def _calculate_backoff_delay(self, attempt_count: int) -> float:
        """Calculate exponential backoff delay for reconnection attempts.

        Uses settings from config_manager for base interval, max interval, and multiplier.
        """
        app_settings = config_manager.app_settings
        base_interval = app_settings.mcp_reconnect_interval
        max_interval = app_settings.mcp_reconnect_max_interval
        multiplier = app_settings.mcp_reconnect_backoff_multiplier

        delay = base_interval * (multiplier ** (attempt_count - 1))
        return min(delay, max_interval)


    def _determine_transport_type(self, config: Dict[str, Any]) -> str:
        """Determine the transport type for an MCP server configuration.

        Priority order:
        1. Explicit 'transport' field (highest priority)
        2. Auto-detection from command
        3. Auto-detection from URL if it has protocol
        4. Fallback to 'type' field (backward compatibility)
        """
        # 1. Explicit transport field takes highest priority
        if config.get("transport"):
            logger.debug(f"Using explicit transport: {config['transport']}")
            return config["transport"]

        # 2. Auto-detect from command (takes priority over URL)
        if config.get("command"):
            logger.debug("Auto-detected STDIO transport from command")
            return "stdio"

        # 3. Auto-detect from URL if it has protocol
        url = config.get("url")
        if url:
            if url.startswith(("http://", "https://")):
                if url.endswith("/sse"):
                    logger.debug(f"Auto-detected SSE transport from URL: {url}")
                    return "sse"
                else:
                    logger.debug(f"Auto-detected HTTP transport from URL: {url}")
                    return "http"
            else:
                # URL without protocol - check if type field specifies transport
                transport_type = config.get("type", "stdio")
                if transport_type in ["http", "sse"]:
                    logger.debug(f"Using type field '{transport_type}' for URL without protocol: {url}")
                    return transport_type
                else:
                    logger.debug(f"URL without protocol, defaulting to HTTP: {url}")
                    return "http"

        # 4. Fallback to type field (backward compatibility)
        transport_type = config.get("type", "stdio")
        logger.debug(f"Using fallback transport type: {transport_type}")
        return transport_type

    async def _initialize_single_client(self, server_name: str, config: Dict[str, Any]) -> Optional[Client]:
        """Initialize a single MCP client. Returns None if initialization fails."""
        safe_server_name = sanitize_for_logging(server_name)
        # Keep INFO logs concise; config/transport details can be very verbose.
        logger.info("Initializing MCP client for server '%s'", safe_server_name)
        logger.debug("Server config for '%s': %s", safe_server_name, sanitize_for_logging(str(config)))
        try:
            transport_type = self._determine_transport_type(config)
            logger.debug("Determined transport type for %s: %s", safe_server_name, transport_type)

            if transport_type in ["http", "sse"]:
                # HTTP/SSE MCP server
                url = config.get("url")
                if not url:
                    logger.error(f"No URL provided for HTTP/SSE server: {server_name}")
                    return None

                # Ensure URL has protocol for FastMCP client
                if not url.startswith(("http://", "https://")):
                    url = f"http://{url}"
                    logger.debug(f"Added http:// protocol to URL: {url}")

                raw_token = config.get("auth_token")
                try:
                    token = resolve_env_var(raw_token)  # Resolve ${ENV_VAR} if present
                except ValueError as e:
                    logger.error(f"Failed to resolve auth_token for {server_name}: {e}")
                    return None  # Skip this server

                # Create log handler for this server
                log_handler = self._create_log_handler(server_name)

                if transport_type == "sse":
                    # Use explicit SSE transport
                    logger.debug(f"Creating SSE client for {server_name} at {url}")
                    client = Client(
                        url,
                        auth=token,
                        log_handler=log_handler,
                        elicitation_handler=self._create_elicitation_handler(server_name),
                        sampling_handler=self._create_sampling_handler(server_name),
                    )
                else:
                    # Use HTTP transport (StreamableHttp)
                    logger.debug(f"Creating HTTP client for {server_name} at {url}")
                    client = Client(
                        url,
                        auth=token,
                        log_handler=log_handler,
                        elicitation_handler=self._create_elicitation_handler(server_name),
                        sampling_handler=self._create_sampling_handler(server_name),
                    )

                logger.info(f"Created {transport_type.upper()} MCP client for {server_name}")
                return client

            elif transport_type == "stdio":
                # STDIO MCP server
                command = config.get("command")
                logger.debug("STDIO transport command for %s: %s", safe_server_name, command)
                if command:
                    # Ensure MCP stdio servers run under the same interpreter as the backend.
                    # In dev containers, PATH `python` may not have required deps.
                    if command[0] in {"python", "python3"}:
                        command = [sys.executable, *command[1:]]

                    # Custom command specified
                    cwd = config.get("cwd")
                    env = config.get("env")
                    logger.debug("Working directory specified for %s: %s", safe_server_name, cwd)

                    # Resolve environment variables in env dict
                    resolved_env = None
                    if env is not None:
                        resolved_env = {}
                        for key, value in env.items():
                            try:
                                resolved_env[key] = resolve_env_var(value)
                                logger.debug(f"Resolved env var {key} for {server_name}")
                            except ValueError as e:
                                logger.error(f"Failed to resolve env var {key} for {server_name}: {e}")
                                return None  # Skip this server if env var resolution fails
                        logger.debug("Environment variables specified for %s: %s", safe_server_name, list(resolved_env.keys()))

                    # Create log handler for this server
                    log_handler = self._create_log_handler(server_name)

                    if cwd:
                        # Convert relative path to absolute path from project root
                        if not os.path.isabs(cwd):
                            # Get project root (3 levels up from client.py)
                            # client.py is at: /workspaces/atlas-ui-3-11/backend/modules/mcp_tools/client.py
                            # project root is: /workspaces/atlas-ui-3-11
                            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                            cwd = os.path.join(project_root, cwd)
                            logger.debug("Converted relative cwd to absolute for %s: %s", safe_server_name, cwd)

                        if os.path.exists(cwd):
                            logger.debug("Working directory exists for %s: %s", safe_server_name, cwd)
                            logger.debug("Creating STDIO client for %s with command=%s cwd=%s", safe_server_name, command, cwd)
                            from fastmcp.client.transports import StdioTransport
                            transport = StdioTransport(command=command[0], args=command[1:], cwd=cwd, env=resolved_env)
                            client = Client(
                                transport,
                                log_handler=log_handler,
                                elicitation_handler=self._create_elicitation_handler(server_name),
                                sampling_handler=self._create_sampling_handler(server_name),
                            )
                            logger.info(f"Successfully created STDIO MCP client for {server_name} with custom command and cwd")
                            return client
                        else:
                            logger.error(f"Working directory does not exist: {cwd}")
                            return None
                    else:
                        logger.debug("No cwd specified for %s; creating STDIO client with command=%s", safe_server_name, command)
                        from fastmcp.client.transports import StdioTransport
                        transport = StdioTransport(command=command[0], args=command[1:], env=resolved_env)
                        client = Client(
                            transport,
                            log_handler=log_handler,
                            elicitation_handler=self._create_elicitation_handler(server_name),
                            sampling_handler=self._create_sampling_handler(server_name),
                        )
                        logger.info(f"Successfully created STDIO MCP client for {server_name} with custom command")
                        return client
                else:
                    # Fallback to old behavior for backward compatibility
                    server_path = f"mcp/{server_name}/main.py"
                    logger.debug(f"Attempting to initialize {server_name} at path: {server_path}")
                    if os.path.exists(server_path):
                        logger.debug(f"Server script exists for {server_name}, creating client...")
                        log_handler = self._create_log_handler(server_name)
                        client = Client(
                            server_path,
                            log_handler=log_handler,
                            elicitation_handler=self._create_elicitation_handler(server_name),
                            sampling_handler=self._create_sampling_handler(server_name),
                        )  # Client auto-detects STDIO transport from .py file
                        logger.info(f"Created MCP client for {server_name}")
                        logger.debug(f"Successfully created client for {server_name}")
                        return client
                    else:
                        logger.error(f"MCP server script not found: {server_path}", exc_info=True)
                        return None
            else:
                logger.error(f"Unsupported transport type '{transport_type}' for server: {server_name}")
                return None

        except Exception as e:
            # Targeted debugging for MCP startup errors
            error_type = type(e).__name__
            logger.error(f"Error creating client for {server_name}: {error_type}: {e}")

            # Provide specific debugging information based on error type and config
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                if transport_type in ["http", "sse"]:
                    logger.error(f"DEBUG: Connection failed for HTTP/SSE server '{server_name}'")
                    logger.error(f"    → URL: {config.get('url', 'Not specified')}")
                    logger.error(f"    → Transport: {transport_type}")
                    logger.error("    → Check if server is running and accessible")
                else:
                    logger.error(f"DEBUG: STDIO connection failed for server '{server_name}'")
                    logger.error(f"    → Command: {config.get('command', 'Not specified')}")
                    logger.error(f"    → CWD: {config.get('cwd', 'Not specified')}")
                    logger.error("    → Check if command exists and is executable")

            elif "timeout" in str(e).lower():
                logger.error(f"DEBUG: Timeout connecting to server '{server_name}'")
                logger.error("    → Server may be slow to start or overloaded")
                logger.error("    → Consider increasing timeout or checking server health")

            elif "permission" in str(e).lower() or "access" in str(e).lower():
                logger.error(f"DEBUG: Permission error for server '{server_name}'")
                if config.get('cwd'):
                    logger.error(f"    → Check directory permissions: {config.get('cwd')}")
                if config.get('command'):
                    logger.error(f"    → Check executable permissions: {config.get('command')}")

            elif "module" in str(e).lower() or "import" in str(e).lower():
                logger.error(f"DEBUG: Import/module error for server '{server_name}'")
                logger.error("    → Check if required dependencies are installed")
                logger.error("    → Check Python path and virtual environment")

            elif "json" in str(e).lower() or "decode" in str(e).lower():
                logger.error(f"DEBUG: JSON/protocol error for server '{server_name}'")
                logger.error("    → Server may not be MCP-compatible")
                logger.error("    → Check server output format")

            else:
                # Generic debugging info
                logger.error(f"DEBUG: Generic error for server '{server_name}'")
                logger.error(f"    → Config: {config}")
                logger.error(f"    → Transport type: {transport_type}")

            # Always show the full traceback in debug mode
            logger.debug(f"Full traceback for {server_name}:", exc_info=True)
            return None

    async def initialize_clients(self):
        """Initialize FastMCP clients for all configured servers in parallel."""
        logger.info("Starting MCP client initialization for %d servers", len(self.servers_config))
        logger.debug("MCP servers to initialize: %s", list(self.servers_config.keys()))

        # Create tasks for parallel initialization
        tasks = [
            self._initialize_single_client(server_name, config)
            for server_name, config in self.servers_config.items()
        ]
        server_names = list(self.servers_config.keys())

        # Run all initialization tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and store successful clients
        for server_name, result in zip(server_names, results):
            if isinstance(result, Exception):
                error_msg = f"{type(result).__name__}: {result}"
                logger.error(f"Exception during client initialization for {server_name}: {error_msg}", exc_info=True)
                self._record_server_failure(server_name, error_msg)
            elif result is not None:
                is_new = server_name not in self.clients
                self.clients[server_name] = result
                self._clear_server_failure(server_name)
                logger.info(f"Successfully initialized client for {server_name}")
                if is_new:
                    print(f"  MCP connected: {server_name}", file=__import__('sys').stderr)
            else:
                self._record_server_failure(server_name, "Initialization returned None")
                logger.warning(f"Failed to initialize client for {server_name}")

        failed_servers = sorted(set(self.servers_config.keys()) - set(self.clients.keys()))
        logger.info(
            "MCP client initialization complete: %d/%d connected (%d failed)",
            len(self.clients),
            len(self.servers_config),
            len(failed_servers),
        )
        logger.debug("MCP clients initialized: %s", list(self.clients.keys()))
        logger.debug("MCP clients failed to initialize: %s", failed_servers)

    async def reconnect_failed_servers(self, force: bool = False) -> Dict[str, Any]:
        """Attempt to reconnect to servers that previously failed.

        When ``force`` is False (default), this respects exponential backoff and
        only attempts servers whose backoff delay has elapsed. When ``force`` is
        True, backoff delays are ignored and all currently failed servers are
        attempted immediately. The admin `/admin/mcp/reconnect` endpoint uses
        ``force=True`` to provide an on-demand retry button.

        Returns:
            Dict with reconnection results including newly connected, still
            failed, and skipped servers due to backoff.
        """
        if not self._failed_servers:
            return {
                "attempted": [],
                "reconnected": [],
                "still_failed": [],
                "skipped_backoff": []
            }

        current_time = time.time()
        attempted = []
        reconnected = []
        still_failed = []
        skipped_backoff = []

        for server_name, failure_info in list(self._failed_servers.items()):
            # Skip if server is no longer in config
            if server_name not in self.servers_config:
                self._clear_server_failure(server_name)
                continue

            # Skip if already connected
            if server_name in self.clients:
                self._clear_server_failure(server_name)
                continue

            # Check backoff delay unless this is a forced reconnect
            backoff_delay = self._calculate_backoff_delay(failure_info["attempt_count"])
            time_since_last = current_time - failure_info["last_attempt"]

            if not force and time_since_last < backoff_delay:
                skipped_backoff.append({
                    "server": server_name,
                    "wait_remaining": backoff_delay - time_since_last,
                    "attempt_count": failure_info["attempt_count"]
                })
                continue

            # Attempt reconnection
            attempted.append(server_name)
            config = self.servers_config[server_name]

            try:
                client = await self._initialize_single_client(server_name, config)
                if client is not None:
                    self.clients[server_name] = client
                    self._clear_server_failure(server_name)
                    reconnected.append(server_name)
                    logger.info(f"Successfully reconnected to MCP server: {server_name}")

                    # Discover tools and prompts for the reconnected server
                    await self._discover_and_register_server(server_name, client)
                else:
                    self._record_server_failure(server_name, "Reconnection returned None")
                    still_failed.append(server_name)
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                self._record_server_failure(server_name, error_msg)
                still_failed.append(server_name)
                logger.warning(f"Failed to reconnect to MCP server {server_name}: {error_msg}")

        return {
            "attempted": attempted,
            "reconnected": reconnected,
            "still_failed": still_failed,
            "skipped_backoff": skipped_backoff
        }

    async def _discover_and_register_server(self, server_name: str, client: Client) -> None:
        """Discover tools and prompts for a single server and register them."""
        try:
            # Discover tools
            tool_data = await self._discover_tools_for_server(server_name, client)
            self.available_tools[server_name] = tool_data

            # Update tool index
            if hasattr(self, "_tool_index"):
                for tool in tool_data.get('tools', []):
                    full_name = f"{server_name}_{tool.name}"
                    self._tool_index[full_name] = {
                        'server': server_name,
                        'tool': tool
                    }

            # Discover prompts
            prompt_data = await self._discover_prompts_for_server(server_name, client)
            self.available_prompts[server_name] = prompt_data

            logger.info(
                f"Registered server {server_name}: "
                f"{len(tool_data.get('tools', []))} tools, "
                f"{len(prompt_data.get('prompts', []))} prompts"
            )
        except Exception as e:
            logger.error(f"Error discovering tools/prompts for {server_name}: {e}")

    async def start_auto_reconnect(self) -> None:
        """Start the background auto-reconnect task.

        This task periodically attempts to reconnect to failed MCP servers
        using exponential backoff. Only runs if FEATURE_MCP_AUTO_RECONNECT_ENABLED is true.
        """
        app_settings = config_manager.app_settings
        if not app_settings.feature_mcp_auto_reconnect_enabled:
            logger.info("MCP auto-reconnect is disabled (FEATURE_MCP_AUTO_RECONNECT_ENABLED=false)")
            return

        if self._reconnect_running:
            logger.warning("Auto-reconnect task is already running")
            return

        self._reconnect_running = True
        self._reconnect_task = asyncio.create_task(self._auto_reconnect_loop())
        logger.info("Started MCP auto-reconnect background task")

    async def stop_auto_reconnect(self) -> None:
        """Stop the background auto-reconnect task."""
        self._reconnect_running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        logger.info("Stopped MCP auto-reconnect background task")

    async def _auto_reconnect_loop(self) -> None:
        """Background loop that periodically attempts to reconnect failed servers."""
        app_settings = config_manager.app_settings
        base_interval = app_settings.mcp_reconnect_interval

        while self._reconnect_running:
            try:
                await asyncio.sleep(base_interval)

                if not self._failed_servers:
                    continue

                logger.debug(
                    f"Auto-reconnect: checking {len(self._failed_servers)} failed servers"
                )
                result = await self.reconnect_failed_servers()

                if result["reconnected"]:
                    logger.info(
                        f"Auto-reconnect: successfully reconnected {len(result['reconnected'])} servers: "
                        f"{result['reconnected']}"
                    )
                if result["still_failed"]:
                    logger.debug(
                        f"Auto-reconnect: {len(result['still_failed'])} servers still failed"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-reconnect loop: {e}", exc_info=True)
                await asyncio.sleep(base_interval)  # Wait before retrying

    async def _discover_tools_for_server(self, server_name: str, client: Client) -> Dict[str, Any]:
        """Discover tools for a single server. Returns server tools data."""
        safe_server_name = sanitize_for_logging(server_name)
        server_config = self.servers_config.get(server_name, {})
        safe_config = sanitize_for_logging(str(server_config))
        discovery_timeout = config_manager.app_settings.mcp_discovery_timeout
        logger.debug("Tool discovery: starting for server '%s'", safe_server_name)
        logger.debug("Server config (sanitized): %s", safe_config)
        try:
            logger.debug("Opening client connection for %s", safe_server_name)
            async with client:
                logger.debug("Client connected for %s; listing tools", safe_server_name)
                tools = await asyncio.wait_for(client.list_tools(), timeout=discovery_timeout)
                logger.debug("Got %d tools from %s: %s", len(tools), safe_server_name, [tool.name for tool in tools])

                # Log detailed tool information
                for i, tool in enumerate(tools):
                    logger.debug(
                        "  Tool %d: name='%s', description='%s'",
                        i + 1,
                        tool.name,
                        getattr(tool, 'description', 'No description'),
                    )

                server_data = {
                    'tools': tools,
                    'config': self.servers_config[server_name]
                }
                logger.debug("Stored %d tools for %s", len(tools), safe_server_name)
                return server_data
        except Exception as e:
            error_type = type(e).__name__
            error_msg = sanitize_for_logging(str(e))
            logger.error(f"TOOL DISCOVERY FAILED for '{safe_server_name}': {error_type}: {error_msg}")

            # Targeted debugging for tool discovery errors
            error_lower = str(e).lower()
            if "connection" in error_lower or "refused" in error_lower:
                logger.error(f"DEBUG: Connection lost during tool discovery for '{safe_server_name}'")
                logger.error("    → Server may have crashed or disconnected")
                logger.error("    → Check server logs for startup errors")
                # Check if this is an HTTPS/SSL issue
                if "ssl" in error_lower or "certificate" in error_lower or "https" in error_lower:
                    logger.error("    → SSL/HTTPS error detected")
                    logger.error("    → On Windows, ensure SSL certificates are properly configured")
                    logger.error("    → Try setting REQUESTS_CA_BUNDLE or SSL_CERT_FILE environment variables")
            elif "timeout" in error_lower:
                logger.error(f"DEBUG: Timeout during tool discovery for '{safe_server_name}'")
                logger.error("    → Server is slow to respond to list_tools() request")
                logger.error("    → Server may be overloaded or hanging")
            elif "json" in error_lower or "decode" in error_lower:
                logger.error(f"DEBUG: Protocol error during tool discovery for '{safe_server_name}'")
                logger.error("    → Server returned invalid MCP response")
                logger.error("    → Check if server implements MCP protocol correctly")
            elif "ssl" in error_lower or "certificate" in error_lower:
                logger.error(f"DEBUG: SSL/Certificate error during tool discovery for '{safe_server_name}'")
                logger.error(f"    → URL: {server_config.get('url', 'N/A')}")
                logger.error("    → SSL certificate verification failed")
                logger.error("    → On Windows, this may require installing/updating CA certificates")
                logger.error("    → Check if the server URL uses HTTPS with a self-signed or untrusted certificate")
            else:
                logger.error(f"DEBUG: Generic tool discovery error for '{safe_server_name}'")
                logger.error(f"    → Client type: {type(client).__name__}")
                logger.error(f"    → Server URL: {server_config.get('url', 'N/A')}")
                logger.error(f"    → Transport type: {server_config.get('transport', server_config.get('type', 'N/A'))}")

            # Record failure for status/reconnect purposes
            self._record_server_failure(server_name, f"{error_type}: {error_msg}")

            logger.debug(f"Full tool discovery traceback for {safe_server_name}:", exc_info=True)

            server_data = {
                'tools': [],
                'config': server_config,
            }
            logger.debug(
                "Set empty tools list for failed server '%s' (config_present=%s)",
                safe_server_name,
                server_config is not None,
            )
            return server_data

    async def discover_tools(self):
        """Discover tools from all MCP servers in parallel."""
        logger.info("Starting MCP tool discovery for %d connected servers", len(self.clients))
        logger.debug("Tool discovery servers: %s", list(self.clients.keys()))
        self.available_tools = {}

        # Create tasks for parallel tool discovery
        tasks = [
            self._discover_tools_for_server(server_name, client)
            for server_name, client in self.clients.items()
        ]
        server_names = list(self.clients.keys())

        # Run all tool discovery tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and store server tools data
        for server_name, result in zip(server_names, results):
            # Skip clients whose config was removed during reload
            if server_name not in self.servers_config:
                logger.warning(
                    f"Skipping tool discovery result for '{server_name}' because it is no longer in servers_config"
                )
                continue

            if isinstance(result, Exception):
                logger.error(f"Exception during tool discovery for {server_name}: {result}", exc_info=True)
                # Record failure and set empty tools list for failed server
                self._record_server_failure(server_name, f"Exception during tool discovery: {result}")
                self.available_tools[server_name] = {
                    'tools': [],
                    'config': self.servers_config.get(server_name),
                }
            else:
                # Clear any previous discovery failure on success
                self._clear_server_failure(server_name)
                self.available_tools[server_name] = result

        total_tools = sum(len(server_data.get('tools', [])) for server_data in self.available_tools.values())
        logger.info(
            "MCP tool discovery complete: %d tools across %d servers",
            total_tools,
            len(self.available_tools),
        )
        for server_name, server_data in self.available_tools.items():
            tool_names = [tool.name for tool in server_data.get('tools', [])]
            logger.debug("Tool discovery summary: %s: %d tools %s", server_name, len(tool_names), tool_names)

        # Build tool index for quick lookups
        self._tool_index = {}
        for server_name, server_data in self.available_tools.items():
            if server_name == "canvas":
                self._tool_index["canvas_canvas"] = {
                    'server': 'canvas',
                    'tool': None  # pseudo tool
                }
            else:
                for tool in server_data.get('tools', []):
                    full_name = f"{server_name}_{tool.name}"
                    self._tool_index[full_name] = {
                        'server': server_name,
                        'tool': tool
                    }

    async def _discover_prompts_for_server(self, server_name: str, client: Client) -> Dict[str, Any]:
        """Discover prompts for a single server. Returns server prompts data."""
        safe_server_name = sanitize_for_logging(server_name)
        server_config = self.servers_config.get(server_name, {})
        discovery_timeout = config_manager.app_settings.mcp_discovery_timeout
        logger.debug(f"Attempting to discover prompts from {safe_server_name}")
        try:
            logger.debug(f"Opening client connection for {safe_server_name}")
            async with client:
                logger.debug(f"Client connected for {safe_server_name}, listing prompts...")
                try:
                    prompts = await asyncio.wait_for(client.list_prompts(), timeout=discovery_timeout)
                    logger.debug(
                        f"Got {len(prompts)} prompts from {safe_server_name}: {[prompt.name for prompt in prompts]}"
                    )
                    server_data = {
                        'prompts': prompts,
                        'config': server_config,
                    }
                    logger.info(f"Discovered {len(prompts)} prompts from {safe_server_name}")
                    logger.debug(f"Successfully stored prompts for {safe_server_name}")
                    return server_data
                except Exception as e:
                    # Server might not support prompts or list_prompts() failed  store empty list
                    logger.debug(
                        f"Server {safe_server_name} does not support prompts or list_prompts() failed: {e}"
                    )
                    return {
                        'prompts': [],
                        'config': server_config,
                    }
        except Exception as e:
            error_type = type(e).__name__
            error_msg = sanitize_for_logging(str(e))
            logger.error(f"PROMPT DISCOVERY FAILED for '{safe_server_name}': {error_type}: {error_msg}")

            # Targeted debugging for prompt discovery errors
            error_lower = str(e).lower()
            if "connection" in error_lower or "refused" in error_lower:
                logger.error(f"DEBUG: Connection lost during prompt discovery for '{safe_server_name}'")
                logger.error("    → Server may have crashed or disconnected")
                # Check if this is an HTTPS/SSL issue
                if "ssl" in error_lower or "certificate" in error_lower or "https" in error_lower:
                    logger.error("    → SSL/HTTPS error detected")
                    logger.error("    → On Windows, ensure SSL certificates are properly configured")
            elif "timeout" in error_lower:
                logger.error(f"DEBUG: Timeout during prompt discovery for '{safe_server_name}'")
                logger.error("    → Server is slow to respond to list_prompts() request")
            elif "json" in error_lower or "decode" in error_lower:
                logger.error(f"DEBUG: Protocol error during prompt discovery for '{safe_server_name}'")
                logger.error("    → Server returned invalid MCP response for prompts")
            elif "ssl" in error_lower or "certificate" in error_lower:
                logger.error(f"DEBUG: SSL/Certificate error during prompt discovery for '{safe_server_name}'")
                logger.error(f"    → URL: {server_config.get('url', 'N/A')}")
                logger.error("    → SSL certificate verification failed")
                logger.error("    → On Windows, this may require installing/updating CA certificates")
            else:
                logger.error(f"DEBUG: Generic prompt discovery error for '{safe_server_name}'")

            # Record failure for status/reconnect purposes
            self._record_server_failure(server_name, f"{error_type}: {error_msg}")

            logger.debug(f"Full prompt discovery traceback for {safe_server_name}:", exc_info=True)
            logger.debug(f"Set empty prompts list for failed server {safe_server_name}")
            return {
                'prompts': [],
                'config': server_config,
            }

    async def discover_prompts(self):
        """Discover prompts from all MCP servers in parallel."""
        logger.info("Starting MCP prompt discovery for %d connected servers", len(self.clients))
        logger.debug("Prompt discovery servers: %s", list(self.clients.keys()))
        self.available_prompts = {}

        # Create tasks for parallel prompt discovery
        tasks = [
            self._discover_prompts_for_server(server_name, client)
            for server_name, client in self.clients.items()
        ]
        server_names = list(self.clients.keys())

        # Run all prompt discovery tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and store server prompts data
        for server_name, result in zip(server_names, results):
            # Skip clients whose config was removed during reload
            if server_name not in self.servers_config:
                logger.warning(
                    f"Skipping prompt discovery result for '{server_name}' because it is no longer in servers_config"
                )
                continue

            if isinstance(result, Exception):
                logger.error(f"Exception during prompt discovery for {server_name}: {result}", exc_info=True)
                # Record failure and set empty prompts list for failed server
                self._record_server_failure(server_name, f"Exception during prompt discovery: {result}")
                self.available_prompts[server_name] = {
                    'prompts': [],
                    'config': self.servers_config.get(server_name),
                }
            else:
                # Clear any previous discovery failure on success
                self._clear_server_failure(server_name)
                self.available_prompts[server_name] = result

        total_prompts = sum(len(server_data.get('prompts', [])) for server_data in self.available_prompts.values())
        logger.info(
            "MCP prompt discovery complete: %d prompts across %d servers",
            total_prompts,
            len(self.available_prompts),
        )
        for server_name, server_data in self.available_prompts.items():
            prompt_names = [prompt.name for prompt in server_data.get('prompts', [])]
            logger.debug("Prompt discovery summary: %s: %d prompts %s", server_name, len(prompt_names), prompt_names)

    def get_server_groups(self, server_name: str) -> List[str]:
        """Get required groups for a server."""
        if server_name in self.servers_config:
            return self.servers_config[server_name].get("groups", [])
        return []

    def get_available_servers(self) -> List[str]:
        """Get list of configured servers."""
        return list(self.servers_config.keys())

    def get_tools_for_servers(self, server_names: List[str]) -> Dict[str, Any]:
        """Get tools and their schemas for selected servers."""
        tools_schema = []
        server_tool_mapping = {}

        for server_name in server_names:
            # Handle canvas pseudo-tool
            if server_name == "canvas":
                canvas_tool_schema = {
                    "type": "function",
                    "function": {
                        "name": "canvas_canvas",
                        "description": "Display final rendered content in a visual canvas panel. Use this for: 1) Complete code (not code discussions), 2) Final reports/documents (not report discussions), 3) Data visualizations, 4) Any polished content that should be viewed separately from the conversation. Put the actual content in the canvas, keep discussions in chat.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The content to display in the canvas. Can be markdown, code, or plain text."
                                }
                            },
                            "required": ["content"]
                        }
                    }
                }
                tools_schema.append(canvas_tool_schema)
                server_tool_mapping["canvas_canvas"] = {
                    'server': 'canvas',
                    'tool_name': 'canvas'
                }
            elif server_name in self.available_tools:
                server_tools = self.available_tools[server_name]['tools']
                for tool in server_tools:
                    # Convert MCP tool format to OpenAI function calling format
                    tool_schema = {
                        "type": "function",
                        "function": {
                            "name": f"{server_name}_{tool.name}",
                            "description": tool.description or '',
                            "parameters": tool.inputSchema or {}
                        }
                    }
                    # log the server -> function name
                    # logger.info(f"Adding tool {tool.name} for server {server_name} ")
                    tools_schema.append(tool_schema)
                    server_tool_mapping[f"{server_name}_{tool.name}"] = {
                        'server': server_name,
                        'tool_name': tool.name
                    }

        return {
            'tools': tools_schema,
            'mapping': server_tool_mapping
        }

    def _requires_user_auth(self, server_name: str) -> bool:
        """Check if a server requires per-user authentication.

        Returns True for servers with auth_type 'oauth', 'jwt', 'bearer', or 'api_key'.
        These servers need user-specific tokens rather than shared/admin tokens.
        """
        config = self.servers_config.get(server_name, {})
        auth_type = config.get("auth_type", "none")
        return auth_type in ("oauth", "jwt", "bearer", "api_key")

    async def _get_user_client(
        self,
        server_name: str,
        user_email: str,
    ) -> Optional[Client]:
        """Get or create a user-specific client for servers requiring per-user auth.

        Args:
            server_name: Name of the MCP server
            user_email: User's email address

        Returns:
            FastMCP Client configured with user's token, or None if no token available
        """
        from atlas.modules.mcp_tools.token_storage import get_token_storage

        token_storage = get_token_storage()
        cache_key = (user_email.lower(), server_name)

        # Check cache first, but validate token is still valid
        async with self._user_clients_lock:
            if cache_key in self._user_clients:
                # Verify the token is still valid before returning cached client
                stored_token = token_storage.get_valid_token(user_email, server_name)
                if stored_token is not None:
                    return self._user_clients[cache_key]
                else:
                    # Token expired or removed, invalidate cached client
                    logger.debug(
                        f"Token expired for user on server '{server_name}', "
                        f"invalidating cached client"
                    )
                    del self._user_clients[cache_key]

        # Get user's token from storage
        logger.debug(f"[AUTH] Looking up token for server='{server_name}'")
        stored_token = token_storage.get_valid_token(user_email, server_name)
        logger.debug(f"[AUTH] Token found: {stored_token is not None}")

        if stored_token is None:
            logger.debug(
                f"[AUTH] No valid token for server '{server_name}' - user needs to authenticate"
            )
            return None

        # Get server config
        config = self.servers_config.get(server_name, {})
        url = config.get("url")

        if not url:
            logger.error(f"No URL configured for server '{server_name}'")
            return None

        # Ensure URL has protocol
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"

        # Create client with user's token
        try:
            log_handler = self._create_log_handler(server_name)
            auth_type = config.get("auth_type", "bearer")

            # For API key auth, use custom header; for bearer/jwt/oauth, use auth parameter
            if auth_type == "api_key":
                # Use custom header for API key authentication
                auth_header = config.get("auth_header", "X-API-Key")
                logger.debug(
                    f"Creating API key client for '{server_name}' with header '{auth_header}'"
                )
                transport = StreamableHttpTransport(
                    url,
                    headers={auth_header: stored_token.token_value},
                )
                client = Client(
                    transport=transport,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )
            else:
                # FastMCP Client accepts auth= as a string (bearer token)
                client = Client(
                    url,
                    auth=stored_token.token_value,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )

            # Cache the client
            async with self._user_clients_lock:
                self._user_clients[cache_key] = client

            logger.info(
                f"Created user-specific client for server '{server_name}' (auth_type={auth_type})"
            )
            return client

        except Exception as e:
            logger.error(
                f"Failed to create user client for server '{server_name}': {e}"
            )
            return None

    async def _invalidate_user_client(self, user_email: str, server_name: str) -> None:
        """Remove a user's cached client (e.g., when token is revoked)."""
        cache_key = (user_email.lower(), server_name)
        async with self._user_clients_lock:
            if cache_key in self._user_clients:
                del self._user_clients[cache_key]
                logger.debug(f"Invalidated user client cache for server '{server_name}'")

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        progress_handler: Optional[Any] = None,
        elicitation_handler: Optional[Any] = None,
        user_email: Optional[str] = None,
    ) -> Any:
        """Call a specific tool on an MCP server.

        Args:
            server_name: Name of the MCP server
            tool_name: Name of the tool to call
            arguments: Tool arguments
            progress_handler: Optional progress callback handler
            elicitation_handler: Optional elicitation callback handler. Prefer the built-in
                elicitation routing (registered at client creation time) for shared clients.
            user_email: User's email for per-user authentication (required for oauth/jwt servers)
        """
        # Determine which client to use
        client = None

        # Check if this server requires per-user authentication
        if self._requires_user_auth(server_name):
            logger.debug(f"Server '{server_name}' requires user auth, user_email={user_email}")
            if user_email:
                client = await self._get_user_client(server_name, user_email)
                logger.debug(f"_get_user_client for '{server_name}' returned client: {client is not None}")
                if client is None:
                    # Get auth type and build OAuth URL if applicable
                    server_config = self.servers_config.get(server_name, {})
                    auth_type = server_config.get("auth_type", "oauth")
                    oauth_start_url = None
                    if auth_type == "oauth":
                        # Build OAuth start URL for automatic redirect
                        oauth_start_url = f"/api/mcp/auth/{server_name}/oauth/start"
                    raise AuthenticationRequiredException(
                        server_name=server_name,
                        auth_type=auth_type,
                        message=f"Server '{server_name}' requires authentication.",
                        oauth_start_url=oauth_start_url,
                    )
            else:
                server_config = self.servers_config.get(server_name, {})
                auth_type = server_config.get("auth_type", "oauth")
                raise AuthenticationRequiredException(
                    server_name=server_name,
                    auth_type=auth_type,
                    message=f"Server '{server_name}' requires authentication but no user context.",
                    oauth_start_url=f"/api/mcp/auth/{server_name}/oauth/start" if auth_type == "oauth" else None,
                )
        else:
            # Use shared client for servers without per-user auth
            if server_name not in self.clients:
                raise ValueError(f"No client available for server: {server_name}")
            client = self.clients[server_name]

        call_timeout = config_manager.app_settings.mcp_call_timeout
        try:
            # Set elicitation callback before opening the client context.
            # FastMCP negotiates supported capabilities during session init.
            if elicitation_handler is not None:
                client.set_elicitation_callback(elicitation_handler)

            async with client:
                # Pass progress handler if provided (fastmcp >= 2.3.5)
                kwargs = {}
                if progress_handler is not None:
                    kwargs["progress_handler"] = progress_handler

                result = await asyncio.wait_for(
                    client.call_tool(tool_name, arguments, **kwargs),
                    timeout=call_timeout,
                )
                logger.info(f"Successfully called {sanitize_for_logging(tool_name)} on {sanitize_for_logging(server_name)}")
                return result
        except asyncio.TimeoutError:
            error_msg = f"Tool call '{tool_name}' on server '{server_name}' timed out after {call_timeout}s"
            logger.error(error_msg)
            self._record_server_failure(server_name, error_msg)
            raise TimeoutError(error_msg)
        except Exception as e:
            logger.error(f"Error calling {tool_name} on {server_name}: {e}")
            raise

    async def get_prompt(self, server_name: str, prompt_name: str, arguments: Dict[str, Any] = None) -> Any:
        """Get a specific prompt from an MCP server."""
        if server_name not in self.clients:
            raise ValueError(f"No client available for server: {server_name}")

        client = self.clients[server_name]
        try:
            async with client:
                if arguments:
                    result = await client.get_prompt(prompt_name, arguments)
                else:
                    result = await client.get_prompt(prompt_name)
                logger.info(f"Successfully retrieved prompt {prompt_name} from {server_name}")
                return result
        except Exception as e:
            logger.error(f"Error getting prompt {prompt_name} from {server_name}: {e}")
            raise

    def get_available_prompts_for_servers(self, server_names: List[str]) -> Dict[str, Any]:
        """Get available prompts for selected servers."""
        available_prompts = {}

        for server_name in server_names:
            if server_name in self.available_prompts:
                server_prompts = self.available_prompts[server_name]['prompts']
                for prompt in server_prompts:
                    prompt_key = f"{server_name}_{prompt.name}"
                    available_prompts[prompt_key] = {
                        'server': server_name,
                        'name': prompt.name,
                        'description': prompt.description or '',
                        'arguments': prompt.arguments or {}
                    }

        return available_prompts

    async def get_authorized_servers(self, user_email: str, auth_check_func) -> List[str]:
        """Get list of servers the user is authorized to use."""
        authorized_servers = []
        for server_name, server_config in self.servers_config.items():
            if not server_config.get("enabled", True):
                continue

            required_groups = server_config.get("groups", [])
            if not required_groups:
                authorized_servers.append(server_name)
                continue

            # Check if user is in any of the required groups
            # We need to await each call and collect results before using any()
            group_checks = [await auth_check_func(user_email, group) for group in required_groups]
            if any(group_checks):
                authorized_servers.append(server_name)
        return authorized_servers

    def get_available_tools(self) -> List[str]:
        """Get list of available tool names."""
        available_tools = []
        for server_name, server_data in self.available_tools.items():
            if server_name == "canvas":
                available_tools.append("canvas_canvas")
            else:
                for tool in server_data.get('tools', []):
                    available_tools.append(f"{server_name}_{tool.name}")
        return available_tools

    def get_tools_schema(self, tool_names: List[str]) -> List[Dict[str, Any]]:
        """Get schemas for specified tools.

        Previous implementation attempted to derive the server name by stripping the last
        underscore-delimited segment from the fully-qualified tool name. This broke when
        the original (per-server) tool names themselves contained underscores (e.g.
        server 'ui-demo' with tool 'create_form_demo' produced full name
        'ui-demo_create_form_demo'; naive splitting yielded a *server* of
        'ui-demo_create_form' which does not exist, causing the schema lookup to fail and
        returning an empty set. This method now directly matches fully-qualified tool
        names against the discovered inventory instead of guessing via string surgery.
        """

        if not tool_names:
            return []

        # Build (or reuse) an index of full tool name -> (server_name, tool_obj)
        # so we can do O(1) lookups without fragile string parsing.
        if not hasattr(self, "_tool_index") or not getattr(self, "_tool_index"):
            index = {}
            for server_name, server_data in self.available_tools.items():
                if server_name == "canvas":
                    index["canvas_canvas"] = {
                        'server': 'canvas',
                        'tool': None  # pseudo tool
                    }
                else:
                    for tool in server_data.get('tools', []):
                        full_name = f"{server_name}_{tool.name}"
                        index[full_name] = {
                            'server': server_name,
                            'tool': tool
                        }
            self._tool_index = index
        else:
            index = self._tool_index

        matched = []
        missing = []
        for requested in tool_names:
            entry = index.get(requested)
            if not entry:
                missing.append(requested)
                continue
            if requested == "canvas_canvas":
                # Recreate the canvas schema (kept in one place – duplicate logic intentional
                # to avoid coupling to get_tools_for_servers which returns superset data)
                matched.append({
                    "type": "function",
                    "function": {
                        "name": "canvas_canvas",
                        "description": "Display final rendered content in a visual canvas panel. Use this for: 1) Complete code (not code discussions), 2) Final reports/documents (not report discussions), 3) Data visualizations, 4) Any polished content that should be viewed separately from the conversation. Put the actual content in the canvas, keep discussions in chat.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The content to display in the canvas. Can be markdown, code, or plain text."
                                }
                            },
                            "required": ["content"]
                        }
                    }
                })
            else:
                tool = entry['tool']
                matched.append({
                    "type": "function",
                    "function": {
                        "name": requested,
                        "description": getattr(tool, 'description', '') or '',
                        "parameters": getattr(tool, 'inputSchema', {}) or {}
                    }
                })



        return matched

    # ------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------
    def _normalize_mcp_tool_result(self, raw_result: Any) -> Dict[str, Any]:
        """Normalize a FastMCP CallToolResult (or similar object) into our contract.

        Returns a dict shaped like:
        {
          "results": <payload or string>,
          "meta_data": {...optional...},
          "returned_file_names": [...optional...],
          "returned_file_count": N (if file contents present)
        }

        Notes:
        - We never inline base64 file contents here to avoid prompt bloat.
        - Handles legacy key forms (result, meta-data, metadata).
        - Falls back to stringifying the raw result if structured extraction fails.
        """
        normalized: Dict[str, Any] = {}
        structured: Dict[str, Any] = {}

        # Attempt extraction in priority order
        try:
            if hasattr(raw_result, "structured_content") and raw_result.structured_content:  # type: ignore[attr-defined]
                structured = raw_result.structured_content  # type: ignore[attr-defined]
            elif hasattr(raw_result, "data") and raw_result.data:  # type: ignore[attr-defined]
                structured = raw_result.data  # type: ignore[attr-defined]
            else:
                # Fallback: extract text content from content array
                if hasattr(raw_result, "content"):
                    contents = getattr(raw_result, "content")
                    if contents:
                        # Collect all text from TextContent items
                        text_parts = []
                        for item in contents:
                            if hasattr(item, "type") and getattr(item, "type") == "text":
                                text = getattr(item, "text", None)
                                if text:
                                    text_parts.append(text)

                        if text_parts:
                            combined_text = "\n".join(text_parts)
                            # Try to parse as JSON if it looks like JSON
                            if combined_text.strip().startswith(("{", "[")):
                                try:
                                    logger.info("MCP tool result normalization: using content text JSON fallback for structured extraction")
                                    structured = json.loads(combined_text)
                                except Exception:  # pragma: no cover - defensive
                                    # Not valid JSON, use as plain text result
                                    structured = {"results": combined_text}
                            else:
                                # Plain text - use as results directly
                                structured = {"results": combined_text}
        except Exception as parse_err:  # pragma: no cover - defensive
            logger.debug(f"Non-fatal parse issue extracting structured tool result: {parse_err}")

        if isinstance(structured, dict):
            # Support both correct and legacy key forms
            results_payload = structured.get("results") or structured.get("result")
            meta_payload = (
                structured.get("meta_data")
                or structured.get("meta-data")
                or structured.get("metadata")
            )
            returned_file_names = structured.get("returned_file_names")
            returned_file_contents = structured.get("returned_file_contents")

            if results_payload is not None:
                normalized["results"] = results_payload
            if meta_payload is not None:
                try:
                    # Heuristic to prevent very large meta blobs
                    if len(json.dumps(meta_payload)) < 4000:
                        normalized["meta_data"] = meta_payload
                    else:
                        normalized["meta_data_truncated"] = True
                except Exception:  # pragma: no cover
                    normalized["meta_data_parse_error"] = True
            if returned_file_names:
                normalized["returned_file_names"] = returned_file_names
            if returned_file_contents:
                normalized["returned_file_count"] = (
                    len(returned_file_contents) if isinstance(returned_file_contents, (list, tuple)) else 1
                )

            # Phase 5 fallback: if no explicit results key, treat *entire* structured dict (minus large/base64 fields) as results
            if "results" not in normalized:
                # Prune potentially huge / sensitive keys before fallback
                prune_keys = {"returned_file_contents"}
                pruned = {k: v for k, v in structured.items() if k not in prune_keys}
                try:
                    serialized = json.dumps(pruned)
                    if len(serialized) <= 8000:  # size guard
                        normalized["results"] = pruned
                    else:
                        normalized["results_summary"] = {
                            "keys": list(pruned.keys()),
                            "omitted_due_to_size": len(serialized)
                        }
                except Exception:  # pragma: no cover
                    # Fallback to string repr if serialization fails
                    normalized.setdefault("results", str(pruned))

        if not normalized:
            normalized = {"results": str(raw_result)}
        return normalized

    async def execute_tool(
        self,
        tool_call: ToolCall,
        context: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """Execute a tool call."""
        logger.debug("ToolManager.execute_tool: tool=%s", tool_call.name)
        # Handle canvas pseudo-tool
        if tool_call.name == "canvas_canvas":
            # Canvas tool just returns the content - it's handled by frontend
            content = tool_call.arguments.get("content", "")
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Canvas content displayed: {content[:100]}..." if len(content) > 100 else f"Canvas content displayed: {content}",
                success=True
            )

        # Use the tool index to get server and tool name (avoids parsing issues with dashes/underscores)
        if not hasattr(self, "_tool_index") or not getattr(self, "_tool_index"):
            # Build tool index if not available (same logic as in get_tools_schema)
            index = {}
            for server_name, server_data in self.available_tools.items():
                if server_name == "canvas":
                    index["canvas_canvas"] = {
                        'server': 'canvas',
                        'tool': None  # pseudo tool
                    }
                else:
                    for tool in server_data.get('tools', []):
                        full_name = f"{server_name}_{tool.name}"
                        index[full_name] = {
                            'server': server_name,
                            'tool': tool
                        }
            self._tool_index = index

        # Look up the tool in our index
        tool_entry = self._tool_index.get(tool_call.name)
        if not tool_entry:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool not found: {tool_call.name}",
                success=False,
                error=f"Tool not found: {tool_call.name}"
            )

        server_name = tool_entry['server']
        actual_tool_name = tool_entry['tool'].name if tool_entry['tool'] else tool_call.name

        try:
            update_cb = None
            user_email = None
            if isinstance(context, dict):
                update_cb = context.get("update_callback")
                user_email = context.get("user_email")

            if update_cb is None:
                logger.warning(
                    f"Executing tool '{tool_call.name}' without update_callback - "
                    f"elicitation will not work. Context type: {type(context)}"
                )
            else:
                logger.debug(f"Executing tool '{tool_call.name}' with update_callback present")

            async def _tool_log_callback(
                log_server_name: str,
                level: str,
                message: str,
                extra: Dict[str, Any],
            ) -> None:
                if update_cb is None:
                    return
                try:
                    # Deferred import to avoid cycles
                    from atlas.application.chat.utilities.event_notifier import notify_tool_log
                    await notify_tool_log(
                        server_name=log_server_name,
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        level=level,
                        message=sanitize_for_logging(message),
                        extra=extra,
                        update_callback=update_cb,
                    )
                except Exception:
                    logger.debug("Tool log forwarding failed", exc_info=True)

            # Build a progress handler that forwards to UI if provided via context
            async def _progress_handler(progress: float, total: Optional[float], message: Optional[str]) -> None:
                try:
                    if update_cb is not None:
                        # Deferred import to avoid cycles
                        from atlas.application.chat.utilities.event_notifier import notify_tool_progress
                        await notify_tool_progress(
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                            progress=progress,
                            total=total,
                            message=message,
                            update_callback=update_cb,
                        )
                except Exception:
                    logger.debug("Progress handler forwarding failed", exc_info=True)

            if update_cb is not None:
                async with self._use_log_callback(_tool_log_callback):
                    async with self._use_elicitation_context(server_name, tool_call, update_cb):
                        async with self._use_sampling_context(server_name, tool_call, update_cb):
                            raw_result = await self.call_tool(
                                server_name,
                                actual_tool_name,
                                tool_call.arguments,
                                progress_handler=_progress_handler,
                                user_email=user_email,
                            )
            else:
                async with self._use_elicitation_context(server_name, tool_call, update_cb):
                    async with self._use_sampling_context(server_name, tool_call, update_cb):
                        raw_result = await self.call_tool(
                            server_name,
                            actual_tool_name,
                            tool_call.arguments,
                            progress_handler=_progress_handler,
                            user_email=user_email,
                        )
            normalized_content = self._normalize_mcp_tool_result(raw_result)
            content_str = json.dumps(normalized_content, ensure_ascii=False)

            # Extract v2 MCP response components (supports dict or FastMCP result objects)
            artifacts: List[Dict[str, Any]] = []
            display_config: Optional[Dict[str, Any]] = None
            meta_data: Optional[Dict[str, Any]] = None

            try:
                if isinstance(raw_result, dict):
                    structured = raw_result
                else:
                    structured = {}
                    if hasattr(raw_result, "structured_content") and raw_result.structured_content:  # type: ignore[attr-defined]
                        sc = raw_result.structured_content  # type: ignore[attr-defined]
                        if isinstance(sc, dict):
                            structured = sc
                    elif hasattr(raw_result, "data") and raw_result.data:  # type: ignore[attr-defined]
                        dt = raw_result.data  # type: ignore[attr-defined]
                        if isinstance(dt, dict):
                            structured = dt
                    else:
                        # Fallback: parse first textual content if JSON-like
                        # This handles MCP responses that return data only in content[0].text
                        if hasattr(raw_result, "content"):
                            contents = getattr(raw_result, "content")
                            if contents and len(contents) > 0 and hasattr(contents[0], "text"):
                                first_text = getattr(contents[0], "text")
                                if isinstance(first_text, str) and first_text.strip().startswith("{"):
                                    try:
                                        structured = json.loads(first_text)
                                    except Exception:
                                        pass

                if isinstance(structured, dict) and structured:
                    # Extract artifacts
                    raw_artifacts = structured.get("artifacts")
                    if isinstance(raw_artifacts, list):
                        for art in raw_artifacts:
                            if isinstance(art, dict):
                                name = art.get("name")
                                b64 = art.get("b64")
                                if name and b64:
                                    artifacts.append(art)

                    # Extract display
                    disp = structured.get("display")
                    if isinstance(disp, dict):
                        display_config = disp

                    # Extract metadata
                    md = structured.get("meta_data")
                    if isinstance(md, dict):
                        meta_data = md

                # Extract ImageContent from the content array
                # Allowlist of safe image MIME types
                ALLOWED_IMAGE_MIMES = {
                    "image/png", "image/jpeg", "image/gif",
                    "image/svg+xml", "image/webp", "image/bmp"
                }

                if hasattr(raw_result, "content"):
                    contents = getattr(raw_result, "content")
                    if isinstance(contents, list):
                        image_counter = 0
                        for item in contents:
                            # Check if this is an ImageContent object
                            if hasattr(item, "type") and getattr(item, "type") == "image":
                                data = getattr(item, "data", None)
                                mime_type = getattr(item, "mimeType", None)

                                # Validate mime type against allowlist
                                if mime_type and mime_type not in ALLOWED_IMAGE_MIMES:
                                    logger.warning(
                                        f"Skipping ImageContent with unsupported mime type: {mime_type}"
                                    )
                                    continue

                                # Validate base64 data
                                if data:
                                    try:
                                        import base64
                                        base64.b64decode(data, validate=True)
                                    except Exception:
                                        logger.warning(
                                            "Skipping ImageContent with invalid base64 data"
                                        )
                                        continue

                                if data and mime_type:
                                    # Generate a filename based on image counter and mime type
                                    # Use mcp_image_ prefix to avoid collisions with structured artifacts
                                    ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
                                    filename = f"mcp_image_{image_counter}.{ext}"

                                    # Create artifact in the expected format
                                    artifact = {
                                        "name": filename,
                                        "b64": data,
                                        "mime": mime_type,
                                        "viewer": "image",
                                        "description": f"Image returned by {tool_call.name}"
                                    }
                                    artifacts.append(artifact)
                                    logger.debug(f"Extracted ImageContent as artifact: {filename} ({mime_type})")

                                    # If no display config exists and this is the first image, auto-open canvas
                                    if not display_config and image_counter == 0:
                                        display_config = {
                                            "primary_file": filename,
                                            "open_canvas": True
                                        }

                                    image_counter += 1
            except Exception:
                logger.warning("Error extracting v2 MCP components from tool result", exc_info=True)

            log_metric("tool_call", user_email, tool_name=actual_tool_name)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=content_str,
                success=True,
                artifacts=artifacts,
                display_config=display_config,
                meta_data=meta_data
            )
        except Exception as e:
            logger.error(f"Error executing tool {tool_call.name}: {e}")

            log_metric("tool_error", user_email, tool_name=actual_tool_name)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing tool: {str(e)}",
                success=False,
                error=str(e)
            )

    async def execute_tool_calls(
        self,
        tool_calls: List[ToolCall],
        context: Optional[Dict[str, Any]] = None
    ) -> List[ToolResult]:
        """Execute multiple tool calls."""
        results = []
        for tool_call in tool_calls:
            result = await self.execute_tool(tool_call, context)
            results.append(result)
        return results

    async def cleanup(self):
        """Cleanup all clients."""
        logger.info("Cleaning up MCP clients")
        # FastMCP clients handle cleanup automatically with context managers
