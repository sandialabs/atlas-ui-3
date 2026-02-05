#!/usr/bin/env python3
"""
Username Override Demo MCP Server using FastMCP

This server demonstrates the security feature where the Atlas UI backend
automatically overrides the username parameter with the authenticated user's
email. This prevents LLMs from impersonating other users.
"""

import time
from typing import Any, Dict, Optional

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Username Override Demo")


@mcp.tool
def get_user_info(username: str) -> Dict[str, Any]:
    """Get information about the current user.
    
    This tool demonstrates the username override security feature. Even if the LLM
    tries to pass a different username, the Atlas UI backend will always override
    it with the authenticated user's email from the X-User-Email header.
    
    Args:
        username: The username parameter. This will be automatically overridden
                 by Atlas UI backend with the authenticated user's email.
    
    Returns:
        MCP contract shape with user information:
        {
          "results": {
            "username": str,  # The actual authenticated user
            "message": str,
            "security_note": str
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()
    
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    
    return {
        "results": {
            "username": username,
            "message": f"Current authenticated user: {username}",
            "security_note": "This username was injected by Atlas UI backend and cannot be spoofed by the LLM"
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def create_user_record(username: str, record_type: str, data: str) -> Dict[str, Any]:
    """Create a record associated with the authenticated user.
    
    This tool demonstrates how username override ensures that records are always
    created with the correct user context, preventing unauthorized actions.
    
    Args:
        username: The username parameter (automatically overridden with authenticated user)
        record_type: Type of record to create (e.g., "note", "task", "document")
        data: The content/data for the record
    
    Returns:
        MCP contract shape with record creation confirmation:
        {
          "results": {
            "success": bool,
            "username": str,
            "record_type": str,
            "data_length": int,
            "message": str
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()
    
    # In a real implementation, this would create a record in a database
    # associated with the username
    
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    
    return {
        "results": {
            "success": True,
            "username": username,
            "record_type": record_type,
            "data_length": len(data),
            "message": f"Created {record_type} for user {username} with {len(data)} characters of data"
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def check_user_permissions(username: str, resource: str, action: str) -> Dict[str, Any]:
    """Check if the authenticated user has permission for a specific action.
    
    This tool shows how username override ensures permission checks are always
    performed for the actual authenticated user, not a user the LLM might try
    to impersonate.
    
    Args:
        username: The username parameter (automatically overridden with authenticated user)
        resource: The resource to check permissions for (e.g., "document", "database", "api")
        action: The action to check (e.g., "read", "write", "delete", "admin")
    
    Returns:
        MCP contract shape with permission check results:
        {
          "results": {
            "username": str,
            "resource": str,
            "action": str,
            "has_permission": bool,
            "message": str,
            "security_note": str
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()
    
    # This is a demo, so we'll simulate permission logic
    # In a real system, this would check against a permission database
    simulated_permissions = {
        "document": ["read", "write"],
        "database": ["read"],
        "api": ["read"]
    }
    
    has_permission = action in simulated_permissions.get(resource, [])
    
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    
    return {
        "results": {
            "username": username,
            "resource": resource,
            "action": action,
            "has_permission": has_permission,
            "message": f"User {username} {'has' if has_permission else 'does not have'} {action} permission for {resource}",
            "security_note": "Permission checked for authenticated user only - LLM cannot check permissions for other users"
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def demonstrate_override_attempt(username: str, attempted_username: Optional[str] = None) -> Dict[str, Any]:
    """Demonstrate what happens when trying to override the username.
    
    This tool explicitly shows the security feature in action. Even if the LLM
    tries to pass an attempted_username, the backend will always inject the
    authenticated user's email into the username parameter.
    
    Args:
        username: The authenticated user (automatically injected by Atlas UI backend)
        attempted_username: A username the LLM might try to use (for demonstration)
    
    Returns:
        MCP contract shape demonstrating the override:
        {
          "results": {
            "actual_username": str,
            "attempted_username": str or None,
            "override_occurred": bool,
            "impersonation_attempted": bool,
            "explanation": str
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()
    
    # The override always occurs - username is always injected by the backend
    # when a tool declares it accepts a username parameter
    override_occurred = True
    # Detect if an impersonation attempt was made
    impersonation_attempted = attempted_username is not None and username != attempted_username
    
    if impersonation_attempted:
        explanation = (
            f"The authenticated user is: {username}. "
            f"The LLM attempted to use: {attempted_username}. "
            "Atlas UI backend detected and blocked this impersonation attempt by "
            "overriding the username parameter with the real authenticated user's email."
        )
    elif attempted_username and username == attempted_username:
        explanation = (
            f"The authenticated user is: {username}. "
            "The LLM correctly identified the authenticated user. "
            "The Atlas UI backend still injects the username parameter as a security measure."
        )
    else:
        explanation = (
            f"The authenticated user is: {username}. "
            "Atlas UI backend automatically injected the authenticated user's email "
            "into the username parameter. No impersonation attempt was made."
        )
    
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    
    return {
        "results": {
            "actual_username": username,
            "attempted_username": attempted_username,
            "override_occurred": override_occurred,
            "impersonation_attempted": impersonation_attempted,
            "explanation": explanation
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def plan_with_tools(
    task: str,
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """Plan how to accomplish a task using available MCP tools.

    This tool demonstrates the _mcp_data injection feature. Atlas UI backend
    automatically populates _mcp_data with structured metadata about all
    available MCP tools, enabling this tool to reason about capabilities.

    Args:
        task: Description of the task to plan for.
        _mcp_data: Automatically injected by Atlas UI with available tool metadata.
                   Do not provide this manually.
        username: The authenticated user (automatically injected by Atlas UI backend).

    Returns:
        MCP contract shape with a plan based on available tools:
        {
          "results": {
            "task": str,
            "username": str,
            "available_server_count": int,
            "available_tool_count": int,
            "plan_steps": list,
            "note": str
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()

    mcp_data = _mcp_data or {}
    servers = mcp_data.get("available_servers", [])
    total_tools = sum(len(s.get("tools", [])) for s in servers)

    # Build a simple plan listing available tools
    plan_steps = []
    for server in servers:
        for tool in server.get("tools", []):
            plan_steps.append({
                "tool": tool.get("name", "unknown"),
                "server": server.get("server_name", "unknown"),
                "description": tool.get("description", "")[:100],
            })

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "task": task,
            "username": username or "unknown",
            "available_server_count": len(servers),
            "available_tool_count": total_tools,
            "plan_steps": plan_steps,
            "note": (
                "This is a demo showing that _mcp_data was automatically "
                "injected with metadata about all available MCP tools."
            ),
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        },
    }


if __name__ == "__main__":
    mcp.run()
