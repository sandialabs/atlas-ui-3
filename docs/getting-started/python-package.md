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

## Quick Start

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

## Configuration

The client uses the same configuration as the Atlas server. Set environment variables or use config files:

```bash
# Required: Set your LLM API keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: Custom config directory
export APP_CONFIG_OVERRIDES="/path/to/config/overrides"
```

See the [Configuration Guide](../admin/configuration.md) for full details on configuration options.

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

## Development Installation

For development, install in editable mode:

```bash
# Clone the repository
git clone https://github.com/sandialabs/atlas-ui-3.git
cd atlas-ui-3

# Create virtual environment
uv venv && source .venv/bin/activate

# Install in editable mode
uv pip install -e .

# Or with pip
pip install -e .
```

This allows you to make changes to the source code and have them immediately reflected without reinstalling.

## Next Steps

- [CLI Usage Guide](../developer/cli-usage-2026-01-27.md) - Detailed CLI documentation
- [Configuration Guide](../admin/configuration.md) - Configure models, tools, and features
- [MCP Tools](../admin/mcp-servers.md) - Set up and configure MCP tool servers
