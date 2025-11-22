"""FastMCP client for connecting to MCP servers and managing tools."""

import logging
import os
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from fastmcp import Client
from modules.config import config_manager
from core.utils import sanitize_for_logging
from modules.config.config_manager import resolve_env_var
from domain.messages.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


class MCPToolManager:
    """Manager for MCP servers and their tools.

    Default config path now points to config/overrides (or env override) with legacy fallback.
    """
    
    def __init__(self, config_path: Optional[str] = None):
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
        else:
            self.config_path = config_path
        mcp_config = config_manager.mcp_config
        self.servers_config = {name: server.model_dump() for name, server in mcp_config.servers.items()}
        self.clients = {}
        self.available_tools = {}
        self.available_prompts = {}
        
    
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

                raw_token = config.get("auth_token")
                try:
                    token = resolve_env_var(raw_token)  # Resolve ${ENV_VAR} if present
                except ValueError as e:
                    logger.error(f"Failed to resolve auth_token for {server_name}: {e}")
                    return None  # Skip this server
                
                if transport_type == "sse":
                    # Use explicit SSE transport
                    logger.debug(f"Creating SSE client for {server_name} at {url}")
                    client = Client(url, auth=token)
                else:
                    # Use HTTP transport (StreamableHttp)
                    logger.debug(f"Creating HTTP client for {server_name} at {url}")
                    client = Client(url, auth=token)
                
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
                            logger.info(f"✓ Working directory exists: {cwd}")
                            logger.info(f"Creating STDIO client for {server_name} with command: {command} in cwd: {cwd}")
                            from fastmcp.client.transports import StdioTransport
                            transport = StdioTransport(command=command[0], args=command[1:], cwd=cwd, env=resolved_env)
                            client = Client(transport)
                            logger.info(f"✓ Successfully created STDIO MCP client for {server_name} with custom command and cwd")
                            return client
                        else:
                            logger.error(f"✗ Working directory does not exist: {cwd}")
                            return None
                    else:
                        logger.info(f"No cwd specified, creating STDIO client for {server_name} with command: {command}")
                        from fastmcp.client.transports import StdioTransport
                        transport = StdioTransport(command=command[0], args=command[1:], env=resolved_env)
                        client = Client(transport)
                        logger.info(f"✓ Successfully created STDIO MCP client for {server_name} with custom command")
                        return client
                else:
                    # Fallback to old behavior for backward compatibility
                    server_path = f"mcp/{server_name}/main.py"
                    logger.debug(f"Attempting to initialize {server_name} at path: {server_path}")
                    if os.path.exists(server_path):
                        logger.debug(f"Server script exists for {server_name}, creating client...")
                        client = Client(server_path)  # Client auto-detects STDIO transport from .py file
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
            logger.error(f"✗ Error creating client for {server_name}: {error_type}: {e}")
            
            # Provide specific debugging information based on error type and config
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                if transport_type in ["http", "sse"]:
                    logger.error(f"🔍 DEBUG: Connection failed for HTTP/SSE server '{server_name}'")
                    logger.error(f"    → URL: {config.get('url', 'Not specified')}")
                    logger.error(f"    → Transport: {transport_type}")
                    logger.error("    → Check if server is running and accessible")
                else:
                    logger.error(f"🔍 DEBUG: STDIO connection failed for server '{server_name}'")
                    logger.error(f"    → Command: {config.get('command', 'Not specified')}")
                    logger.error(f"    → CWD: {config.get('cwd', 'Not specified')}")
                    logger.error("    → Check if command exists and is executable")
                    
            elif "timeout" in str(e).lower():
                logger.error(f"🔍 DEBUG: Timeout connecting to server '{server_name}'")
                logger.error("    → Server may be slow to start or overloaded")
                logger.error("    → Consider increasing timeout or checking server health")
                
            elif "permission" in str(e).lower() or "access" in str(e).lower():
                logger.error(f"🔍 DEBUG: Permission error for server '{server_name}'")
                if config.get('cwd'):
                    logger.error(f"    → Check directory permissions: {config.get('cwd')}")
                if config.get('command'):
                    logger.error(f"    → Check executable permissions: {config.get('command')}")
                    
            elif "module" in str(e).lower() or "import" in str(e).lower():
                logger.error(f"🔍 DEBUG: Import/module error for server '{server_name}'")
                logger.error("    → Check if required dependencies are installed")
                logger.error("    → Check Python path and virtual environment")
                
            elif "json" in str(e).lower() or "decode" in str(e).lower():
                logger.error(f"🔍 DEBUG: JSON/protocol error for server '{server_name}'")
                logger.error("    → Server may not be MCP-compatible")
                logger.error("    → Check server output format")
                
            else:
                # Generic debugging info
                logger.error(f"🔍 DEBUG: Generic error for server '{server_name}'")
                logger.error(f"    → Config: {config}")
                logger.error(f"    → Transport type: {transport_type}")
                
            # Always show the full traceback in debug mode
            logger.debug(f"Full traceback for {server_name}:", exc_info=True)
            return None

    async def initialize_clients(self):
        """Initialize FastMCP clients for all configured servers in parallel."""
        import asyncio
        
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
                logger.error(f"✗ Exception during client initialization for {server_name}: {result}", exc_info=True)
            elif result is not None:
                self.clients[server_name] = result
                logger.info(f"✓ Successfully initialized client for {server_name}")
            else:
                logger.warning(f"⚠ Failed to initialize client for {server_name}")
        
        logger.info("=== CLIENT INITIALIZATION COMPLETE ===")
        logger.info(f"Successfully initialized {len(self.clients)} clients: {list(self.clients.keys())}")
        logger.info(f"Failed to initialize: {set(self.servers_config.keys()) - set(self.clients.keys())}")
        logger.info("=== END CLIENT INITIALIZATION SUMMARY ===")
    
    async def _discover_tools_for_server(self, server_name: str, client: Client) -> Dict[str, Any]:
        """Discover tools for a single server. Returns server tools data."""
        logger.info(f"=== TOOL DISCOVERY: Starting discovery for server '{server_name}' ===")
        logger.debug(f"Server config: {self.servers_config.get(server_name, 'No config found')}")
        try:
            logger.info(f"Opening client connection for {server_name}...")
            async with client:
                logger.info(f"Client connected successfully for {server_name}, listing tools...")
                tools = await client.list_tools()
                logger.info(f"✓ Successfully got {len(tools)} tools from {server_name}: {[tool.name for tool in tools]}")

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
                logger.info(f"✓ Successfully stored {len(tools)} tools for {server_name} in available_tools")
                logger.info(f"=== TOOL DISCOVERY: Completed successfully for server '{server_name}' ===")
                return server_data
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"✗ TOOL DISCOVERY FAILED for {server_name}: {error_type}: {e}")
            
            # Targeted debugging for tool discovery errors
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                logger.error(f"🔍 DEBUG: Connection lost during tool discovery for '{server_name}'")
                logger.error("    → Server may have crashed or disconnected")
                logger.error("    → Check server logs for startup errors")
            elif "timeout" in str(e).lower():
                logger.error(f"🔍 DEBUG: Timeout during tool discovery for '{server_name}'")
                logger.error("    → Server is slow to respond to list_tools() request")
                logger.error("    → Server may be overloaded or hanging")
            elif "json" in str(e).lower() or "decode" in str(e).lower():
                logger.error(f"🔍 DEBUG: Protocol error during tool discovery for '{server_name}'")
                logger.error("    → Server returned invalid MCP response")
                logger.error("    → Check if server implements MCP protocol correctly")
            else:
                logger.error(f"🔍 DEBUG: Generic tool discovery error for '{server_name}'")
                logger.error(f"    → Client object: {client}")
                logger.error(f"    → Client type: {type(client)}")
                
            logger.debug(f"Full tool discovery traceback for {server_name}:", exc_info=True)
            
            server_data = {
                'tools': [],
                'config': self.servers_config[server_name]
            }
            logger.error(f"Set empty tools list for failed server {server_name}")
            logger.info(f"=== TOOL DISCOVERY: Failed for server '{server_name}' ===")
            return server_data

    async def discover_tools(self):
        """Discover tools from all MCP servers in parallel."""
        import asyncio
        
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
            if isinstance(result, Exception):
                logger.error(f"✗ Exception during tool discovery for {server_name}: {result}", exc_info=True)
                # Set empty tools list for failed server
                self.available_tools[server_name] = {
                    'tools': [],
                    'config': self.servers_config[server_name]
                }
            else:
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
        logger.debug(f"Attempting to discover prompts from {server_name}")
        try:
            logger.debug(f"Opening client connection for {server_name}")
            async with client:
                logger.debug(f"Client connected for {server_name}, listing prompts...")
                try:
                    prompts = await client.list_prompts()
                    logger.debug(
                        f"Got {len(prompts)} prompts from {server_name}: {[prompt.name for prompt in prompts]}"
                    )
                    server_data = {
                        'prompts': prompts,
                        'config': self.servers_config[server_name]
                    }
                    logger.info(f"Discovered {len(prompts)} prompts from {server_name}")
                    logger.debug(f"Successfully stored prompts for {server_name}")
                    return server_data
                except Exception:
                    # Server might not support prompts – store empty list
                    logger.debug(f"Server {server_name} does not support prompts")
                    return {
                        'prompts': [],
                        'config': self.servers_config[server_name]
                    }
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"✗ PROMPT DISCOVERY FAILED for {server_name}: {error_type}: {e}")
            
            # Targeted debugging for prompt discovery errors
            if "connection" in str(e).lower() or "refused" in str(e).lower():
                logger.error(f"🔍 DEBUG: Connection lost during prompt discovery for '{server_name}'")
                logger.error("    → Server may have crashed or disconnected")
            elif "timeout" in str(e).lower():
                logger.error(f"🔍 DEBUG: Timeout during prompt discovery for '{server_name}'")
                logger.error("    → Server is slow to respond to list_prompts() request")
            elif "json" in str(e).lower() or "decode" in str(e).lower():
                logger.error(f"🔍 DEBUG: Protocol error during prompt discovery for '{server_name}'")
                logger.error("    → Server returned invalid MCP response for prompts")
            else:
                logger.error(f"🔍 DEBUG: Generic prompt discovery error for '{server_name}'")
                
            logger.debug(f"Full prompt discovery traceback for {server_name}:", exc_info=True)
            logger.debug(f"Set empty prompts list for failed server {server_name}")
            return {
                'prompts': [],
                'config': self.servers_config[server_name]
            }

    async def discover_prompts(self):
        """Discover prompts from all MCP servers in parallel."""
        import asyncio
        
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
            if isinstance(result, Exception):
                logger.error(f"✗ Exception during prompt discovery for {server_name}: {result}", exc_info=True)
                # Set empty prompts list for failed server
                self.available_prompts[server_name] = {
                    'prompts': [],
                    'config': self.servers_config[server_name]
                }
            else:
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
    
    async def get_discoverable_servers(self, user_email: str, auth_check_func) -> Dict[str, Dict[str, Any]]:
        """Get servers that are discoverable but not authorized for the user.
        
        Returns a dict mapping server names to their basic info (description, author, help_email, groups).
        Only includes servers with allow_discovery=true where the user lacks access.
        """
        discoverable_servers = {}
        for server_name, server_config in self.servers_config.items():
            if not server_config.get("enabled", True):
                continue
            
            # Skip if discovery is not allowed
            if not server_config.get("allow_discovery", False):
                continue
            
            required_groups = server_config.get("groups", [])
            
            # Skip servers with no groups (they're accessible to everyone)
            if not required_groups:
                continue
            
            # Check if user is in any of the required groups
            group_checks = [await auth_check_func(user_email, group) for group in required_groups]
            
            # Only include if user does NOT have access
            if not any(group_checks):
                discoverable_servers[server_name] = {
                    'server': server_name,
                    'description': server_config.get('description', ''),
                    'author': server_config.get('author', ''),
                    'short_description': server_config.get('short_description', ''),
                    'help_email': server_config.get('help_email', ''),
                    'groups': required_groups,
                    'compliance_level': server_config.get('compliance_level'),
                    'is_discoverable': True,
                    'has_access': False
                }
        
        return discoverable_servers
    
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

        # Helpful logging / diagnostics
        # try:
        #     logger.info(
        #         f"get_tools_schema: requested={tool_names} matched={len(matched)} missing={missing} available_index_size={len(index)}"
        #     )
        #     if missing:
        #         logger.warning(
        #             "Some requested tools were not found. This usually means discover_tools() ran before those tools were available, or the tool names contain unexpected characters. Missing: %s", missing
        #         )
        # except Exception:
        #     pass

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
                        if isinstance(first_text, str) and first_text.strip().startswith("{"):
                            try:
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
            # Build a progress handler that forwards to UI if provided via context
            async def _progress_handler(progress: float, total: Optional[float], message: Optional[str]) -> None:
                try:
                    update_cb = None
                    if isinstance(context, dict):
                        update_cb = context.get("update_callback")
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
