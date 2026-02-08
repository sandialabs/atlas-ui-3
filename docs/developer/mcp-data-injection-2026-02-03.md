# _mcp_data Injection for MCP Tools

Last updated: 2026-02-04

## Overview

Atlas UI supports automatic injection of `_mcp_data` into MCP tool arguments. When an MCP tool declares `_mcp_data` in its input schema, the backend populates it with structured metadata about all available MCP tools for the current user. This enables "planning" or "orchestration" MCP tools that can reason about available capabilities.

## How It Works

The injection follows the same pattern as the existing `username` injection:

1. **Schema detection**: Before executing a tool, the backend checks if the tool's JSON Schema declares `_mcp_data` in its `properties`.
2. **Injection**: If declared, the backend builds a structured dict describing all available MCP servers and their tools, then sets it as the `_mcp_data` argument value.
3. **Security re-injection**: After user approval edits, `_mcp_data` is re-injected to ensure the tool always receives current, system-provided data.
4. **Schema filtering**: The existing schema-aware argument filter removes `_mcp_data` from tools that do not declare it, so non-planning tools are unaffected.

## _mcp_data Structure

```json
{
    "available_servers": [
        {
            "server_name": "my_server",
            "description": "Server description from config",
            "tools": [
                {
                    "name": "my_server_toolName",
                    "description": "What this tool does",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"}
                        }
                    }
                }
            ]
        }
    ]
}
```

### Fields

| Field | Description |
|-------|-------------|
| `available_servers` | Array of server objects |
| `server_name` | Name of the MCP server |
| `description` | Server description from `mcp.json` config |
| `tools[].name` | Fully-qualified tool name (`serverName_toolName`) |
| `tools[].description` | Tool description from the MCP server |
| `tools[].parameters` | Tool input schema (JSON Schema) |

### What is included

- All MCP tool servers discovered and authorized for the current session
- Tool names, descriptions, and parameter schemas
- Server names and descriptions

### What is excluded

- The `canvas` pseudo-tool (internal)
- Compliance level information (internal concern)
- Server connection details (URLs, transport config)

## Creating a Tool That Uses _mcp_data

Add `_mcp_data` as a parameter in your MCP tool's schema. Atlas UI will automatically detect and populate it.

### FastMCP Example

```python
from typing import Any, Dict, Optional
from fastmcp import FastMCP

mcp = FastMCP("My Planning Server")

@mcp.tool
def plan_task(
    task: str,
    _mcp_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Plan how to accomplish a task using available tools."""
    mcp_data = _mcp_data or {}
    servers = mcp_data.get("available_servers", [])

    # Enumerate available tools
    all_tools = []
    for server in servers:
        for tool in server.get("tools", []):
            all_tools.append(tool["name"])

    return {
        "results": {
            "task": task,
            "available_tools": all_tools,
            "tool_count": len(all_tools),
        }
    }
```

### Naming Convention

The `_` prefix on `_mcp_data` signals that this is a system-injected parameter, not user-provided. LLMs should not attempt to populate this field -- Atlas UI handles it automatically.

## Servers Using _mcp_data

### tool_planner (recommended reference)

The `tool_planner` MCP server (`atlas/mcp/tool_planner/main.py`) is the primary example of `_mcp_data` in action. It combines `_mcp_data` injection with MCP sampling to generate runnable bash scripts:

1. Receives `_mcp_data` with all available tool metadata
2. Converts it into a human-readable CLI reference via `format_tools_for_llm()`
3. Uses `ctx.sample()` to ask the LLM to write a bash script using `atlas_chat_cli.py` (optionally with `--env-file` and config overrides like `--config-overrides`, `--llm-config`, `--mcp-config`, and `--rag-sources-config` for repeatable testing)
4. Returns the generated script

### username-override-demo

The `username-override-demo` MCP server includes a simpler `plan_with_tools` tool that demonstrates basic `_mcp_data` injection without sampling. See `atlas/mcp/username-override-demo/main.py`.

## Related

- Username injection: same pattern, injects authenticated user email into tools declaring a `username` parameter
- Tool approval: `_mcp_data` is re-injected after user edits, same as `username`
- Implementation: `atlas/application/chat/utilities/tool_executor.py`
