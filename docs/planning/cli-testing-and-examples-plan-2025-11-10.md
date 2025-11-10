# CLI Testing and Examples Enhancement Plan

**Created:** 2025-11-10
**Target Files:**
- `backend/cli.py`
- `backend/tests/test_cli.py`
- `scripts/cli_examples/` (new directory)

## Overview

This plan outlines tasks to enhance the Atlas UI 3 CLI with comprehensive unit tests and practical usage examples. The CLI provides headless access to chat functionality, model/tool listing, and configuration management.

---

## Part 1: Add Unit Tests to `test_cli.py`

### Current Test Coverage (Already Implemented)
The existing tests (`backend/tests/test_cli.py`) include:
- ✅ `test_cli_list_models()` - Integration test for listing models
- ✅ `test_cli_chat_with_config_file()` - Integration test for chat with config file
- ✅ `test_cli_chat_override_config_with_cli_arg()` - CLI args override config file settings

**Coverage Analysis:**
- Commands tested: `list-models`, `chat` (with config)
- Commands NOT tested: `list-tools`
- Scenarios NOT tested: chat without config, invalid config, error handling, unit tests for CLIConnectionAdapter

### New Tests to Add

#### 1.1 Test `list-tools` Command (NEW - High Priority)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_list_tools()`
**Type:** Integration test (marked with `@pytest.mark.integration`)

**What to test:**
- Run `python cli.py list-tools` as a subprocess
- Verify command exits with return code 0
- Verify output contains "Available Tools" text
- Verify output contains at least one tool entry (check MCP tools are listed)

**Implementation hints:**
```python
@pytest.mark.integration
def test_cli_list_tools():
    """Tests the list-tools command."""
    command = [sys.executable, "cli.py", "list-tools"]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)  # Debug output
    result.check_returncode()
    assert "Available Tools" in result.stdout
```

#### 1.2 Test Chat Command with Direct CLI Arguments (NEW)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_chat_with_cli_args_only()`
**Type:** Integration test

**What to test:**
- Run chat command with all arguments provided via CLI (no config file at all)
- Use arguments: `--model`, `--user-email`, `--agent-mode`, `--tool`
- Verify the output shows correct model, user email, agent mode, and tools

**Note:** This differs from existing tests by NOT using a config file. The existing `test_cli_chat_override_config_with_cli_arg()` test uses both config and CLI args.

**Example command:**
```bash
python cli.py chat \
  --model "gpt-4" \
  --user-email "test@example.com" \
  --agent-mode \
  --tool "tool1" \
  --tool "tool2" \
  "Test prompt"
```

**Implementation hints:**
```python
@pytest.mark.integration
def test_cli_chat_with_cli_args_only():
    """Tests chat command using only CLI arguments (no config file)."""
    command = [
        sys.executable,
        "cli.py",
        "chat",
        "--model", "gpt-4",
        "--user-email", "cli-only@example.com",
        "--agent-mode",
        "--tool", "tool1",
        "--tool", "tool2",
        "Hello without config"
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)
    result.check_returncode()

    assert "User: cli-only@example.com" in result.stdout
    assert "Model: gpt-4" in result.stdout
    assert "Agent Mode: True" in result.stdout
    assert "tool1" in result.stdout
    assert "tool2" in result.stdout
```

#### 1.3 Test Chat Command Without Agent Mode (NEW)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_chat_without_agent_mode()`
**Type:** Integration test

**What to test:**
- Run chat without the `--agent-mode` flag
- Verify output shows `Agent Mode: False`
- Verify chat still executes successfully

**Implementation hints:**
```python
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
```

#### 1.4 Test Invalid Config File Handling (NEW - Error Case)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_invalid_config_file()`
**Type:** Integration test

**What to test:**
- Create a malformed YAML config file (invalid syntax)
- Run CLI with `--config` pointing to this file
- Verify command fails gracefully with appropriate error message or traceback

**Implementation hints:**
```python
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
```

#### 1.5 Test Nonexistent Config File (NEW - Error Case)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_nonexistent_config_file()`
**Type:** Integration test

**What to test:**
- Run CLI with `--config` pointing to a file that doesn't exist
- Verify command fails with appropriate error (typer should handle this via file validation)

**Implementation hints:**
```python
@pytest.mark.integration
def test_cli_nonexistent_config_file():
    """Tests handling of nonexistent config file."""
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
```

#### 1.6 Unit Test for CLIConnectionAdapter (NEW - Unit Test)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_connection_adapter()`
**Type:** Unit test (no `@pytest.mark.integration`)

**What to test:**
- Create instance of `CLIConnectionAdapter`
- Test `get_user_email()` returns correct email
- Test `send_json()` with different message types (error, token_stream, etc.)
- Verify console output formatting

**Note:** This is a TRUE unit test (not subprocess-based). It directly imports and tests the CLIConnectionAdapter class.

**Implementation hints:**
```python
import pytest
import sys
import os

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli import CLIConnectionAdapter

@pytest.mark.asyncio
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
```

#### 1.7 Test Chat with Multiple Tools (OPTIONAL - May be covered)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_chat_with_multiple_tools()`
**Type:** Integration test

**What to test:**
- Run chat with multiple `--tool` flags explicitly
- Verify all tools appear in the output

**Note:** The existing `test_cli_chat_with_config_file()` already tests multiple tools via config file. This test would verify the `--tool` flag can be repeated.

**Implementation hints:**
```python
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
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stderr)
    result.check_returncode()

    # Verify all three tools are listed
    assert "filesystem" in result.stdout
    assert "web-search" in result.stdout
    assert "calculator" in result.stdout
```

#### 1.8 Test Help Command (NEW)
**File:** `backend/tests/test_cli.py`
**Function name:** `test_cli_help_commands()`
**Type:** Integration test

**What to test:**
- Run `python cli.py --help` and verify general help text appears
- Run `python cli.py chat --help` and verify chat-specific help appears
- Run `python cli.py list-models --help` and verify command help

**Implementation hints:**
```python
@pytest.mark.integration
def test_cli_help_commands():
    """Tests help command output."""
    # Test main help
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "Headless CLI for Atlas UI 3" in result.stdout
    assert "chat" in result.stdout
    assert "list-models" in result.stdout
    assert "list-tools" in result.stdout

    # Test chat command help
    result = subprocess.run(
        [sys.executable, "cli.py", "chat", "--help"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "--model" in result.stdout
    assert "--agent-mode" in result.stdout
    assert "--tool" in result.stdout
```

---

## Part 2: Create CLI Usage Examples

### Directory Structure
Create the following structure:
```
scripts/
└── cli_examples/
    ├── README.md
    ├── example-config.yaml
    ├── 01-list-models.sh
    ├── 02-list-tools.sh
    ├── 03-simple-chat.sh
    ├── 04-chat-with-config.sh
    ├── 05-agent-mode-chat.sh
    ├── 06-chat-with-tools.sh
    └── 07-advanced-chat.sh
```

### 2.1 Create README.md
**File:** `scripts/cli_examples/README.md`
**Content:** Overview of CLI examples, prerequisites, and usage instructions

**Required sections:**
1. Introduction - What is the Atlas UI 3 CLI
2. Prerequisites - Python 3.11+, uv package manager, activated venv
3. Setup Instructions - How to run examples from the scripts directory
4. Example Descriptions - Brief description of each example script
5. Troubleshooting - Common issues and solutions

**Template:**
```markdown
# Atlas UI 3 CLI Examples

**Last updated:** 2025-11-10

## Introduction
These examples demonstrate how to use the Atlas UI 3 headless CLI for various tasks.

## Prerequisites
- Python 3.11+
- uv package manager installed
- Backend dependencies installed (`uv pip install -r requirements.txt`)
- Activated virtual environment

## Setup
From the repository root:
```bash
cd backend
python cli.py --help
```

## Examples
1. `01-list-models.sh` - List all available LLM models
2. `02-list-tools.sh` - List all available MCP tools
...

## Troubleshooting
- **Error: "uv not found"**: Install uv package manager
- **Error: "ModuleNotFoundError"**: Activate venv and install dependencies
```

### 2.2 Create example-config.yaml
**File:** `scripts/cli_examples/example-config.yaml`
**Purpose:** Sample configuration file showing all available options

**Content structure:**
```yaml
# Example Atlas UI 3 CLI configuration
# Last updated: 2025-11-10

chat:
  # Default model to use (can be overridden with --model)
  model: "gpt-4"

  # User email for session tracking
  user_email: "example@company.com"

  # Enable agent mode (allows tool usage and multi-step reasoning)
  agent_mode: true

  # Pre-selected tools (can be augmented with --tool flags)
  selected_tools:
    - "filesystem"
    - "web-search"
```

### 2.3 Create 01-list-models.sh
**File:** `scripts/cli_examples/01-list-models.sh`
**Purpose:** Demonstrate listing available models

```bash
#!/bin/bash
# Example 1: List all available LLM models
# This shows all models configured in config/defaults/llmconfig.yml

cd "$(dirname "$0")/../../backend" || exit 1

echo "Listing all available LLM models..."
python cli.py list-models
```

### 2.4 Create 02-list-tools.sh
**File:** `scripts/cli_examples/02-list-tools.sh`
**Purpose:** Demonstrate listing available MCP tools

```bash
#!/bin/bash
# Example 2: List all available MCP tools
# This shows all tools from configured MCP servers

cd "$(dirname "$0")/../../backend" || exit 1

echo "Listing all available MCP tools..."
python cli.py list-tools
```

### 2.5 Create 03-simple-chat.sh
**File:** `scripts/cli_examples/03-simple-chat.sh`
**Purpose:** Basic chat without config file

```bash
#!/bin/bash
# Example 3: Simple chat with direct arguments
# No agent mode, no tools, just basic LLM interaction

cd "$(dirname "$0")/../../backend" || exit 1

echo "Starting simple chat session..."
python cli.py chat \
  --model "gpt-4" \
  --user-email "user@example.com" \
  "What is the capital of France?"
```

### 2.6 Create 04-chat-with-config.sh
**File:** `scripts/cli_examples/04-chat-with-config.sh`
**Purpose:** Demonstrate using a config file

```bash
#!/bin/bash
# Example 4: Chat using configuration file
# Settings come from example-config.yaml

cd "$(dirname "$0")/../../backend" || exit 1

CONFIG_PATH="../scripts/cli_examples/example-config.yaml"

echo "Starting chat with config file..."
python cli.py \
  --config "$CONFIG_PATH" \
  chat \
  "Explain how configuration files work in Atlas UI 3"
```

### 2.7 Create 05-agent-mode-chat.sh
**File:** `scripts/cli_examples/05-agent-mode-chat.sh`
**Purpose:** Demonstrate agent mode for multi-step reasoning

```bash
#!/bin/bash
# Example 5: Chat with agent mode enabled
# Agent mode allows the LLM to use tools and perform multi-step tasks

cd "$(dirname "$0")/../../backend" || exit 1

echo "Starting agent mode chat session..."
python cli.py chat \
  --model "gpt-4" \
  --user-email "user@example.com" \
  --agent-mode \
  "Search the web for the latest news on AI and summarize the top 3 stories"
```

### 2.8 Create 06-chat-with-tools.sh
**File:** `scripts/cli_examples/06-chat-with-tools.sh`
**Purpose:** Demonstrate selecting specific tools

```bash
#!/bin/bash
# Example 6: Chat with specific tools selected
# Multiple --tool flags can be used to select tools

cd "$(dirname "$0")/../../backend" || exit 1

echo "Starting chat with selected tools..."
python cli.py chat \
  --model "gpt-4" \
  --user-email "user@example.com" \
  --agent-mode \
  --tool "filesystem" \
  --tool "web-search" \
  "Find information about Python async/await and save it to a file"
```

### 2.9 Create 07-advanced-chat.sh
**File:** `scripts/cli_examples/07-advanced-chat.sh`
**Purpose:** Demonstrate overriding config with CLI arguments

```bash
#!/bin/bash
# Example 7: Advanced - Override config file with CLI arguments
# CLI arguments take precedence over config file settings

cd "$(dirname "$0")/../../backend" || exit 1

CONFIG_PATH="../scripts/cli_examples/example-config.yaml"

echo "Starting chat with mixed config and CLI arguments..."
python cli.py \
  --config "$CONFIG_PATH" \
  chat \
  --model "claude-3-opus-20240229" \
  --tool "calculator" \
  "Calculate the compound interest on $10000 at 5% annual rate for 10 years"

echo ""
echo "Note: The model was overridden to Claude Opus via CLI argument"
echo "      but other settings (user_email, agent_mode) came from config"
```

---

## Implementation Checklist

### Part 1: Unit Tests

**Already Completed (DO NOT ADD AGAIN):**
- [x] `test_cli_list_models()` - Tests list-models command
- [x] `test_cli_chat_with_config_file()` - Tests chat with config file
- [x] `test_cli_chat_override_config_with_cli_arg()` - Tests CLI arg override

**New Tests to Add:**
- [ ] Add `test_cli_list_tools()` - **HIGH PRIORITY** - Test list-tools command
- [ ] Add `test_cli_chat_with_cli_args_only()` - Test chat with CLI args only (no config)
- [ ] Add `test_cli_chat_without_agent_mode()` - Test chat without agent mode
- [ ] Add `test_cli_invalid_config_file()` - Test invalid config error handling
- [ ] Add `test_cli_nonexistent_config_file()` - Test missing config file error
- [ ] Add `test_cli_connection_adapter()` - **TRUE UNIT TEST** - Test CLIConnectionAdapter class
- [ ] Add `test_cli_chat_with_multiple_tools()` - OPTIONAL - Test multiple --tool flags
- [ ] Add `test_cli_help_commands()` - Test help output for main and subcommands

**Validation:**
- [ ] Run all tests: `bash run_test_shortcut.sh`
- [ ] Verify all new tests pass
- [ ] Verify total test count increased by 6-7 tests

### Part 2: CLI Examples
- [ ] Create `scripts/cli_examples/` directory
- [ ] Write `README.md` with complete documentation
- [ ] Create `example-config.yaml` with all config options
- [ ] Write `01-list-models.sh` script
- [ ] Write `02-list-tools.sh` script
- [ ] Write `03-simple-chat.sh` script
- [ ] Write `04-chat-with-config.sh` script
- [ ] Write `05-agent-mode-chat.sh` script
- [ ] Write `06-chat-with-tools.sh` script
- [ ] Write `07-advanced-chat.sh` script
- [ ] Make all `.sh` files executable: `chmod +x scripts/cli_examples/*.sh`
- [ ] Test each example script manually to verify it works
- [ ] Update main project README to mention CLI examples (optional)

---

## Testing the Implementation

### Verify Unit Tests
```bash
cd /workspaces/atlas-ui-3
bash run_test_shortcut.sh
```

All tests should pass, including the new CLI tests.

### Verify Examples
```bash
cd /workspaces/atlas-ui-3

# Test each example
bash scripts/cli_examples/01-list-models.sh
bash scripts/cli_examples/02-list-tools.sh
bash scripts/cli_examples/03-simple-chat.sh
# ... and so on
```

---

## Additional Notes for Junior Engineer

1. **Testing Best Practices:**
   - Always mark integration tests with `@pytest.mark.integration`
   - Use `tmp_path` fixture for creating temporary files in tests
   - Include debug `print(result.stderr)` statements to help troubleshoot failures
   - Test both success and failure cases

2. **Example Script Best Practices:**
   - Always include a shebang (`#!/bin/bash`) at the top
   - Add comments explaining what each example demonstrates
   - Use `cd "$(dirname "$0")/../../backend"` to ensure scripts work from any directory
   - Include echo statements to provide user feedback

3. **Common Pitfalls:**
   - Remember to activate the venv before running CLI commands
   - The CLI path should be relative to the backend directory
   - Config file paths in examples should be relative to where the CLI runs (backend/)
   - Some tests may timeout - the CLI chat command has a 5-second sleep by default

4. **Resources:**
   - CLI implementation: `backend/cli.py:1-164`
   - Existing tests: `backend/tests/test_cli.py:1-146`
   - Pytest docs: https://docs.pytest.org/
   - Typer docs: https://typer.tiangolo.com/

5. **Getting Help:**
   - If tests fail, check the stderr output first
   - Review how existing tests are structured
   - Ask for clarification on test assertions if needed
   - Test examples manually before considering them complete
