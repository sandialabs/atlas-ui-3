#!/usr/bin/env python3
"""
Tool Planner MCP Server using FastMCP.

Uses _mcp_data injection and MCP sampling to generate bash scripts that
accomplish a user's task by calling the Atlas CLI with the appropriate tools.

Flow:
1. User asks the LLM to plan a task (e.g., "create a powerpoint about dogs")
2. LLM calls plan_with_tools, passing the user's request as `task`
3. Atlas UI injects `_mcp_data` (all available tools metadata) and `username`
4. Inside the tool:
   a. Convert `_mcp_data` into an LLM-friendly CLI tool reference
   b. Use ctx.sample() to ask the LLM to write a bash script using atlas_chat_cli.py
   c. Return the generated bash script
"""

import base64
import re
from typing import Any, Dict, Optional

from fastmcp import Context, FastMCP

mcp = FastMCP("Tool Planner")


PLANNER_SYSTEM_PROMPT = """\
You are a task planner. Given a user's request and a list of available CLI tools,
write a bash script that accomplishes the task using sequential calls to:

  python atlas_chat_cli.py "instruction" --tools tool_name

Rules:
- Each step is a plain CLI call with a natural language instruction and the right --tools flag
- Use -o filename.txt to save output to a file when a later step needs it
- Use loops (for/while) for repetitive operations
- Include set -e for error checking
- The script should be self-contained and runnable from the backend/ directory
- Do NOT use --json unless parsing structured output is truly necessary
- Keep instructions to the LLM clear and specific in each CLI call
- Be concise: at most 2 short comment lines between commands, no lengthy explanations
- The script should be easy to read at a glance
"""


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
    """Build the user message for the sampling call.

    Combines the task description, CLI usage instructions, and the
    formatted tools reference into a single prompt.

    Args:
        task: The user's task description.
        tools_reference: Output of ``format_tools_for_llm()``.

    Returns:
        The complete user-message string for ctx.sample().
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
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Plan how to accomplish a task by generating a bash script that uses the Atlas CLI.

    This tool receives metadata about all available MCP tools via _mcp_data
    injection, then uses LLM sampling to produce a runnable bash script.
    Each step in the script calls atlas_chat_cli.py with the appropriate
    --tools flag. The script is returned as a downloadable .sh file.

    Args:
        task: Description of the task to accomplish.
        _mcp_data: Automatically injected by Atlas UI with available tool
                   metadata. Do not provide this manually.
        username: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with the bash script as a downloadable file.
    """
    mcp_data = _mcp_data or {}
    tools_reference = format_tools_for_llm(mcp_data)
    user_message = build_planning_prompt(task, tools_reference)

    if ctx is None:
        fallback = (
            f"#!/bin/bash\nset -e\n"
            f"# Sampling unavailable -- cannot generate plan.\n"
            f"# Task: {task}\n"
            f"# Tools reference:\n{tools_reference}"
        )
        return _build_artifact_response(fallback, task)

    server_count = len(mcp_data.get("available_servers", []))
    await ctx.report_progress(
        progress=0, total=3,
        message=f"Discovered {server_count} servers, building prompt...",
    )

    await ctx.report_progress(
        progress=1, total=3,
        message="Asking LLM to generate bash script...",
    )

    result = await ctx.sample(
        messages=user_message,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        temperature=0.3,
        max_tokens=10000,
    )

    await ctx.report_progress(
        progress=2, total=3,
        message="Packaging script as downloadable artifact...",
    )

    script_text = result.text or "#!/bin/bash\nset -e\n# Unable to generate plan"
    response = _build_artifact_response(script_text, task)

    await ctx.report_progress(
        progress=3, total=3,
        message="Done.",
    )

    return response


if __name__ == "__main__":
    mcp.run(show_banner=False)
