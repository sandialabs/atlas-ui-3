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
async def main(
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
async def list_models():
    """Lists all available LLM models."""
    console.print("[bold green]Available LLM Models:[/bold green]")
    config_manager = app_factory.get_config_manager()
    table = Table("ID", "Model Name", "Provider", "Compliance Level")
    for model_id, model_config in config_manager.llm_config.models.items():
        table.add_row(
            model_id,
            model_config.model_name,
            "N/A", # Provider not directly available in ModelConfig
            model_config.compliance_level if model_config.compliance_level else "N/A"
        )
    console.print(table)

@app.command()
async def list_tools():
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

if __name__ == "__main__":
    async def run_app():
        async with lifespan(app):
            app()
    asyncio.run(run_app())
