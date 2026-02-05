# Using Atlas as a Python Package

Last updated: 2026-02-04

Atlas can be installed as a Python package, allowing you to use it programmatically in your scripts or integrate it into your applications.

## Installation

```bash
# Install from PyPI
pip install atlas-chat

# Or with uv (faster)
uv pip install atlas-chat
```

**Important:** After installation, you must [configure your API keys](#configuration-required) before using Atlas.

## Quick Start

**Prerequisites:** Set at least one LLM API key (see [Configuration](#configuration-required) below):
```bash
export OPENAI_API_KEY="sk-your-key-here"
```

### Basic Usage

```python
import asyncio
from atlas import AtlasClient, ChatResult

async def main():
    client = AtlasClient()

    # Simple chat
    result = await client.chat("What is the capital of France?")
    print(result.message)

    # Cleanup when done
    await client.cleanup()

asyncio.run(main())
```

### Synchronous Usage

For simpler scripts that don't need async:

```python
from atlas import AtlasClient

client = AtlasClient()
result = client.chat_sync("Explain quantum computing in simple terms")
print(result.message)
```

## API Reference

### AtlasClient

The main client class for interacting with Atlas.

#### Methods

##### `async chat(prompt, **kwargs) -> ChatResult`

Send a chat message and get a response.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | str | required | The user message to send |
| `model` | str | None | LLM model name (uses config default if not specified) |
| `agent_mode` | bool | False | Enable agent loop for multi-step tool use |
| `selected_tools` | List[str] | None | List of tool names to enable |
| `selected_data_sources` | List[str] | None | List of RAG data sources to query |
| `only_rag` | bool | False | Use only RAG without tools |
| `user_email` | str | None | User identity for auth-filtered tools/RAG |
| `session_id` | UUID | None | Reuse an existing session for multi-turn conversations |
| `max_steps` | int | 10 | Maximum agent iterations |
| `temperature` | float | 0.7 | LLM temperature |
| `streaming` | bool | False | Stream tokens to stdout as they arrive |
| `quiet` | bool | False | Suppress status output on stderr |

**Returns:** `ChatResult`

##### `chat_sync(prompt, **kwargs) -> ChatResult`

Synchronous wrapper around `chat()`. Takes the same parameters.

##### `async list_data_sources(user_email=None) -> Dict`

Discover available RAG data sources.

**Returns:** Dict with `servers` (config info) and `sources` (discovered qualified IDs)

##### `async cleanup()`

Clean up MCP connections. Call this when done using the client.

### ChatResult

A dataclass containing the chat response.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `message` | str | The assistant's response text |
| `tool_calls` | List[Dict] | List of tool calls made during the conversation |
| `files` | Dict | Files generated or referenced |
| `canvas_content` | str | HTML/markdown content for canvas display |
| `session_id` | UUID | Session ID for multi-turn conversations |

**Methods:**

- `to_dict() -> Dict` - Convert result to a dictionary

## Examples

### Using a Specific Model

```python
from atlas import AtlasClient

client = AtlasClient()
result = client.chat_sync(
    "Write a haiku about programming",
    model="claude-3-5-sonnet"
)
print(result.message)
```

### Using Tools

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    # Enable specific tools
    result = await client.chat(
        "Search for the latest news about AI",
        selected_tools=["web_search", "summarize"],
        agent_mode=True  # Enable agent loop for tool use
    )

    print("Response:", result.message)
    print("Tool calls made:", len(result.tool_calls))

    await client.cleanup()

asyncio.run(main())
```

### Using RAG Data Sources

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    # List available data sources
    sources = await client.list_data_sources()
    print("Available sources:", sources["sources"])

    # Query specific data sources
    result = await client.chat(
        "What does the documentation say about authentication?",
        selected_data_sources=["docs:handbook", "docs:api-reference"],
        only_rag=True  # Use only RAG, no tools
    )

    print(result.message)
    await client.cleanup()

asyncio.run(main())
```

### Multi-turn Conversations

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    # First message - save the session_id
    result1 = await client.chat("My name is Alice")
    session_id = result1.session_id

    # Follow-up message in the same session
    result2 = await client.chat(
        "What's my name?",
        session_id=session_id
    )
    print(result2.message)  # Should remember "Alice"

    await client.cleanup()

asyncio.run(main())
```

### Streaming Output

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    # Stream tokens to stdout as they arrive
    result = await client.chat(
        "Tell me a short story",
        streaming=True
    )
    # Tokens are printed as they arrive
    # result.message contains the full response

    await client.cleanup()

asyncio.run(main())
```

### Agent Mode with Multiple Steps

```python
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    result = await client.chat(
        "Research the top 3 AI companies and summarize their recent announcements",
        agent_mode=True,
        max_steps=15,  # Allow more iterations for complex tasks
        selected_tools=["web_search", "summarize"]
    )

    print("Final response:", result.message)
    print(f"Made {len(result.tool_calls)} tool calls")

    await client.cleanup()

asyncio.run(main())
```

## Configuration (Required)

**Whether you install from PyPI or use editable mode, you must configure API keys and settings before using Atlas.**

### Step 1: Set Up API Keys

Atlas needs API keys to communicate with LLM providers. Set them as environment variables:

```bash
# At minimum, set ONE of these (depending on which provider you use):
export OPENAI_API_KEY="sk-..."           # For GPT-4, GPT-3.5, etc.
export ANTHROPIC_API_KEY="sk-ant-..."    # For Claude models
export GOOGLE_API_KEY="..."              # For Gemini models

# You can set multiple keys to use multiple providers
```

**Option A: Export in your shell**
```bash
# Add to ~/.bashrc or ~/.zshrc for persistence
export OPENAI_API_KEY="sk-your-key-here"
```

**Option B: Use a .env file**
```bash
# Create a .env file in your working directory
echo 'OPENAI_API_KEY=sk-your-key-here' > .env
echo 'ANTHROPIC_API_KEY=sk-ant-your-key' >> .env
```

Atlas automatically loads `.env` files from the current directory.

### Step 2: Configure Models (Optional)

By default, Atlas uses built-in model configurations. To customize available models, create a config directory:

```bash
# Create config directory
mkdir -p config/defaults

# Create llmconfig.yml with your models
cat > config/defaults/llmconfig.yml << 'EOF'
models:
  gpt-4o:
    model_name: gpt-4o
    api_key: ${OPENAI_API_KEY}
  claude-3-sonnet:
    model_name: claude-3-5-sonnet-20241022
    api_key: ${ANTHROPIC_API_KEY}
EOF

# Tell Atlas where to find config
export APP_CONFIG_OVERRIDES="./config"
```

### Step 3: Configure MCP Tools (Optional)

To use MCP tools, create an `mcp.json` configuration:

```bash
cat > config/defaults/mcp.json << 'EOF'
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-filesystem", "/path/to/allowed/dir"],
      "description": "File system access",
      "enabled": true
    }
  }
}
EOF
```

### Quick Start Configuration

For the simplest setup, just set your API key and go:

```bash
# Minimal setup - just need an API key
export OPENAI_API_KEY="sk-your-key-here"

# Now you can use Atlas
atlas-chat "Hello, world!"

# Or in Python
python -c "
from atlas import AtlasClient
client = AtlasClient()
result = client.chat_sync('Hello!')
print(result.message)
"
```

### Configuration Reference

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `OPENAI_API_KEY` | Yes* | OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes* | Anthropic API key |
| `GOOGLE_API_KEY` | Yes* | Google AI API key |
| `APP_CONFIG_OVERRIDES` | No | Path to custom config directory |
| `APP_LOG_DIR` | No | Directory for log files |
| `DEBUG_MODE` | No | Enable debug logging (true/false) |

*At least one API key is required.

See the [Configuration Guide](../admin/configuration.md) for full details on all configuration options.

## CLI Tools

When you install the atlas-chat package, two CLI commands become available:

### atlas-chat

Chat with an LLM from the command line:

```bash
# Simple query
atlas-chat "What is 2+2?"

# Use a specific model
atlas-chat "Explain relativity" --model gpt-4o

# Use tools
atlas-chat "Search for Python tutorials" --tools web_search

# List available tools
atlas-chat --list-tools

# List available models
atlas-chat --list-models
```

### atlas-server

Start the Atlas server:

```bash
# Start on default port 8000
atlas-server

# Custom port and host
atlas-server --port 8080 --host 0.0.0.0

# Use custom environment file and config folder
atlas-server --env /path/to/.env --config-folder /path/to/config
```

## Development Installation (Editable Mode)

For development, install the package in **editable mode** (`pip install -e .`). This creates a symlink from your Python environment to your local source code directory, allowing you to modify the code and see changes immediately without reinstalling.

### Setup

```bash
# Clone the repository
git clone https://github.com/sandialabs/atlas-ui-3.git
cd atlas-ui-3

# Create virtual environment
uv venv && source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Install atlas package in editable mode
uv pip install -e .

# Or with pip
pip install -e .
```

### What Editable Mode Does

When you run `pip install -e .`, Python creates a special link that points to your source directory instead of copying files to `site-packages`. This means:

| Normal Install (`pip install atlas-chat`) | Editable Install (`pip install -e .`) |
|-------------------------------------------|---------------------------------------|
| Copies files to `site-packages/` | Links to your source directory |
| Must reinstall after every code change | Changes take effect immediately |
| Uses the version from PyPI | Uses your local code |

### Development Workflow

```bash
# 1. Install once in editable mode
uv pip install -e .

# 2. Make changes to the code
vim atlas/atlas_client.py

# 3. Test immediately - no reinstall needed!
python -c "from atlas import AtlasClient; print('works!')"

# 4. CLI commands also use your local code
atlas-chat "test my changes"

# 5. Run the server with your changes
atlas-server --port 8000
```

### Example: Modifying the Client

```bash
# Edit the AtlasClient class
vim atlas/atlas_client.py

# Your script immediately uses the updated code
python my_test_script.py

# The CLI also uses your changes
atlas-chat --list-tools
```

### Verifying Editable Mode is Working

```python
# Check where Python is loading atlas from
import atlas
print(atlas.__file__)
# Should show: /path/to/atlas-ui-3/atlas/__init__.py
# NOT: /path/to/.venv/lib/python3.x/site-packages/atlas/__init__.py
```

### Running the Full Application

With editable mode installed, you can run the full web application:

```bash
# Option 1: Use the start script (builds frontend + starts backend)
bash agent_start.sh

# Option 2: Use the CLI server command
atlas-server --port 8000

# Option 3: Run directly with uvicorn
cd atlas && PYTHONPATH=.. uvicorn main:app --port 8000
```

### Combining with Frontend Development

```bash
# Terminal 1: Build frontend (one-time or when frontend changes)
cd frontend && npm install && npm run build

# Terminal 2: Run backend with your Python changes
atlas-server --port 8000

# Now edit Python files and refresh browser to see changes
```

## Next Steps

- [CLI Usage Guide](../developer/cli-usage-2026-01-27.md) - Detailed CLI documentation
- [Configuration Guide](../admin/configuration.md) - Configure models, tools, and features
- [MCP Tools](../admin/mcp-servers.md) - Set up and configure MCP tool servers
