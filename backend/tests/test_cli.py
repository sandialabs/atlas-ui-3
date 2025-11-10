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
import subprocess
import sys
import os
from pathlib import Path
import pytest
import yaml

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli import CLIConnectionAdapter

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

    """

    Tests the list-models command.

    """

    command = [sys.executable, "cli.py", "list-models"]

    

    result = subprocess.run(command, capture_output=True, text=True)

    print(result.stderr) # Debug print

    result.check_returncode() # Raise CalledProcessError if non-zero

    

    assert "Available LLM Models" in result.stdout

    # The default mock LLM is gpt-4

    assert "gpt-4" in result.stdout



@pytest.mark.integration

def test_cli_chat_with_config_file(temp_config_file: Path):


# Path to the backend CLI script and working directory for subprocesses
CLI_CWD = os.path.join(os.path.dirname(__file__), '..')
CLI_PY = os.path.join(CLI_CWD, 'cli.py')
    """

    Tests that the chat command correctly uses a config file.

    """

    command = [

                sys.executable,

                "cli.py",

        "--config",

        str(temp_config_file),

        "chat",

        "Hello from config",

    ]

    command = [sys.executable, CLI_PY, "list-models"]

    print(result.stderr) # Debug print

    result.check_returncode() # Raise CalledProcessError if non-zero

    

    # Check that the settings from the config file were used

    assert "User: config-user@example.com" in result.stdout

    assert "Model: test-model-from-config" in result.stdout

    assert "Agent Mode: True" in result.stdout


                CLI_PY,
def test_cli_list_tools():
    """Tests the list-tools command."""
    command = [sys.executable, "cli.py", "list-tools"]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)  # Debug output
    result.check_returncode()
    assert "Available Tools" in result.stdout


@pytest.mark.integration
def test_cli_chat_with_cli_args_only():
    """Tests chat command using only CLI arguments (no config file)."""
    command = [
        sys.executable,
        "cli.py",
        "chat",
        "--model", "gpt-4",
    command = [sys.executable, CLI_PY, "list-tools"]
        "--agent-mode",
        "--tool", "tool1",
        "--tool", "tool2",
        "Hello without config"
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)
    result.check_returncode()

    assert "User: cli-only@example.com" in result.stdout
        CLI_PY,
    assert "Agent Mode: True" in result.stdout
    assert "tool1" in result.stdout
    assert "tool2" in result.stdout


@pytest.mark.integration
def test_cli_chat_without_agent_mode():
    """Tests chat command without agent mode."""
    command = [
        sys.executable,
        "cli.py",
        "chat",
        "--model", "gpt-4",
        "--user-email", "noagent@example.com",
        "Simple question without agent mode"
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)
    result.check_returncode()

    assert "User: noagent@example.com" in result.stdout
    assert "Agent Mode: False" in result.stdout

        CLI_PY,
@pytest.mark.integration
def test_cli_invalid_config_file(tmp_path: Path):
    """Tests handling of invalid config file."""
    config_file = tmp_path / "invalid.yaml"
    with open(config_file, "w") as f:
        f.write("invalid: yaml: syntax: [[[")  # Malformed YAML

    command = [sys.executable, "cli.py", "--config", str(config_file), "list-models"]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)

    # Should fail with non-zero exit code
    assert result.returncode != 0
    # Should have error message in stderr
    assert "error" in result.stderr.lower() or "exception" in result.stderr.lower()


@pytest.mark.integration
def test_cli_nonexistent_config_file():
    command = [sys.executable, CLI_PY, "--config", str(config_file), "list-models"]
    command = [
        sys.executable,
        "cli.py",
        "--config", "/nonexistent/path/config.yaml",
        "list-models"
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)

    # Typer should fail validation before command runs
    assert result.returncode != 0


        CLI_PY,
async def test_cli_connection_adapter(capsys):
    """Unit test for CLIConnectionAdapter."""
    adapter = CLIConnectionAdapter("test@example.com")

    # Test get_user_email
    assert adapter.get_user_email() == "test@example.com"

    # Test send_json with error message
    await adapter.send_json({"type": "error", "message": "Test error"})
    captured = capsys.readouterr()
    assert "ERROR: Test error" in captured.out

    # Test send_json with normal message
    await adapter.send_json({"type": "token_stream", "content": "Hello"})
    captured = capsys.readouterr()
    assert "Received: token_stream" in captured.out
    assert "Hello" in captured.out


@pytest.mark.integration
def test_cli_chat_with_multiple_tools():
    """Tests chat with multiple --tool flags."""
    command = [
        sys.executable,
        "cli.py",
        "chat",
        "--model", "gpt-4",
        "--agent-mode",
        "--tool", "filesystem",
        "--tool", "web-search",
        "--tool", "calculator",
        "Use all three tools"
    ]
        CLI_PY,
    print(result.stderr)
    result.check_returncode()

    # Verify all three tools are listed
    assert "filesystem" in result.stdout
    assert "web-search" in result.stdout
    assert "calculator" in result.stdout


@pytest.mark.integration
def test_cli_help_commands():
    """Tests help command output."""
    # Test main help
    result = subprocess.run(
        [sys.executable, CLI_PY, "--help"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD,
    )
    assert result.returncode == 0
    assert "Headless CLI for Atlas UI 3" in result.stdout
    assert "chat" in result.stdout
    assert "list-models" in result.stdout
    assert "list-tools" in result.stdout

    # Test chat command help
    result = subprocess.run(
        [sys.executable, CLI_PY, "chat", "--help"],
        capture_output=True,
        text=True,
        cwd=CLI_CWD,
    )
    assert result.returncode == 0
    assert "--model" in result.stdout
    assert "--agent-mode" in result.stdout
    assert "--tool" in result.stdout



@pytest.mark.integration

def test_cli_chat_override_config_with_cli_arg(temp_config_file: Path):

    """

    Tests that CLI arguments override config file settings.

    """

    command = [

                sys.executable,
                CLI_PY,
                "cli.py",

        "--config",

        str(temp_config_file),

        "chat",

        "--model",

        "override-model",

        "Hello from override",

    ]

    result = subprocess.run(command, capture_output=True, text=True)

    print(result.stderr) # Debug print

    result.check_returncode() # Raise CalledProcessError if non-zero

    

    # Check that the CLI argument for model was used, but other config settings were kept

    assert "User: config-user@example.com" in result.stdout

    assert "Model: override-model" in result.stdout

    assert "Agent Mode: True" in result.stdout
