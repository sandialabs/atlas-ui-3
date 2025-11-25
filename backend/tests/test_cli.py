import subprocess
import sys
import os
from pathlib import Path
import pytest
import yaml

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli import CLIConnectionAdapter

# Path to the backend CLI script and working directory for subprocesses
CLI_CWD = os.path.join(os.path.dirname(__file__), '..')
CLI_PY = os.path.join(CLI_CWD, 'cli.py')


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
    command = [sys.executable, CLI_PY, "list-models"]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "Available LLM Models" in result.stdout
    assert "gpt-4" in result.stdout


@pytest.mark.integration
def test_cli_chat_with_config_file(temp_config_file: Path):
    """Tests that the chat command correctly uses a config file."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        str(temp_config_file),
        "chat",
        "Hello from config",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "User: config-user@example.com" in result.stdout
    assert "Model: test-model-from-config" in result.stdout
    assert "Agent Mode: True" in result.stdout


@pytest.mark.integration
def test_cli_list_tools():
    """Tests the list-tools command."""
    command = [sys.executable, CLI_PY, "list-tools"]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()
    assert "Available Tools" in result.stdout


@pytest.mark.integration
def test_cli_chat_with_cli_args_only():
    """Tests chat command using only CLI arguments (no config file)."""
    command = [
        sys.executable,
        CLI_PY,
        "chat",
        "--model",
        "gpt-4",
        "--user-email",
        "cli-only@example.com",
        "--agent-mode",
        "--tool",
        "tool1",
        "--tool",
        "tool2",
        "Hello without config",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "User: cli-only@example.com" in result.stdout
    assert "Model: gpt-4" in result.stdout
    assert "Agent Mode: True" in result.stdout
    assert "tool1" in result.stdout
    assert "tool2" in result.stdout


@pytest.mark.integration
def test_cli_chat_without_agent_mode():
    """Tests chat command without agent mode."""
    command = [
        sys.executable,
        CLI_PY,
        "chat",
        "--model",
        "gpt-4",
        "--user-email",
        "noagent@example.com",
        "Simple question without agent mode",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "User: noagent@example.com" in result.stdout
    assert "Agent Mode: False" in result.stdout


@pytest.mark.integration
def test_cli_invalid_config_file(tmp_path: Path):
    """Tests handling of invalid config file."""
    config_file = tmp_path / "invalid.yaml"
    with open(config_file, "w") as f:
        f.write("invalid: yaml: syntax: [[[")

    command = [sys.executable, CLI_PY, "--config", str(config_file), "list-models"]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)

    assert result.returncode != 0
    assert "error" in result.stderr.lower() or "exception" in result.stderr.lower()


@pytest.mark.integration
def test_cli_nonexistent_config_file():
    """Tests handling of nonexistent config file."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        "/nonexistent/path/config.yaml",
        "list-models",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)

    assert result.returncode != 0


@pytest.mark.asyncio
async def test_cli_connection_adapter(capsys):
    """Unit test for CLIConnectionAdapter."""
    adapter = CLIConnectionAdapter("test@example.com")

    assert adapter.get_user_email() == "test@example.com"

    await adapter.send_json({"type": "error", "message": "Test error"})
    captured = capsys.readouterr()
    assert "ERROR: Test error" in captured.out

    await adapter.send_json({"type": "token_stream", "content": "Hello"})
    captured = capsys.readouterr()
    assert "Received: token_stream" in captured.out
    assert "Hello" in captured.out


@pytest.mark.integration
def test_cli_chat_with_multiple_tools():
    """Tests chat with multiple --tool flags."""
    command = [
        sys.executable,
        CLI_PY,
        "chat",
        "--model",
        "gpt-4",
        "--agent-mode",
        "--tool",
        "filesystem",
        "--tool",
        "web-search",
        "--tool",
        "calculator",
        "Use all three tools",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "filesystem" in result.stdout
    assert "web-search" in result.stdout
    assert "calculator" in result.stdout


@pytest.mark.integration
def test_cli_help_commands():
    """Tests help command output."""
    # Set NO_COLOR to disable rich formatting
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    
    result = subprocess.run(
        [sys.executable, CLI_PY, "--help"], capture_output=True, text=True, cwd=CLI_CWD, env=env
    )
    assert result.returncode == 0
    assert "Headless CLI for Atlas UI 3" in result.stdout
    assert "chat" in result.stdout
    assert "list-models" in result.stdout
    assert "list-tools" in result.stdout

    result = subprocess.run(
        [sys.executable, CLI_PY, "chat", "--help"], capture_output=True, text=True, cwd=CLI_CWD, env=env
    )
    assert result.returncode == 0
    assert "model" in result.stdout.lower()
    assert "agent" in result.stdout.lower()
    assert "tool" in result.stdout.lower()


@pytest.mark.integration
def test_cli_chat_override_config_with_cli_arg(temp_config_file: Path):
    """Tests that CLI arguments override config file settings."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        str(temp_config_file),
        "chat",
        "--model",
        "override-model",
        "Hello from override",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()

    assert "User: config-user@example.com" in result.stdout
    assert "Model: override-model" in result.stdout
    assert "Agent Mode: True" in result.stdout


# --- Additional Unit Tests for CLI ---

@pytest.mark.asyncio
async def test_cli_connection_adapter_unknown_message_type(capsys):
    """Unit test for CLIConnectionAdapter with unknown message type."""
    adapter = CLIConnectionAdapter("test@example.com")
    
    await adapter.send_json({"type": "custom_type", "data": "custom_data"})
    captured = capsys.readouterr()
    assert "Received: custom_type" in captured.out
    assert "custom_data" in captured.out


@pytest.mark.asyncio
async def test_cli_connection_adapter_empty_message(capsys):
    """Unit test for CLIConnectionAdapter with empty message."""
    adapter = CLIConnectionAdapter("test@example.com")
    
    await adapter.send_json({})
    captured = capsys.readouterr()
    assert "Received: unknown" in captured.out


@pytest.mark.asyncio
async def test_cli_connection_adapter_nested_json(capsys):
    """Unit test for CLIConnectionAdapter with nested JSON structure."""
    adapter = CLIConnectionAdapter("test@example.com")
    
    await adapter.send_json({
        "type": "tool_result",
        "result": {
            "status": "success",
            "data": {"key": "value", "nested": {"deep": True}}
        }
    })
    captured = capsys.readouterr()
    assert "Received: tool_result" in captured.out
    assert "success" in captured.out
    assert "nested" in captured.out


def test_cli_connection_adapter_user_email():
    """Unit test for CLIConnectionAdapter user email getter."""
    adapter = CLIConnectionAdapter("user1@example.com")
    assert adapter.get_user_email() == "user1@example.com"
    
    adapter2 = CLIConnectionAdapter("different-user@domain.org")
    assert adapter2.get_user_email() == "different-user@domain.org"


@pytest.mark.integration
def test_cli_list_models_help():
    """Tests list-models command help output."""
    result = subprocess.run(
        [sys.executable, CLI_PY, "list-models", "--help"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD
    )
    assert result.returncode == 0
    assert "Lists all available LLM models" in result.stdout


@pytest.mark.integration
def test_cli_list_tools_help():
    """Tests list-tools command help output."""
    result = subprocess.run(
        [sys.executable, CLI_PY, "list-tools", "--help"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD
    )
    assert result.returncode == 0
    assert "Lists all available MCP tools" in result.stdout


@pytest.fixture
def empty_config_file(tmp_path: Path) -> Path:
    """Creates an empty config file for testing."""
    config_file = tmp_path / "empty_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump({}, f)
    return config_file


@pytest.mark.integration
def test_cli_chat_with_empty_config(empty_config_file: Path):
    """Tests chat command with an empty config file uses defaults."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        str(empty_config_file),
        "chat",
        "Hello with empty config",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()
    
    # Should use default values
    assert "User: cli-user@example.com" in result.stdout
    assert "Model: default" in result.stdout
    assert "Agent Mode: False" in result.stdout


@pytest.fixture
def partial_config_file(tmp_path: Path) -> Path:
    """Creates a partial config file with only some settings."""
    config_data = {
        "chat": {
            "model": "partial-model",
            # user_email and agent_mode not set
        }
    }
    config_file = tmp_path / "partial_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)
    return config_file


@pytest.mark.integration
def test_cli_chat_with_partial_config(partial_config_file: Path):
    """Tests chat command with a partial config file uses defaults for missing values."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        str(partial_config_file),
        "chat",
        "Hello with partial config",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()
    
    assert "Model: partial-model" in result.stdout
    assert "User: cli-user@example.com" in result.stdout  # default
    assert "Agent Mode: False" in result.stdout  # default


@pytest.mark.integration
def test_cli_chat_user_email_override(temp_config_file: Path):
    """Tests that user-email CLI arg overrides config file."""
    command = [
        sys.executable,
        CLI_PY,
        "--config",
        str(temp_config_file),
        "chat",
        "--user-email",
        "override@example.com",
        "Hello with email override",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()
    
    assert "User: override@example.com" in result.stdout
    assert "Model: test-model-from-config" in result.stdout  # from config


@pytest.mark.integration
def test_cli_version_not_implemented():
    """Tests that --version flag is handled (typer includes this by default if configured)."""
    result = subprocess.run(
        [sys.executable, CLI_PY, "--version"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD
    )
    # Typer doesn't include --version by default, so this should error
    assert result.returncode != 0


@pytest.mark.integration
def test_cli_unknown_command():
    """Tests that unknown commands are rejected."""
    result = subprocess.run(
        [sys.executable, CLI_PY, "unknown-command"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD
    )
    assert result.returncode != 0
    assert "No such command" in result.stderr or "Error" in result.stderr
