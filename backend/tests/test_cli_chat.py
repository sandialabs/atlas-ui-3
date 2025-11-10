import subprocess
import sys
import os
from pathlib import Path
import pytest
import yaml

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
