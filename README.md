# Atlas UI 3

[![CI/CD Pipeline](https://github.com/sandialabs/atlas-ui-3/actions/workflows/ci.yml/badge.svg)](https://github.com/sandialabs/atlas-ui-3/actions/workflows/ci.yml)
[![Security Checks](https://github.com/sandialabs/atlas-ui-3/actions/workflows/security.yml/badge.svg)](https://github.com/sandialabs/atlas-ui-3/actions/workflows/security.yml)
[![Docker Image](https://ghcr-badge.egpl.dev/sandialabs/atlas-ui-3/latest_tag?trim=major&label=latest)](https://github.com/sandialabs/atlas-ui-3/pkgs/container/atlas-ui-3)
[![PyPI version](https://badge.fury.io/py/atlas-chat.svg)](https://badge.fury.io/py/atlas-chat)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![React 19](https://img.shields.io/badge/react-19.2-blue.svg)
![License MIT](https://img.shields.io/badge/license-MIT-blue.svg)

Atlas UI 3 is a secure chat application with MCP (Model Context Protocol) integration, developed by Sandia National Laboratories -- a U.S. Department of Energy national laboratory -- to support U.S. Government customers.



![Screenshot](docs/readme_img/screenshot-11-6-2025image.png)

## About the Project

**Atlas UI 3** is a full-stack LLM chat interface that supports multiple AI models, including those from OpenAI, Anthropic, and Google. Its core feature is the integration with the Model Context Protocol (MCP), which allows the AI assistant to connect to external tools and data sources, enabling complex, real-time workflows.

### Features

*   **Multi-LLM Support**: Connect to various LLM providers.
*   **MCP Integration**: Extend the AI's capabilities with custom tools.
*   **RAG Support**: Enhance responses with Retrieval-Augmented Generation.
*   **Secure and Configurable**: Features group-based access control, compliance levels, and a tool approval system.
*   **Modern Stack**: Built with React 19, FastAPI, and WebSockets.
*   **Python Package**: Install and use as a library or CLI tool.

## Installation

### Install from PyPI (Recommended for Users)

```bash
# Install the package
pip install atlas-chat

# Or with uv (faster)
uv pip install atlas-chat
```

### CLI Usage

After installation, three CLI tools are available:

```bash
# Set up configuration (run this first!)
atlas-init              # Creates .env and config/ in current directory
atlas-init --minimal    # Creates just a minimal .env file

# Chat with an LLM
atlas-chat "Hello, how are you?" --model gpt-4o
atlas-chat "Use the search tool" --tools server_tool1
atlas-chat --list-tools

# Start the server
atlas-server --port 8000
atlas-server --env /path/to/.env --config-folder /path/to/config
```

### Python API Usage

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()
    result = await client.chat("Hello, how are you?")
    print(result.message)

    # With options
    result = await client.chat(
        "Analyze this data",
        model="gpt-4o",
        selected_tools=["calculator", "search"],
        agent_mode=True,
    )
    await client.cleanup()

asyncio.run(main())

# Or use the sync wrapper
client = AtlasClient()
result = client.chat_sync("Hello!")
print(result.message)
```

## Quick Start (Development)

### Prerequisites

```bash
# Install uv package manager (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

### Development Installation (Editable Mode)

For development, install the package in **editable mode**. This creates a link from your Python environment to your local source code, so any changes you make to the code are immediately available without reinstalling.

```bash
# Install in editable mode with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

**What editable mode gives you:**
- Edit any Python file in `atlas/` and changes take effect immediately
- CLI commands (`atlas-chat`, `atlas-server`) use your local code
- Import `from atlas import AtlasClient` in scripts and get your local version
- No need to reinstall after making changes

**Example workflow:**
```bash
# Install once in editable mode
uv pip install -e .

# Edit code
vim atlas/atlas_client.py

# Run immediately with your changes - no reinstall needed
atlas-chat "test my changes"
python my_script.py  # uses updated AtlasClient
```

**Alternative: PYTHONPATH (if you can't use editable install)**
```bash
# Set PYTHONPATH manually when running
PYTHONPATH=/path/to/atlas-ui-3 python atlas/main.py
```

### Running the Application

**Linux/macOS:**
```bash
bash agent_start.sh
```

**Windows:**
```powershell
.\ps_agent_start.ps1
```

**Note for Windows users**: If you encounter frontend build errors related to Rollup dependencies, delete `frontend/package-lock.json` and `frontend/node_modules`, then run the script again.

Both scripts automatically detect and work with Docker or Podman. The `agent_start.sh` script builds the frontend, starts necessary services, and launches the backend server.

## Documentation

We have created a set of comprehensive guides to help you get the most out of Atlas UI 3.

*   **[Getting Started](./docs/getting-started/installation.md)**: The perfect starting point for all users. This guide covers how to get the application running with Docker or on your local machine.

*   **[Administrator's Guide](./docs/admin/README.md)**: For those who will deploy and manage the application. This guide details configuration, security settings, access control, and other operational topics.

*   **[Developer's Guide](./docs/developer/README.md)**: For developers who want to contribute to the project. It provides an overview of the architecture and instructions for creating new MCP servers.

## Docker / Podman

### Quick Start

```bash
# 1. Set up local config (copies defaults from atlas/config/)
atlas-init
# Edit .env to add your API keys

# 2. Build the image
podman build -t atlas-ui-3 .

# 3. Run with your local config mounted
podman run -p 8000:8000 \
  -v $(pwd)/config:/app/config:Z \
  --env-file .env \
  atlas-ui-3
```

The container seeds `/app/config` from package defaults at build time. Mounting your local `config/` folder overrides those defaults, so you can customize `llmconfig.yml`, `mcp.json`, etc. without rebuilding.

### Container Images

Pre-built container images are available at `quay.io/agarlan-snl/atlas-ui-3:latest` (pushes automatically from main branch).

## For AI Agent Contributors

If you are an AI agent working on this repository, please refer to the following documents for the most current and concise guidance:

*   **[CLAUDE.md](./CLAUDE.md)**: Detailed architecture, workflows, and conventions.
*   **[GEMINI.md](./GEMINI.md)**: Gemini-specific instructions.
*   **[.github/copilot-instructions.md](./.github/copilot-instructions.md)**: A compact guide for getting productive quickly.

## License

Copyright 2025 National Technology & Engineering Solutions of Sandia, LLC (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains certain rights in this software

MIT License

