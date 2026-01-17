# Atlas UI 3 CLI Examples

**Last updated:** 2025-11-10

## Introduction
These examples demonstrate how to use the Atlas UI 3 headless CLI for common tasks such as listing models, listing MCP tools, and performing chat sessions from the command line.

## Prerequisites
- Python 3.11+
- The repository's backend dependencies installed in an activated virtual environment. The project uses `uv` as the recommended environment manager (see project docs).

## Setup
From the repository root:

```bash
cd backend
# Show CLI help
python cli.py --help
```

If you prefer to run the example scripts in this directory, make them executable and run them from the repository root. Example scripts assume `python` resolves to a Python executable that can import and run the backend code.

## Example scripts
1. `01-list-models.sh` - List all available LLM models
2. `02-list-tools.sh` - List all available MCP tools
3. `03-simple-chat.sh` - Simple chat using CLI args
4. `04-chat-with-config.sh` - Chat using a YAML config file
5. `05-agent-mode-chat.sh` - Chat with agent mode enabled
6. `06-chat-with-tools.sh` - Chat while specifying multiple tools
7. `07-advanced-chat.sh` - Advanced example demonstrating more options

## example-config.yaml
See `example-config.yaml` in this directory for a sample configuration file showing common CLI options.

## Troubleshooting
- "uv not found": install `uv` or use a standard venv and install dependencies via `pip install -r requirements.txt` (see repo README).
- "ModuleNotFoundError": ensure you run commands from the repository root and have activated the project's venv.
- If a CLI command fails with YAML parse error while using `--config`, check that the YAML file is valid.

## License
Follow the repository license for reuse of these scripts.
