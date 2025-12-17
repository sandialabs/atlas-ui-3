"""FastMCP client for connecting to MCP servers and managing tools."""

import asyncio
import contextvars
import logging
import os
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Awaitable, AsyncIterator

from fastmcp import Client
from modules.config import config_manager
from core.utils import sanitize_for_logging
from modules.config.config_manager import resolve_env_var
from domain.messages.models import ToolCall, ToolResult
from modules.mcp_tools.jwt_storage import get_jwt_storage

try:
    # Exposed at module scope so tests can patch `backend.modules.mcp_tools.client.OAuth`.
    from fastmcp.client.auth import OAuth  # type: ignore
except Exception:  # pragma: no cover
    OAuth = None  # type: ignore

try:
    # Exposed at module scope so tests can patch these symbols.
    from key_value.aio.stores.disk import DiskStore  # type: ignore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper  # type: ignore
    from cryptography.fernet import Fernet  # type: ignore
except Exception:  # pragma: no cover
    DiskStore = None  # type: ignore
    FernetEncryptionWrapper = None  # type: ignore
    Fernet = None  # type: ignore



logger = logging.getLogger(__name__)

# Type alias for log callback function
LogCallback = Callable[[str, str, str, Dict[str, Any]], Awaitable[None]]

# Context-local override used to route MCP logs to the *current* request/session.
# This prevents cross-user log leakage when MCPToolManager is shared across connections.
_ACTIVE_LOG_CALLBACK: contextvars.ContextVar[Optional[LogCallback]] = contextvars.ContextVar(
    "mcp_active_log_callback",
    default=None,
)

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

    Default config path now points to config/overrides (or env override) with legacy fallback.
    
    Supports:
    - Hot-reloading configuration from disk via reload_config()
    - Tracking failed server connections for retry
    - Auto-reconnect with exponential backoff (when feature flag is enabled)
    """
    
    def __init__(self, config_path: Optional[str] = None, log_callback: Optional[LogCallback] = None):
        if config_path is None:
            # Use config manager to get config path
            app_settings = config_manager.app_settings
            overrides_root = Path(app_settings.app_config_overrides)

            # If relative, resolve from project root
            if not overrides_root.is_absolute():
                # This file is in backend/modules/mcp_tools/client.py
                backend_root = Path(__file__).parent.parent.parent
                project_root = backend_root.parent
                overrides_root = project_root / overrides_root

            candidate = overrides_root / "mcp.json"
            if not candidate.exists():
                # Legacy fallback
                candidate = Path("backend/configfilesadmin/mcp.json")
                if not candidate.exists():
                    candidate = Path("backend/configfiles/mcp.json")
            self.config_path = str(candidate)
            # Use default config manager when no path specified
            mcp_config = config_manager.mcp_config
            self.servers_config = {name: server.model_dump() for name, server in mcp_config.servers.items()}
        else:
            # Load config from the specified path
            self.config_path = config_path
            config_file = Path(config_path)
            if config_file.exists():
                from modules.config.config_manager import MCPConfig
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
                from fastmcp.client.logging import LogMessage
                
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
                
                # Log to backend logger with server context
                logger.log(
                    python_log_level,
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
    
    def _get_auth_for_server(self, server_name: str, config: Dict[str, Any], url: Optional[str] = None) -> Any:
        """
        Determine the authentication method for an MCP server.
        
        Priority order:
        1. OAuth 2.1 if oauth_config.enabled is True
        2. User-uploaded JWT from jwt_storage
        3. Bearer token from auth_token field
        4. None (no authentication)
        
        Args:
            server_name: Name of the MCP server
            config: Server configuration dictionary
            url: MCP server URL (required for OAuth)
            
        Returns:
            Auth parameter for FastMCP Client (OAuth instance, token string, or None)
        """
        # Check for OAuth configuration
        oauth_config = config.get("oauth_config")
        if oauth_config and oauth_config.get("enabled"):
            logger.info(f"Using OAuth 2.1 authentication for {server_name}")
            try:
                if OAuth is None:
                    raise ImportError("fastmcp.client.auth.OAuth is not available")
                
                # Build OAuth configuration
                oauth_kwargs = {
                    "mcp_url": url or config.get("url")
                }
                
                if oauth_config.get("scopes"):
                    oauth_kwargs["scopes"] = oauth_config["scopes"]
                
                if oauth_config.get("client_name"):
                    oauth_kwargs["client_name"] = oauth_config["client_name"]
                
                if oauth_config.get("callback_port"):
                    oauth_kwargs["callback_port"] = oauth_config["callback_port"]
                
                if oauth_config.get("additional_metadata"):
                    oauth_kwargs["additional_client_metadata"] = oauth_config["additional_metadata"]
                
                # Set up token storage if path is specified
                if oauth_config.get("token_storage_path"):
                    try:
                        if DiskStore is None or FernetEncryptionWrapper is None or Fernet is None:
                            raise ImportError("OAuth token storage dependencies not available")
                        
                        storage_path = Path(oauth_config["token_storage_path"]).expanduser()
                        storage_path.mkdir(parents=True, exist_ok=True)
                        
                        # Get or generate encryption key
                        encryption_key = os.environ.get("OAUTH_STORAGE_ENCRYPTION_KEY")
                        if not encryption_key:
                            key_file = storage_path / ".encryption_key"
                            if key_file.exists():
                                encryption_key = key_file.read_text().strip()
                            else:
                                generated_key = Fernet.generate_key()
                                if isinstance(generated_key, bytes):
                                    encryption_key = generated_key.decode()
                                elif isinstance(generated_key, str):
                                    encryption_key = generated_key
                                else:
                                    encryption_key = str(generated_key)
                                key_file.write_text(encryption_key)
                                key_file.chmod(0o600)
                                logger.warning(
                                    f"Generated new OAuth storage encryption key for {server_name}. "
                                    "Set OAUTH_STORAGE_ENCRYPTION_KEY env var for production."
                                )
                        
                        # Ensure key is bytes for Fernet
                        if isinstance(encryption_key, str):
                            encryption_key_bytes = encryption_key.encode()
                        else:
                            encryption_key_bytes = encryption_key
                        
                        encrypted_storage = FernetEncryptionWrapper(
                            key_value=DiskStore(directory=str(storage_path)),
                            fernet=Fernet(encryption_key_bytes)
                        )
                        oauth_kwargs["token_storage"] = encrypted_storage
                        logger.info(f"Configured encrypted token storage at {storage_path}")
                    except ImportError as e:
                        logger.warning(
                            f"Could not set up encrypted OAuth token storage for {server_name}: {e}. "
                            "Install key-value library for persistent token storage."
                        )
                
                oauth_auth = OAuth(**oauth_kwargs)
                logger.info(f"OAuth 2.1 configured for {server_name}")
                return oauth_auth
                
            except ImportError as e:
                logger.error(
                    f"OAuth authentication requested for {server_name} but fastmcp.client.auth.OAuth not available: {e}"
                )
                return None
            except Exception as e:
                logger.error(f"Failed to configure OAuth for {server_name}: {e}")
                return None
        
        # Check for user-uploaded JWT
        jwt_storage = get_jwt_storage()
        if jwt_storage.has_jwt(server_name):
            jwt_token = jwt_storage.get_jwt(server_name)
            if jwt_token:
                logger.info(f"Using user-uploaded JWT for {server_name}")
                return jwt_token
        
        # Check for auth_token field
        if "auth_token" in config:
            raw_token = config.get("auth_token")

            # Preserve explicit empty string; some callers/tests treat this as an
            # intentional value (e.g., to clear a previously configured token).
            if raw_token == "":
                return ""

            if raw_token is not None:
                try:
                    token = resolve_env_var(raw_token)
                    logger.info(f"Using bearer token from auth_token field for {server_name}")
                    return token
                except ValueError as e:
                    logger.error(f"Failed to resolve auth_token for {server_name}: {e}")
                    return None
        
        # No authentication
        logger.debug(f"No authentication configured for {server_name}")
        return None

    async def _initialize_single_client(self, server_name: str, config: Dict[str, Any]) -> Optional[Client]:
        """Initialize a single MCP client. Returns None if initialization fails."""
        logger.info(f"=== Initializing client for server '{sanitize_for_logging(server_name)}' ===\n\nServer config: {sanitize_for_logging(str(config))}")
        try:
            transport_type = self._determine_transport_type(config)
            logger.info(f"Determined transport type: {transport_type}")
            
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

                # Get authentication (OAuth, JWT, or bearer token)
                auth = self._get_auth_for_server(server_name, config, url)

                # If auth_token was configured but could not be resolved, skip this server.
                # (This keeps _get_auth_for_server returning None for missing env vars,
                # while still preventing accidental unauthenticated connections.)
                if auth is None and "auth_token" in config:
                    raw_token = config.get("auth_token")
                    if raw_token not in (None, ""):
                        try:
                            resolve_env_var(raw_token)
                        except Exception:
                            return None
                
                # Create log handler for this server
                log_handler = self._create_log_handler(server_name)
                
                if transport_type == "sse":
                    # Use explicit SSE transport
                    logger.debug(f"Creating SSE client for {server_name} at {url}")
                    client = Client(url, auth=auth, log_handler=log_handler)
                else:
                    # Use HTTP transport (StreamableHttp)
                    logger.debug(f"Creating HTTP client for {server_name} at {url}")
                    client = Client(url, auth=auth, log_handler=log_handler)
                
                logger.info(f"Created {transport_type.upper()} MCP client for {server_name}")
                return client
            
            elif transport_type == "stdio":
                # STDIO MCP server
                command = config.get("command")
                logger.info(f"STDIO transport - command: {command}")
                if command:
                    # Custom command specified
                    cwd = config.get("cwd")
                    env = config.get("env")
                    logger.info(f"Working directory specified: {cwd}")
                    
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
                        logger.info(f"Environment variables specified: {list(resolved_env.keys())}")
                    
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
                            logger.info(f"Converted relative cwd to absolute: {cwd} (project_root: {project_root})")
                        
                        if os.path.exists(cwd):
                            logger.info(f"Working directory exists: {cwd}")
                            logger.info(f"Creating STDIO client for {server_name} with command: {command} in cwd: {cwd}")
                            from fastmcp.client.transports import StdioTransport
                            transport = StdioTransport(command=command[0], args=command[1:], cwd=cwd, env=resolved_env)
                            client = Client(transport, log_handler=log_handler)
                            logger.info(f"Successfully created STDIO MCP client for {server_name} with custom command and cwd")
                            return client
                        else:
                            logger.error(f"Working directory does not exist: {cwd}")
                            return None
                    else:
                        logger.info(f"No cwd specified, creating STDIO client for {server_name} with command: {command}")
                        from fastmcp.client.transports import StdioTransport
                        transport = StdioTransport(command=command[0], args=command[1:], env=resolved_env)
                        client = Client(transport, log_handler=log_handler)
                        logger.info(f"Successfully created STDIO MCP client for {server_name} with custom command")
                        return client
                else:
                    # Fallback to old behavior for backward compatibility
                    server_path = f"mcp/{server_name}/main.py"
                    logger.debug(f"Attempting to initialize {server_name} at path: {server_path}")
                    if os.path.exists(server_path):
                        logger.debug(f"Server script exists for {server_name}, creating client...")
                        log_handler = self._create_log_handler(server_name)
                        client = Client(server_path, log_handler=log_handler)  # Client auto-detects STDIO transport from .py file
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
        logger.info(f"=== CLIENT INITIALIZATION: Starting parallel initialization for {len(self.servers_config)} servers: {list(self.servers_config.keys())} ===")
        
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
                self.clients[server_name] = result
                self._clear_server_failure(server_name)
                logger.info(f"Successfully initialized client for {server_name}")
            else:
                self._record_server_failure(server_name, "Initialization returned None")
                logger.warning(f"Failed to initialize client for {server_name}")
        
        logger.info("=== CLIENT INITIALIZATION COMPLETE ===")
        logger.info(f"Successfully initialized {len(self.clients)} clients: {list(self.clients.keys())}")
        failed_servers = set(self.servers_config.keys()) - set(self.clients.keys())
        logger.info(f"Failed to initialize: {failed_servers}")
        logger.info("=== END CLIENT INITIALIZATION SUMMARY ===")
    
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
        logger.info(f"=== TOOL DISCOVERY: Starting discovery for server '{safe_server_name}' ===")
        logger.debug(f"Server config: {safe_config}")
        try:
            logger.info(f"Opening client connection for {safe_server_name}...")
            async with client:
                logger.info(f"Client connected successfully for {safe_server_name}, listing tools...")
                tools = await client.list_tools()
                logger.info(f"Successfully got {len(tools)} tools from {safe_server_name}: {[tool.name for tool in tools]}")

                # Log detailed tool information
                for i, tool in enumerate(tools):
                    logger.info(
                        "  Tool %d: name='%s', description='%s'",
                        i + 1,
                        tool.name,
                        getattr(tool, 'description', 'No description'),
                    )

                server_data = {
                    'tools': tools,
                    'config': self.servers_config[server_name]
                }
                logger.info(f"Successfully stored {len(tools)} tools for {safe_server_name} in available_tools")
                logger.info(f"=== TOOL DISCOVERY: Completed successfully for server '{safe_server_name}' ===")
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
            logger.error(f"Set empty tools list for failed server '{safe_server_name}' (config_present={server_config is not None})")
            logger.info(f"=== TOOL DISCOVERY: Failed for server '{safe_server_name}' ===")
            return server_data

    async def discover_tools(self):
        """Discover tools from all MCP servers in parallel."""
        logger.info(f"Starting parallel tool discovery for {len(self.clients)} clients: {list(self.clients.keys())}")
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
        
        logger.info("=== TOOL DISCOVERY COMPLETE ===")
        logger.info("Final available_tools summary:")
        for server_name, server_data in self.available_tools.items():
            tool_count = len(server_data['tools'])
            tool_names = [tool.name for tool in server_data['tools']]
            logger.info(f"  {server_name}: {tool_count} tools {tool_names}")
        logger.info("=== END TOOL DISCOVERY SUMMARY ===")

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
        logger.debug(f"Attempting to discover prompts from {safe_server_name}")
        try:
            logger.debug(f"Opening client connection for {safe_server_name}")
            async with client:
                logger.debug(f"Client connected for {safe_server_name}, listing prompts...")
                try:
                    prompts = await client.list_prompts()
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
        logger.info(f"Starting parallel prompt discovery for {len(self.clients)} clients: {list(self.clients.keys())}")
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
        
        logger.info("=== PROMPT DISCOVERY COMPLETE ===")
        total_prompts = sum(len(server_data['prompts']) for server_data in self.available_prompts.values())
        logger.info(f"Total prompts discovered: {total_prompts}")
        for server_name, server_data in self.available_prompts.items():
            prompt_count = len(server_data['prompts'])
            prompt_names = [prompt.name for prompt in server_data['prompts']]
            logger.info(f"  {server_name}: {prompt_count} prompts {prompt_names}")
        logger.info("=== END PROMPT DISCOVERY SUMMARY ===")
    
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
    
    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        progress_handler: Optional[Any] = None,
    ) -> Any:
        """Call a specific tool on an MCP server."""
        if server_name not in self.clients:
            raise ValueError(f"No client available for server: {server_name}")
        
        client = self.clients[server_name]
        try:
            async with client:
                # Pass through per-call progress handler if provided (fastmcp >= 2.3.5)
                kwargs = {}
                if progress_handler is not None:
                    kwargs["progress_handler"] = progress_handler
                result = await client.call_tool(tool_name, arguments, **kwargs)
                logger.info(f"Successfully called {sanitize_for_logging(tool_name)} on {sanitize_for_logging(server_name)}")
                return result
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
                # Fallback: parse first textual content if JSON-like
                if hasattr(raw_result, "content"):
                    contents = getattr(raw_result, "content")
                    if contents and hasattr(contents[0], "text"):
                        first_text = getattr(contents[0], "text")
                        # Allow JSON objects and arrays in content[0].text
                        if isinstance(first_text, str) and first_text.strip().startswith(("{", "[")):
                            try:
                                logger.info("MCP tool result normalization: using content[0].text JSON fallback for structured extraction")
                                structured = json.loads(first_text)
                            except Exception:  # pragma: no cover - defensive
                                pass
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
        logger.info(f"Step 7: Entering ToolManager.execute_tool for tool {tool_call.name}")
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
            if isinstance(context, dict):
                update_cb = context.get("update_callback")

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
                    from application.chat.utilities.notification_utils import notify_tool_log
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
                        from application.chat.utilities.notification_utils import notify_tool_progress
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
                    raw_result = await self.call_tool(
                        server_name,
                        actual_tool_name,
                        tool_call.arguments,
                        progress_handler=_progress_handler,
                    )
            else:
                raw_result = await self.call_tool(
                    server_name,
                    actual_tool_name,
                    tool_call.arguments,
                    progress_handler=_progress_handler,
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
            except Exception:
                logger.warning("Error extracting v2 MCP components from tool result", exc_info=True)

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
