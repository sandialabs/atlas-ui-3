"""MCP Tools module for the chat backend.

This module provides:
- MCP server management and client connections
- Tool execution and coordination
- Server discovery and authentication
"""

from .client import MCPToolManager

# Create default instance
mcp_tool_manager = MCPToolManager()

__all__ = [
    "MCPToolManager", 
    "mcp_tool_manager",
]