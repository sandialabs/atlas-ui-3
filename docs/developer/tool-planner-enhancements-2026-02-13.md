# Tool Planner Enhancements (2026-02-13)

The `tool_planner` MCP server (`atlas/mcp/tool_planner/main.py`) now provides five tools for multi-tool task orchestration. The original bash script planner is preserved; four new tools add structured step planning, CLI execution, and Python workflow generation.

## Tools Overview

### 1. plan_with_tools (original)
Generates a bash script using `atlas_chat_cli.py` calls. Uses LLM sampling to write the script.

### 2. plan_cli_steps
Generates a JSON array of `[prompt, tool]` tuples. Each tuple represents one sequential step. Output is a downloadable `.json` file that can be fed directly to `execute_cli_plan`.

**Example output:**
```json
[
  ["Evaluate 2+2", "calculator"],
  ["Summarize the result in a report", "csv_reporter"]
]
```

### 3. execute_cli_plan
Takes a JSON string of `[prompt, tool]` tuples (from `plan_cli_steps`) and executes each step sequentially by invoking `atlas-chat "prompt" --tools tool`. Returns collected output from every step.

### 4. generate_tool_functions
Maps every available MCP tool server to a Python function stub:
```python
def atlas_tool_calculator(prompt: str, output_file: str = '') -> str:
    """Call the calculator tool."""
    return _run_atlas_tool(prompt, "calculator", output_file)
```
Returns a downloadable `.py` module that workflow scripts can import.

### 5. plan_python_workflow
Uses LLM sampling to generate a complete Python workflow script. The AI sees the available Python helper functions (from step 4) and writes a script using native Python control flow -- `if/elif/else`, `for`/`while` loops, `try/except` -- which is harder to express in bash. Returns two artifacts: the workflow script and the helper module.

## Architecture

All five tools share:
- `format_tools_for_llm()` -- converts `_mcp_data` into an LLM-readable tool reference
- `_build_artifact_response()` -- wraps output in the Atlas artifact download format
- `_strip_fences()` -- removes markdown code fences from LLM output
- `_tools_as_python_stubs()` / `_tools_as_python_reference()` -- Python-specific formatters

All tools accept the standard Atlas injected parameters (`_mcp_data`, `username`) and use `ctx.sample()` for LLM generation with `ctx.report_progress()` for status updates.

## Usage via CLI

```bash
# Plan CLI steps
atlas-chat "Create a presentation about dogs, then summarize it" --tools tool_planner

# The LLM will choose the appropriate tool_planner tool based on the request
```

## Usage via UI

Select the `tool_planner` server in the Atlas UI tool picker. The LLM will select the appropriate tool based on the user's request. Artifacts are displayed in the canvas with the code viewer.
