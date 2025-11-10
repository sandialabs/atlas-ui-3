import subprocess
import sys
import os
from pathlib import Path
import pytest

# Path to the backend CLI script and working directory for subprocesses
CLI_CWD = os.path.join(os.path.dirname(__file__), '..')
CLI_PY = os.path.join(CLI_CWD, 'cli.py')


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
def test_cli_list_tools():
    """Tests the list-tools command."""
    command = [sys.executable, CLI_PY, "list-tools"]
    result = subprocess.run(command, capture_output=True, text=True, cwd=CLI_CWD)
    print(result.stderr)
    result.check_returncode()
    assert "Available Tools" in result.stdout


@pytest.mark.integration
def test_cli_help_commands():
    """Tests help command output."""
    result = subprocess.run(
        [sys.executable, CLI_PY, "--help"], capture_output=True, text=True, cwd=CLI_CWD
    )
    assert result.returncode == 0
    assert "Headless CLI for Atlas UI 3" in result.stdout
    assert "chat" in result.stdout
    assert "list-models" in result.stdout
    assert "list-tools" in result.stdout

    result = subprocess.run(
        [sys.executable, CLI_PY, "chat", "--help"], capture_output=True, text=True, cwd=CLI_CWD
    )
    assert result.returncode == 0
    assert "--model" in result.stdout
    assert "--agent-mode" in result.stdout
    assert "--tool" in result.stdout


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
