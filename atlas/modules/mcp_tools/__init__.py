"""MCP Tools module for the chat backend.

This module provides:
- MCP server management and client connections
- Tool execution and coordination
- Server discovery and authentication

There is deliberately no package-level ``MCPToolManager`` instance here: a
second manager would split session ownership, and any cleanup that released
on it would free nothing that actually serves chat (issue #1013). The single
runtime manager is built and owned by the application factory
(``atlas.infrastructure.app_factory``) and handed to every ``ChatService``
as its ``tool_manager``.
"""

from .client import MCPToolManager
from .session_manager import MCPSessionManager

__all__ = [
    "MCPToolManager",
    "MCPSessionManager",
]
