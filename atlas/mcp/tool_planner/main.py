#!/usr/bin/env python3
"""
Tool Planner MCP Server using FastMCP.

Uses _mcp_data injection and optional client-provided script content to generate bash scripts that
accomplish a user's task by calling the Atlas CLI with the appropriate tools.

Flow:
1. User asks the LLM to plan a task (e.g., "create a powerpoint about dogs")
2. LLM calls plan_with_tools, passing the user's request as `task`
3. Atlas UI injects `_mcp_data` (all available tools metadata) and `_atlas_user`
4. Inside the tool:
   a. Convert `_mcp_data` into an LLM-friendly CLI tool reference
   b. Optionally accept a client-generated bash script
   c. Return the generated bash script
"""

import base64
import re
from typing import Any, Dict, Optional

from fastmcp import Context

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("Tool Planner")


def format_tools_for_llm(mcp_data: Dict[str, Any]) -> str:
    """Convert _mcp_data into a human-readable CLI tool reference.

    Produces a text block describing each server and its tools with
    parameter details, suitable for inclusion in an LLM prompt.

    Args:
        mcp_data: The _mcp_data dict injected by Atlas UI, containing
                  an ``available_servers`` list.

    Returns:
        A formatted multi-line string describing available tools.
    """
    servers = mcp_data.get("available_servers", [])
    if not servers:
        return "(No tools available)"

    lines: list[str] = []
    for server in servers:
        server_name = server.get("server_name", "unknown")
        server_desc = server.get("description", "")
        desc_part = f" ({server_desc})" if server_desc else ""
        lines.append(f"Server: {server_name}{desc_part}")

        for tool in server.get("tools", []):
            tool_name = tool.get("name", "unknown")
            tool_desc = tool.get("description", "")
            lines.append(f"  Tool: {tool_name}")
            if tool_desc:
                lines.append(f"    Description: {tool_desc}")

            params = tool.get("parameters", {})
            properties = params.get("properties", {})
            required = set(params.get("required", []))

            if properties:
                lines.append("    Parameters:")
                for param_name, param_schema in properties.items():
                    if param_name.startswith("_"):
                        continue
                    param_type = param_schema.get("type", "any")
                    req_label = "required" if param_name in required else "optional"
                    param_desc = param_schema.get("description", "")
                    desc_suffix = f": {param_desc}" if param_desc else ""
                    lines.append(
                        f"      - {param_name} ({param_type}, {req_label}){desc_suffix}"
                    )

        lines.append("")

    return "\n".join(lines)


def build_planning_prompt(task: str, tools_reference: str) -> str:
    """Build guidance text for client-side script generation.

    Combines the task description, CLI usage instructions, and the
    formatted tools reference into a single prompt.

    Args:
        task: The user's task description.
        tools_reference: Output of ``format_tools_for_llm()``.

    Returns:
        Guidance text suitable for a client-side LLM prompt.
    """
    return (
        f"Task: {task}\n\n"
        f"Available tools:\n{tools_reference}\n\n"
        "CLI usage:\n"
        '  python atlas_chat_cli.py "instruction for the LLM" --tools tool_name\n\n'
        "Capture output to file if needed:\n"
        '  python atlas_chat_cli.py "instruction" --tools tool_name -o result.txt\n\n'
        "Loops:\n"
        '  for item in "a" "b" "c"; do\n'
        '    python atlas_chat_cli.py "Do something with $item" --tools tool_name\n'
        "  done\n\n"
        "Write a bash script that accomplishes the task."
    )


def _sanitize_filename(task: str, max_length: int = 40) -> str:
    """Derive a safe filename from the task description."""
    slug = re.sub(r"[^\w\s-]", "", task.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:max_length] if slug else "plan"


def _build_artifact_response(
    script_text: str, task: str
) -> Dict[str, Any]:
    """Wrap a script in the Atlas artifact download format.

    Returns a dict with ``results``, ``artifacts``, and ``display``
    matching the convention used by pptx_generator and csv_reporter.
    """
    filename = f"{_sanitize_filename(task)}.sh"
    script_b64 = base64.b64encode(script_text.encode("utf-8")).decode("utf-8")

    return {
        "results": {
            "operation": "plan_with_tools",
            "task": task,
            "filename": filename,
            "message": "Bash script plan generated.",
        },
        "artifacts": [
            {
                "name": filename,
                "b64": script_b64,
                "mime": "application/x-sh",
                "viewer": "code",
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": filename,
            "mode": "replace",
            "viewer_hint": "code",
        },
    }


@mcp.tool
async def plan_with_tools(
    task: str,
    generated_script: Optional[str] = None,
    _mcp_data: Optional[Dict[str, Any]] = None,
    _atlas_user: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Plan how to accomplish a task by generating a bash script that uses the Atlas CLI.

    This tool receives metadata about all available MCP tools via _mcp_data
    injection. The calling client can provide ``generated_script`` from its own
    LLM run; otherwise this tool returns a deterministic starter script and
    embeds prompt guidance comments built from available tool metadata.

    Args:
        task: Description of the task to accomplish.
        generated_script: Optional script text generated by the calling client.
        _mcp_data: Automatically injected by Atlas UI with available tool
                   metadata. Do not provide this manually.
        _atlas_user: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with the bash script as a downloadable file.
    """
    mcp_data = _mcp_data or {}
    tools_reference = format_tools_for_llm(mcp_data)
    user_message = build_planning_prompt(task, tools_reference)

    def _default_script() -> str:
        return (
            "#!/bin/bash\n"
            "set -e\n\n"
            f"# Task: {task}\n"
            "# Replace the placeholder command below with client-generated\n"
            "# atlas_chat_cli.py steps based on the tools reference.\n"
            f"#\n# Tools reference:\n{tools_reference}\n\n"
            f'python atlas_chat_cli.py "{user_message}" --tools atlas_discover_sources\n'
        )

    server_count = len(mcp_data.get("available_servers", []))
    if ctx is not None:
        await ctx.report_progress(
            progress=0,
            total=2,
            message=f"Discovered {server_count} servers, preparing script artifact...",
        )

    script_text = (
        generated_script.strip()
        if isinstance(generated_script, str) and generated_script.strip()
        else _default_script()
    )
    response = _build_artifact_response(script_text, task)

    if ctx is not None:
        await ctx.report_progress(
            progress=2,
            total=2,
            message="Done.",
        )

    return response


if __name__ == "__main__":
    mcp.run(show_banner=False)
