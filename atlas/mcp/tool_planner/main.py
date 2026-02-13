#!/usr/bin/env python3
"""
Tool Planner MCP Server using FastMCP.

Five tools for planning and executing multi-tool tasks:

1. plan_with_tools     -- (original) AI generates a bash script
2. plan_cli_steps      -- AI generates a JSON list of (prompt, tool) tuples
3. execute_cli_plan    -- Executes a (prompt, tool) tuple list via atlas-chat
4. generate_tool_functions -- Maps MCP tools to Python function stubs
5. plan_python_workflow    -- AI generates a .py workflow using mapped functions
"""

import asyncio
import base64
import json
import re
import sys
from typing import Any, Dict, List, Optional

from fastmcp import Context, FastMCP

mcp = FastMCP("Tool Planner")

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

BASH_PLANNER_PROMPT = """\
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

CLI_STEPS_PROMPT = """\
You are a task planner. Given a user's request and a list of available tools,
produce a JSON array of [prompt, tool] tuples representing sequential steps.

Each tuple is: ["natural language instruction for the LLM", "tool_server_name"]

Rules:
- Output ONLY valid JSON -- a single array of two-element arrays
- Each prompt should be a clear, self-contained instruction
- Each tool name must match one of the available server names exactly
- Order steps logically: data gathering before analysis, generation before review
- If a step needs output from a previous step, mention it in the prompt
- Do NOT wrap in markdown code fences, just raw JSON
"""

PYTHON_WORKFLOW_PROMPT = """\
You are a Python workflow generator. Given a task and a set of Python helper
functions that wrap Atlas CLI tools, write a complete Python script.

Rules:
- Output ONLY valid Python code, no markdown fences
- Use the provided atlas_tool_* functions for all tool interactions
- Use standard Python control flow: if/elif/else, for/while loops, try/except
- Assign return values to variables when subsequent steps need them
- Include a brief docstring at the top of the script
- The script should be self-contained and runnable with: python <script>.py
- Import helpers with: from atlas_tool_functions import *
"""

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def format_tools_for_llm(mcp_data: Dict[str, Any]) -> str:
    """Convert _mcp_data into a human-readable CLI tool reference."""
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
                for pname, pschema in properties.items():
                    if pname.startswith("_"):
                        continue
                    ptype = pschema.get("type", "any")
                    req = "required" if pname in required else "optional"
                    pdesc = pschema.get("description", "")
                    suffix = f": {pdesc}" if pdesc else ""
                    lines.append(f"      - {pname} ({ptype}, {req}){suffix}")
        lines.append("")
    return "\n".join(lines)


def _sanitize_filename(task: str, max_length: int = 40) -> str:
    """Derive a safe filename from a task description."""
    slug = re.sub(r"[^\w\s-]", "", task.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug[:max_length] if slug else "plan"


def _build_artifact_response(
    content: str,
    task: str,
    *,
    ext: str = "sh",
    mime: str = "application/x-sh",
    operation: str = "plan_with_tools",
    message: str = "Plan generated.",
) -> Dict[str, Any]:
    """Wrap content in the Atlas artifact download format."""
    filename = f"{_sanitize_filename(task)}.{ext}"
    b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    return {
        "results": {
            "operation": operation,
            "task": task,
            "filename": filename,
            "message": message,
        },
        "artifacts": [
            {"name": filename, "b64": b64, "mime": mime, "viewer": "code"}
        ],
        "display": {
            "open_canvas": True,
            "primary_file": filename,
            "mode": "replace",
            "viewer_hint": "code",
        },
    }


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped the output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


def _tools_as_python_stubs(mcp_data: Dict[str, Any]) -> str:
    """Generate a Python module with one wrapper function per MCP tool server."""
    servers = mcp_data.get("available_servers", [])
    if not servers:
        return "# No tools available\n"

    lines: list[str] = [
        "import subprocess",
        "import sys",
        "",
        "",
        "def _run_atlas_tool(prompt: str, tool: str, output_file: str = '') -> str:",
        '    """Run an atlas-chat CLI call and return stdout."""',
        '    cmd = [sys.executable, "atlas_chat_cli.py", prompt, "--tools", tool]',
        "    if output_file:",
        '        cmd.extend(["-o", output_file])',
        "    result = subprocess.run(cmd, capture_output=True, text=True, check=True)",
        "    return result.stdout",
        "",
        "",
    ]

    for server in servers:
        sname = server.get("server_name", "unknown")
        sdesc = server.get("description", "")
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", sname)

        lines.append(f"def atlas_tool_{safe}(prompt: str, output_file: str = '') -> str:")
        lines.append(f'    """Call the {sname} tool. {sdesc}"""')
        lines.append(f'    return _run_atlas_tool(prompt, "{sname}", output_file)')
        lines.append("")
        lines.append("")

    return "\n".join(lines)


def _tools_as_python_reference(mcp_data: Dict[str, Any]) -> str:
    """One-line-per-function reference for the LLM prompt."""
    servers = mcp_data.get("available_servers", [])
    if not servers:
        return "(No functions available)"

    lines = ["Available Python helper functions:"]
    for server in servers:
        sname = server.get("server_name", "unknown")
        sdesc = server.get("description", "")
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", sname)
        comment = f"  # {sdesc}" if sdesc else ""
        lines.append(
            f"  atlas_tool_{safe}(prompt: str, output_file: str = '') -> str{comment}"
        )
    return "\n".join(lines)


def build_planning_prompt(task: str, tools_reference: str) -> str:
    """Build the user message for the bash script sampling call."""
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


# ---------------------------------------------------------------------------
# Tool 1 (original): plan_with_tools -- bash script
# ---------------------------------------------------------------------------


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
    tools_ref = format_tools_for_llm(mcp_data)
    user_message = build_planning_prompt(task, tools_ref)

    if ctx is None:
        fallback = (
            f"#!/bin/bash\nset -e\n"
            f"# Sampling unavailable -- cannot generate plan.\n"
            f"# Task: {task}\n# Tools reference:\n{tools_ref}"
        )
        return _build_artifact_response(fallback, task)

    server_count = len(mcp_data.get("available_servers", []))
    await ctx.report_progress(
        progress=0, total=3,
        message=f"Discovered {server_count} servers, building prompt...",
    )
    await ctx.report_progress(progress=1, total=3, message="Asking LLM to generate bash script...")

    result = await ctx.sample(
        messages=user_message,
        system_prompt=BASH_PLANNER_PROMPT,
        temperature=0.3,
        max_tokens=10000,
    )

    await ctx.report_progress(progress=2, total=3, message="Packaging script...")
    script = result.text or "#!/bin/bash\nset -e\n# Unable to generate plan"
    response = _build_artifact_response(script, task)
    await ctx.report_progress(progress=3, total=3, message="Done.")
    return response


# ---------------------------------------------------------------------------
# Tool 2: plan_cli_steps -- JSON list of (prompt, tool) tuples
# ---------------------------------------------------------------------------


@mcp.tool
async def plan_cli_steps(
    task: str,
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Plan a task as a list of (prompt, tool) step tuples for sequential CLI execution.

    Uses LLM sampling to break a task into ordered steps. Each step is a
    [prompt, tool] pair that maps to: atlas-chat "prompt" --tools tool.
    The result is a JSON file you can feed to execute_cli_plan.

    Args:
        task: Description of the task to accomplish.
        _mcp_data: Automatically injected by Atlas UI with available tool metadata.
        username: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with a JSON step list as a downloadable file.
    """
    mcp_data = _mcp_data or {}
    tools_ref = format_tools_for_llm(mcp_data)

    user_message = (
        f"Task: {task}\n\n"
        f"Available tools (use the Server name as the tool value):\n{tools_ref}\n\n"
        "Produce a JSON array of [prompt, tool] tuples."
    )

    if ctx is None:
        fallback = json.dumps([["(sampling unavailable)", "unknown"]], indent=2)
        return _build_artifact_response(
            fallback, task, ext="json", mime="application/json",
            operation="plan_cli_steps", message="Step list generated (fallback).",
        )

    server_count = len(mcp_data.get("available_servers", []))
    await ctx.report_progress(
        progress=0, total=2,
        message=f"Discovered {server_count} servers, planning steps...",
    )

    result = await ctx.sample(
        messages=user_message,
        system_prompt=CLI_STEPS_PROMPT,
        temperature=0.3,
        max_tokens=5000,
    )

    raw = _strip_fences(result.text or "[]")
    await ctx.report_progress(progress=2, total=2, message="Done.")

    return _build_artifact_response(
        raw, task, ext="json", mime="application/json",
        operation="plan_cli_steps", message="CLI step list generated.",
    )


# ---------------------------------------------------------------------------
# Tool 3: execute_cli_plan -- run the tuple list via atlas-chat
# ---------------------------------------------------------------------------


@mcp.tool
async def execute_cli_plan(
    steps_json: str,
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Execute a list of (prompt, tool) step tuples sequentially via the Atlas CLI.

    Takes a JSON string with an array of [prompt, tool] pairs and runs each
    in order using: atlas-chat "prompt" --tools tool.
    Returns collected output from every step.

    Args:
        steps_json: JSON string of [[prompt, tool], ...] tuples.
        _mcp_data: Automatically injected by Atlas UI.
        username: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with JSON execution results.
    """
    try:
        steps: List = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return {"results": {"error": f"Invalid JSON: {exc}", "success": False}}

    if not isinstance(steps, list):
        return {"results": {"error": "Expected a JSON array of [prompt, tool] pairs."}}

    total = len(steps)
    outputs: list[Dict[str, Any]] = []

    for i, step in enumerate(steps):
        if not isinstance(step, (list, tuple)) or len(step) != 2:
            outputs.append({"step": i + 1, "error": f"Invalid step format: {step}"})
            continue

        prompt, tool = str(step[0]), str(step[1])

        if ctx:
            await ctx.report_progress(
                progress=i, total=total,
                message=f"Step {i + 1}/{total}: {tool} -- {prompt[:60]}",
            )

        cmd = [sys.executable, "atlas_chat_cli.py", prompt, "--tools", tool]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            outputs.append({
                "step": i + 1,
                "prompt": prompt,
                "tool": tool,
                "exit_code": proc.returncode,
                "output": stdout.decode("utf-8", errors="replace")[:5000],
                "stderr": (
                    stderr.decode("utf-8", errors="replace")[:2000]
                    if proc.returncode != 0 else ""
                ),
            })
        except Exception as exc:
            outputs.append({"step": i + 1, "prompt": prompt, "tool": tool, "error": str(exc)})

    if ctx:
        await ctx.report_progress(progress=total, total=total, message="All steps completed.")

    summary = json.dumps(outputs, indent=2)
    return _build_artifact_response(
        summary, "cli_plan_results", ext="json", mime="application/json",
        operation="execute_cli_plan", message=f"Executed {total} steps.",
    )


# ---------------------------------------------------------------------------
# Tool 4: generate_tool_functions -- Python stubs for each MCP tool
# ---------------------------------------------------------------------------


@mcp.tool
async def generate_tool_functions(
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Generate Python function stubs that map each MCP tool to an atlas-chat call.

    Produces a Python module where each tool server gets a wrapper function
    like atlas_tool_<server>(prompt, output_file) that invokes atlas-chat.
    This module can be imported by workflow scripts generated by
    plan_python_workflow.

    Args:
        _mcp_data: Automatically injected by Atlas UI with available tool metadata.
        username: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with a .py file containing function stubs.
    """
    mcp_data = _mcp_data or {}
    if ctx:
        await ctx.report_progress(progress=0, total=1, message="Generating Python stubs...")

    module_text = _tools_as_python_stubs(mcp_data)

    if ctx:
        await ctx.report_progress(progress=1, total=1, message="Done.")

    return _build_artifact_response(
        module_text, "atlas_tool_functions", ext="py", mime="text/x-python",
        operation="generate_tool_functions",
        message="Python tool function stubs generated.",
    )


# ---------------------------------------------------------------------------
# Tool 5: plan_python_workflow -- AI-generated .py workflow
# ---------------------------------------------------------------------------


@mcp.tool
async def plan_python_workflow(
    task: str,
    _mcp_data: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Generate a Python workflow script that accomplishes a task using tool functions.

    Maps all available MCP tools to Python helper functions, then uses LLM
    sampling to produce a complete Python script with control flow (if/elif,
    for loops, try/except). Returns both the workflow and the helper module.

    The advantage over bash scripts is native Python control structures for
    conditional logic, iteration, and error handling.

    Args:
        task: Description of the task to accomplish.
        _mcp_data: Automatically injected by Atlas UI with available tool metadata.
        username: The authenticated user (automatically injected by Atlas UI).

    Returns:
        Atlas artifact dict with a .py workflow and helper module.
    """
    mcp_data = _mcp_data or {}
    stubs_module = _tools_as_python_stubs(mcp_data)
    func_ref = _tools_as_python_reference(mcp_data)

    user_message = (
        f"Task: {task}\n\n"
        f"{func_ref}\n\n"
        "Each function takes a prompt string and an optional output_file path.\n"
        "They return the CLI output as a string.\n\n"
        "The helpers are in atlas_tool_functions.py (already generated).\n"
        "Import them with: from atlas_tool_functions import *\n\n"
        "Write a complete Python workflow script for this task.\n"
    )

    if ctx is None:
        fallback = (
            f'"""Workflow: {task}"""\n\n'
            "# Sampling unavailable -- cannot generate workflow.\n"
            "from atlas_tool_functions import *\n"
        )
        return _build_artifact_response(
            fallback, task, ext="py", mime="text/x-python",
            operation="plan_python_workflow", message="Workflow generated (fallback).",
        )

    server_count = len(mcp_data.get("available_servers", []))
    await ctx.report_progress(
        progress=0, total=3,
        message=f"Mapped {server_count} servers to Python functions...",
    )
    await ctx.report_progress(progress=1, total=3, message="Asking LLM to generate workflow...")

    result = await ctx.sample(
        messages=user_message,
        system_prompt=PYTHON_WORKFLOW_PROMPT,
        temperature=0.3,
        max_tokens=10000,
    )

    workflow_text = _strip_fences(result.text or "")
    await ctx.report_progress(progress=2, total=3, message="Packaging workflow...")

    wf_name = f"{_sanitize_filename(task)}_workflow.py"
    helpers_name = "atlas_tool_functions.py"
    wf_b64 = base64.b64encode(workflow_text.encode("utf-8")).decode("utf-8")
    helpers_b64 = base64.b64encode(stubs_module.encode("utf-8")).decode("utf-8")

    await ctx.report_progress(progress=3, total=3, message="Done.")

    return {
        "results": {
            "operation": "plan_python_workflow",
            "task": task,
            "filename": wf_name,
            "helpers_filename": helpers_name,
            "message": "Python workflow and helper module generated.",
        },
        "artifacts": [
            {"name": wf_name, "b64": wf_b64, "mime": "text/x-python", "viewer": "code"},
            {"name": helpers_name, "b64": helpers_b64, "mime": "text/x-python", "viewer": "code"},
        ],
        "display": {
            "open_canvas": True,
            "primary_file": wf_name,
            "mode": "replace",
            "viewer_hint": "code",
        },
    }


if __name__ == "__main__":
    mcp.run()
