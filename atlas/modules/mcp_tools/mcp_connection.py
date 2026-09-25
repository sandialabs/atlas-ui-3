"""MCP server connection lifecycle for MCPToolManager.

Server initialization, transport detection, failure tracking, exponential
backoff, and the auto-reconnect background loop. Patched globals
(config_manager, Client) are referenced via the client module so test patches
of ``atlas.modules.mcp_tools.client.<name>`` continue to take effect.
"""
import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from fastmcp import Client

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.config.config_manager import resolve_env_var

logger = logging.getLogger(__name__)


def _client():
    """Lazily import the client module to avoid a module-level import cycle.

    The patched globals (``config_manager`` / ``Client`` /
    ``StreamableHttpTransport``) live on the client module; resolving them at
    call time keeps ``@patch('atlas.modules.mcp_tools.client.<name>')`` working
    regardless of which module the calling method now lives in.
    """
    from atlas.modules.mcp_tools import client
    return client


class ConnectionMixin:
    """Server connection, reconnection, and failure tracking."""

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
        app_settings = _client().config_manager.app_settings
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
                    client = _client().Client(
                        url,
                        auth=token,
                        log_handler=log_handler,
                        elicitation_handler=self._create_elicitation_handler(server_name),
                        sampling_handler=self._create_sampling_handler(server_name),
                    )
                else:
                    # Use HTTP transport (StreamableHttp)
                    logger.debug(f"Creating HTTP client for {server_name} at {url}")
                    client = _client().Client(
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

                    # Add project root to PYTHONPATH so STDIO servers can import
                    # atlas.mcp_shared (BlockedStateStore / create_stdio_server)
                    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                    if resolved_env is None:
                        resolved_env = dict(os.environ)
                    existing_pypath = resolved_env.get("PYTHONPATH", "")
                    if existing_pypath:
                        resolved_env["PYTHONPATH"] = f"{project_root}:{existing_pypath}"
                    else:
                        resolved_env["PYTHONPATH"] = project_root

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
                            client = _client().Client(
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
                        client = _client().Client(
                            transport,
                            log_handler=log_handler,
                            elicitation_handler=self._create_elicitation_handler(server_name),
                            sampling_handler=self._create_sampling_handler(server_name),
                        )
                        logger.info(f"Successfully created STDIO MCP client for {server_name} with custom command")
                        return client
                else:
                    # Fallback to old behavior for backward compatibility
                    # The fallback only ever serves mcp/<name>/main.py for a
                    # name that is a configured server key. Reject names that
                    # could form a traversal path before touching the
                    # filesystem (the name reaches here from config files and,
                    # via the admin refresh endpoint, from a request body).
                    if (
                        "/" in server_name
                        or "\\" in server_name
                        or ".." in server_name
                        or os.path.isabs(server_name)
                    ):
                        logger.error(
                            "Refusing fallback script path for server '%s': "
                            "name must not contain path separators",
                            safe_server_name,
                        )
                        return None
                    server_path = f"mcp/{server_name}/main.py"
                    logger.debug(f"Attempting to initialize {server_name} at path: {server_path}")
                    if os.path.exists(server_path):
                        logger.debug(f"Server script exists for {server_name}, creating client...")
                        log_handler = self._create_log_handler(server_name)
                        client = _client().Client(
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

    def _drop_server_tool_index_entries(self, server_name: str) -> None:
        """Remove a server's entries from the shared tool routing index."""
        if hasattr(self, "_tool_index"):
            stale_names = [
                full_name
                for full_name, info in self._tool_index.items()
                if info.get("server") == server_name
            ]
            for full_name in stale_names:
                self._tool_index.pop(full_name, None)
            if stale_names:
                logger.debug(
                    "Dropped %d stale tool index entries for server '%s'",
                    len(stale_names),
                    sanitize_for_logging(server_name),
                )

    async def refresh_server(self, server_name: str) -> Dict[str, Any]:
        """Re-read the configuration for a single server and re-establish its connection.

        Single-server counterpart of ``reload_config()`` +
        ``initialize_clients()`` + ``discover_tools()``/``discover_prompts()``.
        Unlike ``reconnect_failed_servers()``, which only retries servers that
        are already tracked as failed, this also rebuilds servers that are
        currently connected -- the maintenance case where the target server was
        restarted, is wedged, or its ``mcp.json`` entry was edited and the
        change should apply to just that one server.

        Only the named server's state is touched: the targeted config read
        installs just that server's new entry (other servers' edits in
        ``mcp.json`` wait for a real global reload), and other servers'
        clients, caches, and failure records are untouched.

        Concurrent refreshes of the same server (two admins, or a refresh
        racing the auto-reconnect loop) are serialized by
        ``_server_refresh_lock`` so two clients for one server can never be
        constructed and one silently overwrite the other.

        Behaviour:
        1. The server's shared client (if any) is closed before reconnecting.
           In-flight calls still holding it may fail; that is inherent to
           refreshing a connection and bounded by the close timeout.
        2. The server's ``mcp.json`` entry is re-read from disk so config
           edits apply. A broken config file does not block reconnection:
           the in-memory config is kept and the parse error is surfaced in
           the result.
        3. Idle cached per-user clients for the server are evicted and the
           per-user discovery caches for it cleared, so later calls rebuild
           against the refreshed connection. Entries with an in-flight call
           are left alone so a streaming call is not torn down mid-flight;
           they are marked stale so both acquisition paths rebuild them on
           their next use.
        4. The client is re-initialized with the refreshed config and that
           server's tools and prompts are re-discovered. Because discovery
           helpers swallow connection errors (they record the failure and
           return an empty catalogue), a discovery failure is reported here
           as ``status: "failed"`` rather than a hollow "connected".

        Returns:
            Dict with ``server``, ``status`` (``"connected"``, ``"failed"``,
            ``"removed"``, or ``"unknown"``), ``tools``, ``prompts``,
            ``error``, ``config_changed``, ``evicted_clients``, and
            ``config_reload_error``.
        """
        async with self._server_refresh_lock:
            return await self._refresh_server_locked(server_name)

    async def _refresh_server_locked(self, server_name: str) -> Dict[str, Any]:
        safe_server_name = sanitize_for_logging(server_name)
        previous_config = self.servers_config.get(server_name)
        was_configured = previous_config is not None

        # Close and drop the shared client first. The targeted config read
        # below never touches other servers, and closing before it guarantees
        # the old connection is closed even when the server is being removed
        # by this refresh.
        old_client = self.clients.pop(server_name, None) if hasattr(self, "clients") else None
        if old_client is not None:
            await self._close_user_client_entry(
                ("", server_name, None), old_client, release_session=False
            )
            logger.info("Closed previous client for server '%s' during refresh", safe_server_name)

        config_reload_error: Optional[str] = None
        try:
            self._refresh_server_config_from_disk(server_name)
        except Exception as e:  # noqa: BLE001
            config_reload_error = f"{type(e).__name__}: {e}"
            logger.warning(
                "Config reload failed during refresh of server '%s'; "
                "continuing with the in-memory configuration: %s",
                safe_server_name,
                sanitize_for_logging(config_reload_error),
            )

        new_config = self.servers_config.get(server_name)
        config_changed = new_config != previous_config

        evicted_clients = 0
        invalidate = getattr(self, "_invalidate_user_clients_for_server", None)
        if invalidate is not None:
            try:
                evicted_clients = await invalidate(server_name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Could not evict cached user clients for server '%s': %s",
                    safe_server_name,
                    sanitize_for_logging(str(e)),
                )

        # Drop cached per-user tool catalogues for this server so users are
        # not offered tools from before the refresh against a routing index
        # that no longer contains them.
        clear_user_cache = getattr(self, "clear_user_tool_cache", None)
        if clear_user_cache is not None:
            try:
                clear_user_cache(server_name=server_name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Could not clear per-user tool cache for server '%s': %s",
                    safe_server_name,
                    sanitize_for_logging(str(e)),
                )

        if new_config is None:
            if not was_configured:
                return {
                    "server": server_name,
                    "status": "unknown",
                    "tools": 0,
                    "prompts": 0,
                    "error": f"Server '{server_name}' is not configured",
                    "config_changed": False,
                    "evicted_clients": evicted_clients,
                    "config_reload_error": config_reload_error,
                }
            # Removed from config: drop its catalogue, failure record, and
            # routing entries so it stops being offered to users.
            self._failed_servers.pop(server_name, None)
            if hasattr(self, "available_tools"):
                self.available_tools.pop(server_name, None)
            if hasattr(self, "available_prompts"):
                self.available_prompts.pop(server_name, None)
            self._drop_server_tool_index_entries(server_name)
            logger.info("Refresh removed server '%s': no longer configured", safe_server_name)
            return {
                "server": server_name,
                "status": "removed",
                "tools": 0,
                "prompts": 0,
                "error": None,
                "config_changed": config_changed,
                "evicted_clients": evicted_clients,
                "config_reload_error": config_reload_error,
            }

        try:
            client = await self._initialize_single_client(server_name, new_config)
            if client is None:
                error = "Refresh returned None"
                self._record_server_failure(server_name, error)
                return {
                    "server": server_name,
                    "status": "failed",
                    "tools": 0,
                    "prompts": 0,
                    "error": error,
                    "config_changed": config_changed,
                    "evicted_clients": evicted_clients,
                    "config_reload_error": config_reload_error,
                }

            self.clients[server_name] = client
            # Clear any stale failure record from before the refresh so the
            # post-discovery check below only reacts to failures discovery
            # records now (a successful discovery clears nothing itself).
            self._clear_server_failure(server_name)
            logger.info("Successfully refreshed MCP server: %s", safe_server_name)

            # Re-discover just this server. Stale routing entries (tools that
            # no longer exist on the refreshed server) are dropped first.
            self._drop_server_tool_index_entries(server_name)
            await self._discover_and_register_server(server_name, client)

            # Discovery helpers catch connection errors themselves (recording
            # the failure and returning an empty catalogue), so a client that
            # built fine but cannot answer tools/list must surface as a
            # failed refresh -- otherwise the admin sees success and the
            # auto-reconnect loop skips a server that is in self.clients.
            if server_name in self._failed_servers:
                error = self._failed_servers[server_name].get("error", "Discovery failed")
                logger.warning(
                    "Refresh of MCP server %s connected but discovery failed: %s",
                    safe_server_name,
                    sanitize_for_logging(str(error)),
                )
                return {
                    "server": server_name,
                    "status": "failed",
                    "tools": 0,
                    "prompts": 0,
                    "error": error,
                    "config_changed": config_changed,
                    "evicted_clients": evicted_clients,
                    "config_reload_error": config_reload_error,
                }

            tool_data = self.available_tools.get(server_name, {})
            prompt_data = self.available_prompts.get(server_name, {})
            return {
                "server": server_name,
                "status": "connected",
                "tools": len(tool_data.get("tools", [])),
                "prompts": len(prompt_data.get("prompts", [])),
                "error": None,
                "config_changed": config_changed,
                "evicted_clients": evicted_clients,
                "config_reload_error": config_reload_error,
            }
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            self._record_server_failure(server_name, error)
            logger.warning(
                "Failed to refresh MCP server %s: %s",
                safe_server_name,
                sanitize_for_logging(error),
            )
            return {
                "server": server_name,
                "status": "failed",
                "tools": 0,
                "prompts": 0,
                "error": error,
                "config_changed": config_changed,
                "evicted_clients": evicted_clients,
                "config_reload_error": config_reload_error,
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
                "Registered server %s: %d tools, %d prompts",
                sanitize_for_logging(server_name),
                len(tool_data.get("tools", [])),
                len(prompt_data.get("prompts", [])),
            )
        except Exception as e:
            logger.error(
                "Error discovering tools/prompts for %s: %s",
                sanitize_for_logging(server_name),
                sanitize_for_logging(str(e)),
            )

    async def start_auto_reconnect(self) -> None:
        """Start the background auto-reconnect task.

        This task periodically attempts to reconnect to failed MCP servers
        using exponential backoff. Only runs if FEATURE_MCP_AUTO_RECONNECT_ENABLED is true.
        """
        app_settings = _client().config_manager.app_settings
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
                # Expected: we just cancelled the reconnect task; swallow the
                # propagated cancellation so callers (e.g. shutdown) see a
                # clean stop.
                pass
            self._reconnect_task = None
        logger.info("Stopped MCP auto-reconnect background task")

    async def _auto_reconnect_loop(self) -> None:
        """Background loop that periodically attempts to reconnect failed servers."""
        app_settings = _client().config_manager.app_settings
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
