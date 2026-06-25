"""FastMCP client for connecting to MCP servers and managing tools.

``MCPToolManager`` is assembled from focused mixins defined in the sibling
``mcp_*`` modules (routing, connection, user-client cache, discovery, result
processing, execution). This module keeps the public class, its constructor,
and configuration (re)loading.

The third-party/config imports (``Client``, ``StreamableHttpTransport``,
``config_manager``) live here so that the mixin modules can reference them via
this module and existing ``@patch('atlas.modules.mcp_tools.client.<name>')``
targets keep working regardless of which module a method now lives in.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastmcp import Client

# Re-exported so ``@patch('atlas.modules.mcp_tools.client.StreamableHttpTransport')``
# (and the mixins' ``_client().StreamableHttpTransport`` indirection) keep working
# even though the only call site now lives in mcp_user_clients.
from fastmcp.client.transports import StreamableHttpTransport  # noqa: F401

from atlas.modules.config import config_manager
from atlas.modules.mcp_tools.mcp_connection import ConnectionMixin
from atlas.modules.mcp_tools.mcp_discovery import DiscoveryMixin
from atlas.modules.mcp_tools.mcp_errors import _is_session_terminated_error
from atlas.modules.mcp_tools.mcp_execution import ExecutionMixin
from atlas.modules.mcp_tools.mcp_result_processor import ResultProcessorMixin
from atlas.modules.mcp_tools.mcp_routing import (
    MCP_TO_PYTHON_LOG_LEVEL,
    LogCallback,
    RoutingMixin,
    _ElicitationRoutingContext,
    _SamplingRoutingContext,
)
from atlas.modules.mcp_tools.mcp_user_clients import (
    _DEFAULT_USER_CLIENT_CACHE_IDLE_TTL_SECONDS,
    _DEFAULT_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS,
    _DEFAULT_USER_CLIENT_CACHE_MAX_ENTRIES,
    _DEFAULT_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS,
    _DEFAULT_USER_CLIENT_CLOSE_TIMEOUT_SECONDS,
    UserClientMixin,
)

logger = logging.getLogger(__name__)

# Backwards-compatibility: these names were defined in client.py before the
# split. Re-exported (and imported above) so existing imports/patches such as
# ``from atlas.modules.mcp_tools.client import _ElicitationRoutingContext``
# continue to resolve.
__all__ = [
    "MCPToolManager",
    "MCP_TO_PYTHON_LOG_LEVEL",
    "_ElicitationRoutingContext",
    "_is_session_terminated_error",
]


class MCPToolManager(
    RoutingMixin,
    ConnectionMixin,
    UserClientMixin,
    DiscoveryMixin,
    ResultProcessorMixin,
    ExecutionMixin,
):
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

        # Per-user/per-conversation client cache.
        #
        # Key: (user_email_lower, server_name, conversation_id), Value: FastMCP Client.
        #
        # Keying by conversation_id (not just user) is required because the
        # session manager opens persistent contexts per (conversation_id,
        # server) and each open() increments FastMCP's internal nesting
        # counter on the client. Sharing one client across conversations
        # accumulates the counter; if the underlying session_task dies,
        # FastMCP refuses to reconnect ("nesting counter should be 0 when
        # starting new session, got N").
        self._user_clients: Dict[tuple, Client] = {}
        self._user_client_last_used: Dict[tuple, float] = {}
        self._user_clients_lock = asyncio.Lock()

        # Dictionary-based routing for elicitation so a shared Client can still deliver
        # elicitation requests to the correct user's WebSocket.
        # Key: (server_name, tool_call_id) tuple to avoid collisions with concurrent tool calls
        # Note: Cannot use contextvars.ContextVar because MCP receive loop runs in a different task
        self._elicitation_routing: Dict[tuple, _ElicitationRoutingContext] = {}

        # Dictionary-based routing for sampling requests (similar to elicitation)
        # Key: (server_name, tool_call_id) tuple to avoid collisions with concurrent tool calls
        self._sampling_routing: Dict[tuple, "_SamplingRoutingContext"] = {}

        # Session manager for per-conversation session persistence
        from atlas.modules.mcp_tools.session_manager import MCPSessionManager
        self._session_manager = MCPSessionManager()

        # Cache of which servers support background tasks
        self._server_task_support: Dict[str, bool] = {}
        # Per-tool cache for tools that the server refused task-mode on (e.g.
        # fastmcp tools declared with tasks.mode="forbidden"). A server may
        # advertise task capability overall while individual tools opt out;
        # we learn this on first failure and skip task mode thereafter.
        self._tool_task_forbidden: set[tuple[str, str]] = set()

        # Task timeout: seconds before switching to background task polling
        app_settings = config_manager.app_settings
        self._task_timeout = app_settings.mcp_task_timeout
        self._user_client_cache_max_entries = max(
            1,
            int(getattr(app_settings, "mcp_user_client_cache_max_entries", _DEFAULT_USER_CLIENT_CACHE_MAX_ENTRIES)),
        )
        self._user_client_cache_idle_ttl_seconds = max(
            1,
            int(
                getattr(
                    app_settings,
                    "mcp_user_client_cache_idle_ttl_seconds",
                    _DEFAULT_USER_CLIENT_CACHE_IDLE_TTL_SECONDS,
                )
            ),
        )
        self._user_client_cache_sweep_interval_seconds = max(
            1,
            int(
                getattr(
                    app_settings,
                    "mcp_user_client_cache_sweep_interval_seconds",
                    _DEFAULT_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS,
                )
            ),
        )
        # LRU eviction skips entries touched within this window so a tool
        # call in flight does not have its connection torn down. The
        # caller of _get_user_client holds the Client reference outside
        # the cache lock; touch-time is the only signal we have for "in
        # use right now."
        self._user_client_cache_in_use_window_seconds = max(
            0,
            int(
                getattr(
                    app_settings,
                    "mcp_user_client_cache_in_use_window_seconds",
                    _DEFAULT_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS,
                )
            ),
        )
        self._user_client_close_timeout_seconds = max(
            0.1,
            float(
                getattr(
                    app_settings,
                    "mcp_user_client_close_timeout_seconds",
                    _DEFAULT_USER_CLIENT_CLOSE_TIMEOUT_SECONDS,
                )
            ),
        )
        self._user_client_sweeper_task: Optional[asyncio.Task] = None
        # In-flight close batches kicked off by the sweeper. cleanup()
        # awaits these so cancellation between pop-and-close cannot
        # orphan FastMCP clients (codex review #2 on PR #564).
        self._user_client_close_tasks: set[asyncio.Task] = set()

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

        # Clean up removed servers from clients, failures, and tool/prompt caches
        removed_servers = previous_servers - new_servers
        for server_name in removed_servers:
            self._failed_servers.pop(server_name, None)
            if hasattr(self, 'clients'):
                self.clients.pop(server_name, None)
            if hasattr(self, 'available_tools'):
                self.available_tools.pop(server_name, None)
            if hasattr(self, 'available_prompts'):
                self.available_prompts.pop(server_name, None)

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
