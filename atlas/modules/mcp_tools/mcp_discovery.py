"""Tool/prompt discovery and inventory queries for MCPToolManager.

Per-server discovery (with the task-support cache rebuild) plus the read-only
inventory accessors that translate discovered tools/prompts into OpenAI-style
schemas and the lazily-built tool index. config_manager is referenced via the
client module to preserve test patch targets.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastmcp import Client

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

_ATLAS_RAG_DISCOVER_TOOL = "atlas_rag_discover_data_sources"
_ATLAS_RAG_QUERY_TOOL = "atlas_rag_query"
_ATLAS_RAG_TOOL_SCHEMAS = {
    _ATLAS_RAG_DISCOVER_TOOL: {
        "type": "function",
        "function": {
            "name": _ATLAS_RAG_DISCOVER_TOOL,
            "description": (
                "Discover RAG data sources available to the current user. "
                "Returns server-qualified source IDs in the format server:source_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "compliance_level": {
                        "type": "string",
                        "description": "Optional user compliance level for filtering accessible sources.",
                    },
                    "_atlas_user": {
                        "type": "string",
                        "description": "Injected by ATLAS. The authenticated user email.",
                    },
                },
            },
        },
    },
    _ATLAS_RAG_QUERY_TOOL: {
        "type": "function",
        "function": {
            "name": _ATLAS_RAG_QUERY_TOOL,
            "description": (
                "Query selected RAG data sources and return retrieved/synthesized results. "
                "If data_sources is omitted, uses the currently selected UI RAG sources, "
                "falling back to all sources the user can access if none are selected. "
                "Sources outside the user's authorized set are ignored."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "User query to run against RAG."},
                    "data_sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional server-qualified sources (server:source_id).",
                    },
                    "_atlas_user": {
                        "type": "string",
                        "description": "Injected by ATLAS. The authenticated user email.",
                    },
                },
                "required": ["query"],
            },
        },
    },
}


def _client():
    """Lazily import the client module to avoid a module-level import cycle.

    The patched globals (``config_manager`` / ``Client`` /
    ``StreamableHttpTransport``) live on the client module; resolving them at
    call time keeps ``@patch('atlas.modules.mcp_tools.client.<name>')`` working
    regardless of which module the calling method now lives in.
    """
    from atlas.modules.mcp_tools import client
    return client


class DiscoveryMixin:
    """Tool/prompt discovery and inventory query helpers."""

    async def _discover_tools_for_server(self, server_name: str, client: Client) -> Dict[str, Any]:
        """Discover tools for a single server. Returns server tools data."""
        safe_server_name = sanitize_for_logging(server_name)
        server_config = self.servers_config.get(server_name, {})
        safe_config = sanitize_for_logging(str(server_config))
        discovery_timeout = _client().config_manager.app_settings.mcp_discovery_timeout
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
                # Rebuild the per-tool task-forbidden cache for this server
                # from the freshly discovered metadata. Drop any stale entries
                # first so a server upgrade that flips a tool from "forbidden"
                # to "optional"/"required" takes effect on next reload without
                # a process restart. Per MCP SEP-1686, an absent taskSupport
                # value defaults to "forbidden"; only "optional" or "required"
                # leaves us willing to try task mode for a given tool.
                self._tool_task_forbidden = {
                    entry for entry in self._tool_task_forbidden
                    if entry[0] != server_name
                }
                for tool in tools:
                    mode = self._discover_task_support_mode(tool)
                    if mode in ("forbidden", None):
                        self._tool_task_forbidden.add((server_name, tool.name))
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
                self._record_server_failure(server_name, f"Exception during tool discovery: {result}")
                self.available_tools[server_name] = {
                    'tools': [],
                    'config': self.servers_config.get(server_name),
                }
            else:
                self.available_tools[server_name] = result
                # Do NOT call _clear_server_failure here.
                # _discover_tools_for_server already calls
                # _record_server_failure on error and returns an empty
                # tools list; clearing here would erase that failure.

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
        discovery_timeout = _client().config_manager.app_settings.mcp_discovery_timeout
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
                self._record_server_failure(server_name, f"Exception during prompt discovery: {result}")
                self.available_prompts[server_name] = {
                    'prompts': [],
                    'config': self.servers_config.get(server_name),
                }
            else:
                # Do NOT call _clear_server_failure here.
                # _discover_prompts_for_server already calls
                # _record_server_failure on error and returns an empty
                # prompts list; clearing here would erase that failure.
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

    @staticmethod
    def _discover_task_support_mode(tool: Any) -> Optional[str]:
        """Read the per-tool taskSupport mode from a discovered MCP Tool.

        Returns "required", "optional", "forbidden", or None when the tool
        doesn't expose execution metadata. Per MCP SEP-1686, an absent value
        defaults to "forbidden" — callers treat None the same as "forbidden"
        for the purpose of skipping task-mode attempts.
        """
        execution = getattr(tool, "execution", None)
        if execution is None:
            return None
        mode = getattr(execution, "taskSupport", None)
        if mode is None:
            return None
        return getattr(mode, "value", mode)

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

    def get_server_for_tool(self, tool_name: str) -> Optional[str]:
        """Return the owning MCP server name for a fully-qualified tool name.

        Reuses the `_tool_index` built lazily in `get_tools_schema`. Returns
        None when the tool hasn't been discovered yet or doesn't exist — so
        telemetry callers can emit ``tool_source=None`` rather than a
        fabricated prefix. Server names can contain underscores (e.g.
        ``pptx_generator``), so splitting on ``_`` is unsafe.
        """
        if tool_name in (_ATLAS_RAG_DISCOVER_TOOL, _ATLAS_RAG_QUERY_TOOL):
            return "atlas_rag"
        index = getattr(self, "_tool_index", None)
        if not index:
            try:
                self.get_tools_schema([])  # warm cache if not built
            except Exception:
                return None
            index = getattr(self, "_tool_index", None) or {}
            if not index:
                # Populate the index directly from available_tools without
                # going through get_tools_schema (which requires tool_names).
                index = {}
                for server_name, server_data in (self.available_tools or {}).items():
                    if server_name == "canvas":
                        index["canvas_canvas"] = {"server": "canvas", "tool": None}
                    else:
                        for tool in server_data.get("tools", []):
                            index[f"{server_name}_{tool.name}"] = {
                                "server": server_name,
                                "tool": tool,
                            }
                self._tool_index = index
        entry = index.get(tool_name) if index else None
        return entry.get("server") if entry else None

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
        for requested in tool_names:
            if requested in _ATLAS_RAG_TOOL_SCHEMAS:
                matched.append(_ATLAS_RAG_TOOL_SCHEMAS[requested])



        return matched
