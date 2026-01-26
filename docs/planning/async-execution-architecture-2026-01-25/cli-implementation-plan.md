# Atlas CLI Chat Implementation Plan

**Created:** 2025-01-25

## Overview

Create a CLI tool that loads MCP servers and RAG sources, takes a prompt, runs the agent loop to completion, and returns plain text or JSON output. Designed for integration with external planning agents.

## Architecture Decision

**Use ChatService via AppFactory** (not direct agent loop access)

Rationale:
- Reuses existing orchestration, session management, and authorization logic
- AppFactory already wires all dependencies correctly
- ChatService handles message history, tool authorization, and mode routing
- Minimal new code required - just a CLI transport layer

## Integration Model: Planning Agent + Execution Agent

```
Planning Agent (external)
    |
    | creates plan steps
    v
Execution Agent (calls atlas-cli via subprocess)
    |
    | subprocess.run(["python", "-m", "backend.cli.atlas_chat_cli", ...])
    v
Atlas CLI
    |
    | ChatService -> Agent Loop -> MCP Tools/RAG
    v
JSON result (stdout) + exit code
```

The CLI is invoked as a subprocess by the execution agent. This provides:
- Process isolation
- Language-agnostic integration
- Clean failure handling via exit codes
- Structured output via JSON

## File Structure

```
backend/
  cli/
    __init__.py                    # Package exports
    atlas_chat_cli.py              # Main CLI entry point (~200 lines)
    cli_event_publisher.py         # EventPublisher implementation (~100 lines)
    output_formatter.py            # Text/JSON formatters (~80 lines)
```

## Implementation Steps

### Step 1: Create CLI Event Publisher

**File:** `backend/cli/cli_event_publisher.py`

Implement the `EventPublisher` protocol from `backend/interfaces/events.py`:

```python
class CLIEventPublisher:
    """Collects events during execution for CLI output."""

    def __init__(self, verbose: bool = False, quiet: bool = False):
        self.events: List[Dict] = []
        self.tool_executions: List[Dict] = []
        self.final_response: Optional[str] = None
        # ... stream progress to stderr if not quiet
```

Required protocol methods:
- `publish_chat_response()` - Capture final answer
- `publish_response_complete()` - Mark completion
- `publish_agent_update()` - Track agent steps
- `publish_tool_start()` / `publish_tool_complete()` - Track tool usage
- `publish_files_update()` - Handle file events
- `publish_canvas_content()` - Capture canvas output
- `publish_elicitation_request()` - CLI can skip (return immediately)
- `send_json()` - Generic event capture

### Step 2: Create Output Formatter

**File:** `backend/cli/output_formatter.py`

```python
@dataclass
class CLIResult:
    success: bool
    final_answer: str
    agent_steps: int
    tool_executions: List[Dict]
    execution_time_ms: float
    model_used: str
    error: Optional[str] = None

class OutputFormatter:
    @staticmethod
    def format_text(result: CLIResult, include_metadata: bool = False) -> str

    @staticmethod
    def format_json(result: CLIResult, pretty: bool = True) -> str
```

### Step 3: Create Main CLI

**File:** `backend/cli/atlas_chat_cli.py`

Key function:

```python
async def run_chat(args) -> CLIResult:
    # 1. Create AppFactory (reuse existing)
    factory = AppFactory()

    # 2. Initialize MCP (async)
    await factory.mcp_tools.initialize_clients()
    await factory.mcp_tools.discover_tools()

    # 3. Create CLI event publisher
    event_publisher = CLIEventPublisher(verbose=args.verbose, quiet=args.quiet)

    # 4. Create ChatService with event publisher
    # NOTE: Need to modify ChatService or create_chat_service to accept EventPublisher
    chat_service = ChatService(
        llm=factory.llm_caller,
        tool_manager=factory.mcp_tools,
        connection=None,
        config_manager=factory.config_manager,
        file_manager=factory.file_manager,
        event_publisher=event_publisher,
        session_repository=factory.session_repository,
    )

    # 5. Create session and run
    session_id = uuid4()
    await chat_service.create_session(session_id, args.user)

    response = await chat_service.handle_chat_message(
        session_id=session_id,
        content=args.prompt,
        model=args.model,
        agent_mode=args.agent,
        selected_tools=args.tools.split(",") if args.tools else None,
        # ... other params
    )

    # 6. Return CLIResult
    return CLIResult(...)
```

CLI arguments:
- `prompt` (positional) - The prompt to send
- `--model, -m` - LLM model (default from config)
- `--agent, -a` - Enable agent mode
- `--strategy, -s` - Agent strategy: react|think-act|act
- `--max-steps` - Max agent steps (default: 30)
- `--tools` - Comma-separated tool list
- `--data-sources, -d` - Comma-separated RAG sources
- `--json, -j` - Output JSON instead of text
- `--verbose, -v` - Show detailed events
- `--quiet, -q` - Only output final answer
- `--user, -u` - User email for auth context

### Step 4: Modify ChatService to Accept EventPublisher

**File:** `backend/application/chat/service.py`

Current `ChatService.__init__()` creates its own event publisher. Need to:
1. Add optional `event_publisher` parameter
2. Use provided publisher or create default

```python
def __init__(
    self,
    llm: LLMProtocol,
    tool_manager: Optional[ToolManagerProtocol] = None,
    connection: Optional[ChatConnectionProtocol] = None,
    config_manager: Optional[ConfigManager] = None,
    file_manager: Optional[Any] = None,
    event_publisher: Optional[EventPublisher] = None,  # NEW
    # ...
):
    # Use provided or create default
    self.event_publisher = event_publisher or self._create_default_publisher()
```

### Step 5: Update AppFactory

**File:** `backend/infrastructure/app_factory.py`

Add method to create ChatService with custom event publisher:

```python
def create_chat_service(
    self,
    connection: Optional[ChatConnectionProtocol] = None,
    event_publisher: Optional[EventPublisher] = None,  # NEW
) -> ChatService:
    return ChatService(
        # ... existing params ...
        event_publisher=event_publisher,
    )
```

### Step 6: Package Init and Entry Point

**File:** `backend/cli/__init__.py`

```python
from .atlas_chat_cli import main, run_chat
from .cli_event_publisher import CLIEventPublisher
from .output_formatter import CLIResult, OutputFormatter

__all__ = ["main", "run_chat", "CLIEventPublisher", "CLIResult", "OutputFormatter"]
```

## JSON Output Format

```json
{
  "success": true,
  "final_answer": "The result...",
  "metadata": {
    "agent_steps": 5,
    "tool_executions": [
      {"tool": "server_tool", "success": true, "result": "..."}
    ],
    "execution_time_ms": 2340,
    "model_used": "gpt-4"
  }
}
```

On error:
```json
{
  "success": false,
  "final_answer": "",
  "error": "Connection refused...",
  "metadata": {...}
}
```

## Example Usage

```bash
# Simple prompt
python -m backend.cli.atlas_chat_cli "What is 2+2?"

# Agent mode with tools
python -m backend.cli.atlas_chat_cli --agent --tools "filesystem_read" "List files in /tmp"

# JSON output for planning agent integration
python -m backend.cli.atlas_chat_cli --json --agent "Execute step 1" | jq .final_answer

# Specific model
python -m backend.cli.atlas_chat_cli --model claude-3 "Explain quantum computing"

# With RAG
python -m backend.cli.atlas_chat_cli --data-sources "docs:technical" "How do I deploy?"
```

## Planning Agent Integration

External planning agent can invoke CLI as subprocess:

```python
import subprocess
import json

def execute_step(prompt: str, tools: list) -> dict:
    result = subprocess.run(
        ["python", "-m", "backend.cli.atlas_chat_cli",
         "--json", "--agent", "--tools", ",".join(tools), prompt],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)

# Example usage in planning loop
plan_steps = [
    {"prompt": "Read config file", "tools": ["filesystem_read"]},
    {"prompt": "Analyze data", "tools": ["calculator"]},
    {"prompt": "Write report", "tools": ["filesystem_write"]},
]

for step in plan_steps:
    result = execute_step(step["prompt"], step["tools"])
    if not result["success"]:
        print(f"Step failed: {result['error']}")
        break
    print(f"Step completed in {result['metadata']['agent_steps']} steps")
```

## Critical Files to Modify

1. `backend/application/chat/service.py` - Add event_publisher parameter
2. `backend/infrastructure/app_factory.py` - Update create_chat_service()

## New Files to Create

1. `backend/cli/__init__.py`
2. `backend/cli/atlas_chat_cli.py`
3. `backend/cli/cli_event_publisher.py`
4. `backend/cli/output_formatter.py`

## Verification

1. Run `python -m backend.cli.atlas_chat_cli "Hello"` - should return greeting
2. Run `python -m backend.cli.atlas_chat_cli --json "Hello" | jq .` - should output valid JSON
3. Run `python -m backend.cli.atlas_chat_cli --agent --tools "canvas_canvas" "Create a simple HTML page"` - should execute tool
4. Run `./test/run_tests.sh backend` - all existing tests should pass
5. Run `ruff check backend/cli/` - no linting errors

## Future Considerations

- **Batch mode**: Read multiple prompts from a file or stdin
- **Session persistence**: Save/restore conversation state across invocations
- **Streaming mode**: Stream tokens to stderr for long-running tasks
- **Config override**: Allow CLI args to override MCP/RAG config paths
