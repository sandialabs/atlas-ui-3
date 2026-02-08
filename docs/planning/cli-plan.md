# Plan for Creating a Headless CLI (v2)

Last updated: 2026-01-19

This document outlines the plan to create a headless CLI for the application, allowing interaction with the core chat functionality without a frontend. This version incorporates feedback to add resource discovery and configuration file support.

## 1. High-Level Strategy

The goal is to reuse the existing backend logic to create a scriptable and user-friendly CLI.

1.  **New Entrypoint**: Create a new Python script (`atlas/cli.py`) that will serve as the entrypoint for the CLI.
2.  **Leverage `AppFactory`**: Use the existing `infrastructure.app_factory.app_factory` singleton to initialize and access all core services (`ChatService`, `MCPToolManager`, etc.), ensuring maximum code reuse.
3.  **Console Adapter**: Implement a new `CLIConnectionAdapter` class that conforms to the `ChatConnectionProtocol`. Instead of sending data over a WebSocket, it will print formatted JSON to the console.
4.  **CLI Framework**: Use `typer` to create a user-friendly command-line interface.
5.  **Lifecycle Management**: Replicate the `lifespan` context manager from `atlas/main.py` to ensure that services are initialized and shut down gracefully.
6.  **Resource Discovery**: Add commands (`list-models`, `list-tools`, `list-rag-sources`) to allow users to query the available LLMs, tools, and data sources from the backend configuration.
7.  **Configuration File**: Support a YAML configuration file (`--config`) to reduce command-line verbosity. The `chat` command will load settings from this file, which can be overridden by specific CLI arguments.

## 2. Code Implementation

### 2.1. Add `typer` Dependency

The `typer` library is required to build the CLI. `PyYAML` is already a dependency.

**File:** `pyproject.toml`
**Change:** Add `typer[all]` to the `dependencies` list.

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -25,5 +25,6 @@
     "opentelemetry-api",
     "opentelemetry-sdk",
     "opentelemetry-instrumentation-logging",
     "opentelemetry-exporter-otlp",
-    "opentelemetry-instrumentation-fastapi"
+    "opentelemetry-instrumentation-fastapi",
+    "typer[all]"
 ]
 
 [build-system]
```

### 2.2. Create the CLI Script

This new file will contain all the logic for the CLI application.

**File:** `atlas/cli.py` (New)
**Content:**

```python
"""
Headless CLI for interacting with the Atlas UI 3 backend.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import typer
import yaml
from rich.console import Console
from rich.table import Table

from infrastructure.app_factory import app_factory
from interfaces.transport import ChatConnectionProtocol

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Rich console for pretty printing
console = Console()

# Global state for holding config
cli_state = {"config": {}}


class CLIConnectionAdapter(ChatConnectionProtocol):
    """A ChatConnectionProtocol that prints messages to the console."""
    def __init__(self, user_email: str):
        self.user_email = user_email

    async def send_json(self, message: Dict[str, Any]) -> None:
        message_type = message.get("type", "unknown")
        if message_type == "error":
            console.print(f"[bold red]ERROR: {message.get('message')}[/bold red]")
        else:
            console.print(f"--- [bold cyan]Received: {message_type}[/bold cyan] ---")
            console.print(json.dumps(message, indent=2))
        await asyncio.sleep(0)

    def get_user_email(self) -> str:
        return self.user_email


@asynccontextmanager
async def lifespan(app: typer.Typer):
    """Replicates the FastAPI lifespan manager for resource initialization."""
    logger.info("Initializing CLI and backend services...")
    mcp_manager = app_factory.get_mcp_manager()
    try:
        await mcp_manager.initialize_clients()
        await mcp_manager.discover_tools()
        await mcp_manager.discover_prompts()
        logger.info("MCP tools manager initialization complete.")
    except Exception as e:
        logger.error(f"Error during MCP initialization: {e}", exc_info=True)
    yield
    logger.info("Shutting down CLI and backend services...")
    await mcp_manager.cleanup()
    logger.info("Shutdown complete.")


app = typer.Typer()

@app.callback()
def main(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a YAML config file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    )
):
    """
    Headless CLI for Atlas UI 3.
    """
    if config:
        console.print(f"Loading config from: {config}")
        with open(config, "r") as f:
            cli_state["config"] = yaml.safe_load(f)

@app.command()
def list_models():
    """Lists all available LLM models."""
    console.print("[bold green]Available LLM Models:[/bold green]")
    config_manager = app_factory.get_config_manager()
    table = Table("ID", "Provider", "Type")
    for model in config_manager.llm_config.models:
        table.add_row(model.id, model.provider, model.type)
    console.print(table)

@app.command()
def list_tools():
    """Lists all available MCP tools."""
    console.print("[bold green]Available Tools (MCPs):[/bold green]")
    mcp_manager = app_factory.get_mcp_manager()
    table = Table("ID", "Name", "Description")
    for tool_id, tool in mcp_manager.tools.items():
        table.add_row(tool_id, tool.name, tool.description)
    console.print(table)

@app.command()
def chat(
    prompt: str = typer.Argument(..., help="The chat prompt to send."),
    model: Optional[str] = typer.Option(None, help="The model to use."),
    user_email: Optional[str] = typer.Option(None, help="The user email for the session."),
    agent_mode: Optional[bool] = typer.Option(None, "--agent-mode", help="Enable agent mode."),
    selected_tools: Optional[list[str]] = typer.Option(None, "--tool", help="Tool to select. Can be used multiple times."),
):
    """Starts a chat session from the command line."""
    
    # Merge options: CLI > config file > defaults
    config = cli_state["config"].get("chat", {})
    final_user_email = user_email or config.get("user_email", "cli-user@example.com")
    final_model = model or config.get("model", "default")
    final_agent_mode = agent_mode if agent_mode is not None else config.get("agent_mode", False)
    final_tools = selected_tools or config.get("selected_tools", [])

    async def _chat():
        session_id = uuid4()
        connection = CLIConnectionAdapter(final_user_email)
        chat_service = app_factory.create_chat_service(connection)

        console.print(f"[bold green]Starting chat session {session_id}[/bold green]")
        console.print(f"User: {final_user_email}, Model: {final_model}, Agent Mode: {final_agent_mode}, Tools: {final_tools}")

        await chat_service.handle_chat_message(
            session_id=session_id,
            content=prompt,
            model=final_model,
            selected_tools=final_tools,
            agent_mode=final_agent_mode,
            user_email=final_user_email,
            update_callback=connection.send_json,
            # Hardcoded defaults for params not exposed in CLI
            selected_prompts=[],
            selected_data_sources=[],
            agent_max_steps=10,
        )
        
        await asyncio.sleep(5)
        chat_service.end_session(session_id)
        console.print(f"[bold red]Chat session {session_id} ended.[/bold red]")

    asyncio.run(_chat())


if __name__ == "__main__":
    async def run_app():
        async with lifespan(app):
            app()
    asyncio.run(run_app())
```

## 3. Testing Plan

Tests will be updated to cover the new functionality.

**File:** `atlas/tests/test_cli.py` (New)
**Content:**

```python
import subprocess
import sys
from pathlib import Path
import pytest
import yaml

@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Creates a temporary config file for testing."""
    config_data = {
        "chat": {
            "model": "test-model-from-config",
            "user_email": "config-user@example.com",
            "selected_tools": ["tool1", "tool2"],
            "agent_mode": True,
        }
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file

@pytest.mark.integration
def test_cli_list_models():
    """Tests the list-models command."""
    command = [sys.executable, "atlas/cli.py", "list-models"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert "Available LLM Models" in result.stdout
    # The default mock LLM is gpt-4
    assert "gpt-4" in result.stdout

@pytest.mark.integration
def test_cli_chat_with_config_file(temp_config_file: Path):
    """Tests that the chat command correctly uses a config file."""
    command = [
        sys.executable,
        "atlas/cli.py",
        "--config",
        str(temp_config_file),
        "chat",
        "Hello from config",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    
    # Check that the settings from the config file were used
    assert "User: config-user@example.com" in result.stdout
    assert "Model: test-model-from-config" in result.stdout
    assert "Agent Mode: True" in result.stdout
    assert "Tools: ['tool1', 'tool2']" in result.stdout
    assert "error" not in result.stderr.lower()

@pytest.mark.integration
def test_cli_chat_override_config_with_cli_arg(temp_config_file: Path):
    """Tests that CLI arguments override config file settings."""
    command = [
        sys.executable,
        "atlas/cli.py",
        "--config",
        str(temp_config_file),
        "chat",
        "--model",
        "override-model",
        "Hello from override",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    
    # Check that the CLI argument for model was used, but other config settings were kept
    assert "User: config-user@example.com" in result.stdout
    assert "Model: override-model" in result.stdout
    assert "Agent Mode: True" in result.stdout
```

## 4. Documentation Updates

The documentation will be updated to explain the new features.

**File:** `docs/03_developer_guide.md` (or `README.md`)
**Change:** Add a new section about the CLI.

````markdown
---

### Headless CLI

The application includes a headless CLI for scripting, testing, and interacting with the backend without a UI.

**Running the CLI**

Use the `--help` flag to see all available commands and options.

```bash
python atlas/cli.py --help
```

**Discovering Resources**

You can list available models and tools that the backend is configured with.

```bash
# List available LLM models
python atlas/cli.py list-models

# List available tools (MCPs)
python atlas/cli.py list-tools
```

**Using a Configuration File**

To avoid passing many options to the `chat` command, you can use a YAML configuration file.

**Example `cli-config.yaml`:**
```yaml
chat:
  model: claude-3-sonnet-20240229
  user_email: my-user@example.com
  agent_mode: true
  selected_tools:
    - "mcp/calculator"
    - "mcp/duckduckgo"
```

**Chat Command**

The `chat` command runs a single-turn conversation.

**Usage:**
```bash
# Using a config file
python atlas/cli.py --config cli-config.yaml chat "What is 2+2 and what is the weather in Paris?"

# Overriding a config setting with a CLI flag
python atlas/cli.py --config cli-config.yaml chat --model gpt-4 "Tell me a joke."

# Without a config file
python atlas/cli.py chat --model gpt-4 --tool "mcp/calculator" "What is 5*5?"
```
````