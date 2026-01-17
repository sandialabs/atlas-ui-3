# CLI (Command-Line Interface)

**Last Updated:** 2025-11-14

The Atlas UI 3 includes a headless CLI for scripting, testing, and interacting with the backend without a UI. This is useful for:

- Automated testing and CI/CD pipelines
- Scripting batch operations
- Quick model or tool discovery
- Interactive chat sessions from the command line

## Prerequisites

- Python 3.11+
- uv package manager installed
- Backend dependencies installed (`uv pip install -r requirements.txt`)
- Activated virtual environment

## Getting Started

### View Available Commands

Use the `--help` flag to see all available commands and options:

```bash
cd backend
python cli.py --help
```

### Discovering Resources

You can list available models and tools that the backend is configured with:

```bash
# List available LLM models
python cli.py list-models

# List available tools (MCPs)
python cli.py list-tools
```

## Configuration File

To avoid passing many options to the `chat` command, you can use a YAML configuration file.

### Example Configuration

Create a file named `cli-config.yaml`:

```yaml
chat:
  model: claude-3-sonnet-20240229
  user_email: my-user@example.com
  agent_mode: true
  selected_tools:
    - "calculator"
    - "pptx_generator"
```

### Configuration Options

- **model**: The LLM model to use (must be configured in `config/defaults/llmconfig.yml`)
- **user_email**: User email for session tracking
- **agent_mode**: Enable agent mode (allows tool usage and multi-step reasoning)
- **selected_tools**: List of tool names to make available during the chat

## Chat Command

The `chat` command runs a single-turn conversation with the LLM.

### Basic Usage

```bash
# Using a config file
python cli.py --config cli-config.yaml chat "What is 2+2 and what is the weather in Paris?"

# Overriding a config setting with a CLI flag
python cli.py --config cli-config.yaml chat --model gpt-4 "Tell me a joke."

# Without a config file
python cli.py chat --model gpt-4 --user-email user@example.com "What is 5*5?"
```

### Chat Options

- `--model`: Override the model from config
- `--user-email`: Override the user email from config
- `--agent-mode`: Enable agent mode (flag only, no value needed)
- `--tool`: Add a tool to the selection (can be used multiple times)

### Examples

#### Simple Chat (No Agent Mode)

```bash
python cli.py chat --model gpt-4 --user-email user@example.com "Explain quantum computing"
```

#### Chat with Agent Mode and Tools

```bash
python cli.py chat \
  --model gpt-4 \
  --user-email user@example.com \
  --agent-mode \
  --tool calculator \
  --tool pptx_generator \
  "Calculate the compound interest on $10000 at 5% for 10 years and create a presentation"
```

#### Using Config File with Override

```bash
# Create config file first
cat > my-config.yaml << EOF
chat:
  model: claude-3-sonnet-20240229
  user_email: analyst@company.com
  agent_mode: true
  selected_tools:
    - calculator
    - pdfbasic
EOF

# Use config but override model
python cli.py --config my-config.yaml chat --model gpt-4 "Analyze this data..."
```

## Commands Reference

### list-models

Lists all available LLM models configured in the system.

```bash
python cli.py list-models
```

**Output:**
- Model ID
- Model Name
- Provider
- Compliance Level

### list-tools

Lists all available MCP tools. This command initializes MCP connections if needed.

```bash
python cli.py list-tools
```

**Output:**
- Server name
- Tool name
- Tool description

**Note:** First run may take longer as it initializes MCP tool servers.

### chat

Starts a single-turn chat session with the LLM.

```bash
python cli.py [--config FILE] chat [OPTIONS] PROMPT
```

**Arguments:**
- `PROMPT`: The message to send to the LLM (required)

**Options:**
- `--model TEXT`: The model to use
- `--user-email TEXT`: The user email for the session
- `--agent-mode`: Enable agent mode
- `--tool TEXT`: Tool to select (can be used multiple times)

**Global Options:**
- `--config, -c FILE`: Path to YAML config file

## Troubleshooting

### Common Issues

1. **"uv not found"**
   - Install uv package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`

2. **"Model X not found in configuration"**
   - Check available models with `python cli.py list-models`
   - Ensure the model is configured in `config/defaults/llmconfig.yml`

3. **"MCP initialization timed out"**
   - Some MCP servers may take time to start
   - Check MCP server configuration in `config/defaults/mcp.json`
   - Try running again after a moment

4. **Chat hangs or doesn't respond**
   - Ensure you have valid API keys configured in `.env`
   - Check logs in `logs/` directory for errors

### Debug Mode

To see detailed logging, check the console output or log files in the `logs/` directory.

## Testing

CLI tests are located in `backend/tests/test_cli.py`. Run them with:

```bash
cd backend
python -m pytest tests/test_cli.py -v
```

## Examples

For practical examples and ready-to-run scripts, see `scripts/cli_examples/` (if available).

```
 python cli.py  --help
                                                                                                                                                                      
 Usage: cli.py [OPTIONS] COMMAND [ARGS]...                                                                                                                            
                                                                                                                                                                      
 Headless CLI for Atlas UI 3.                                                                                                                                         
                                                                                                                                                                      
╭─ Options ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --config              -c      FILE  Path to a YAML config file.                                                                                                    │
│ --install-completion                Install completion for the current shell.                                                                                      │
│ --show-completion                   Show completion for the current shell, to copy it or customize the installation.                                               │
│ --help                              Show this message and exit.                                                                                                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ list-models   Lists all available LLM models.                                                                                                                      │
│ list-tools    Lists all available MCP tools.                                                                                                                       │
│ chat          Starts a chat session from the command line.                                                                                                         │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

/workspaces/atlas-ui-3/.venv/lib/python3.12/site-packages/litellm/llms/custom_httpx/async_client_cleanup.py:66: DeprecationWarning: There is no current event loop
  loop = asyncio.get_event_loop()
(.venv) @garland3 ➜ /workspaces/atlas-ui-3/backend (cli) $ 
```

And

```
python cli.py  list-models
Available LLM Models:
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ ID                      ┃ Model Name                      ┃ Provider ┃ Compliance Level ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ openrouter-llama-3-370B │ meta-llama/llama-3-70b-instruct │ N/A      │ External         │
│ openrouter-gpt-oss      │ openai/gpt-oss-120b             │ N/A      │ External         │
│ openrouter-qwen3-coder  │ qwen/qwen3-coder                │ N/A      │ External         │
│ gpt-4.1                 │ gpt-4.1                         │ N/A      │ External         │
│ gpt-4.1-nano            │ gpt-4.1-nano                    │ N/A      │ External         │
└─────────────────────────┴─────────────────────────────────┴──────────┴──────────────────┘
```