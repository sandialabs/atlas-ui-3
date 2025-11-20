# Backend Development Guide

The backend is a FastAPI application that serves as an MCP (Model Context Protocol) client, providing WebSocket-based chat functionality with LLM integration.

## Architecture Overview

### Core Components

**MessageProcessor** (`message_processor.py`): The **most important class** in the entire codebase. The `handle_chat_message()` method:
- Orchestrates the entire chat message processing pipeline
- Handles RAG-only vs integrated processing modes  
- Manages tool validation and LLM calls
- Coordinates callbacks throughout the message lifecycle
- Processes WebSocket messages and responses

### Key Modules

- **`main.py`** - FastAPI application entry point and routing
- **`session.py`** - WebSocket session management
- **`message_processor.py`** - Core message processing logic
- **`config.py`** - Unified Pydantic configuration system
- **`auth.py`** / **`auth_utils.py`** - Authentication and authorization
- **`mcp_client.py`** - MCP client implementation
- **`utils.py`** - Utility functions and tool validation
- **`rag_client.py`** - RAG integration client
- **`http_client.py`** - Unified HTTP client with error handling
- **`llm_health_check.py`** - LLM health monitoring system

## Configuration System (v2.0)

The backend uses a modern Pydantic-based configuration system:

### Benefits
- **Type-safe** configuration with automatic validation
- **Centralized** management of all settings
- **Environment integration** with .env file loading
- **Eliminated duplication** - removed ~150 lines of duplicate code

### Usage
```python
from config import config_manager

# Get application settings
app_settings = config_manager.app_settings
print(f"Running {app_settings.app_name} on port {app_settings.port}")

# Get LLM configuration
llm_config = config_manager.llm_config
models = list(llm_config.models.keys())

# Get MCP configuration
mcp_config = config_manager.mcp_config
servers = list(mcp_config.servers.keys())
```

## Authentication & Authorization

### Authentication Flow
- **Production**: Expects `x-email-header` from reverse proxy
- **Development**: Set `DEBUG_MODE=true` to use test user

### Authorization System
```python
from auth_utils import create_authorization_manager
from auth import is_user_in_group

auth_manager = create_authorization_manager(is_user_in_group)

# Validate tool access
requested_servers, warnings = auth_manager.validate_tool_access(
    user_email, selected_tools, get_authorized_servers
)
```

## HTTP Client System

### Unified HTTP Client
```python
from http_client import create_llm_client

# Create client with proper error handling
http_client = create_llm_client()

# All HTTP errors include full tracebacks
# Standardized error responses across all services
```

## WebSocket Protocol

### Client Messages
```json
{
  "type": "chat",
  "content": "Hello, world!",
  "model": "gpt-4",
  "user": "user@example.com"
}

{
  "type": "mcp_request",
  "server": "filesystem",
  "request": {
    "method": "tools/call",
    "params": {
      "name": "read_file",
      "arguments": {"path": "test.txt"}
    }
  }
}
```

### Server Messages
```json
{
  "type": "chat_response",
  "message": "Hello! How can I help you?",
  "user": "user@example.com"
}

{
  "type": "mcp_response",
  "server": "filesystem",
  "response": {"content": "file contents..."}
}
```

## MCP Server Development

### Built-in MCP Servers

**Filesystem Server** (`mcp/filesystem/`):
- Tools: read_file, write_file, list_directory, create_directory, delete_file, file_exists
- Security: Path validation to prevent directory traversal
- Groups: users, mcp_basic

**Calculator Server** (`mcp/calculator/`):
- Tools: add, subtract, multiply, divide, power, sqrt, factorial, evaluate
- Security: Safe expression evaluation with restricted builtins
- Groups: users

### Adding New MCP Servers

1. **Create server directory**:
   ```bash
   mkdir backend/mcp/myserver
   ```

2. **Implement main.py**:
   ```python
   from fastmcp import FastMCP
   
   mcp = FastMCP(name="MyServer")
   
   @mcp.tool
   def my_tool(param: str) -> str:
       """Tool description."""
       return f"Result: {param}"
   
   if __name__ == "__main__":
       mcp.run()
   ```

3. **Add configuration** to `mcp.json`:
   ```json
   {
     "myserver": {
       "groups": ["users"],
       "is_exclusive": false,
       "description": "My custom server",
       "enabled": true
     }
   }
   ```

## API Endpoints

- **`GET /`** - Main chat interface
- **`GET /api/config`** - Get available models and tools for user
- **`WebSocket /ws`** - Real-time chat communication
- **`GET /auth`** - Authentication endpoint for redirects

## Code Quality Standards

### File Organization
- **Maximum 400 lines per file**
- **Highly modular design**
- **Single responsibility principle**

### Error Handling
- **Always use `exc_info=True`** for error logging
- **Full tracebacks** in all error logs
- **Consistent error logging patterns**

### Example:
```python
import logging

logger = logging.getLogger(__name__)

try:
    # Some operation
    pass
except Exception as e:
    logger.error("Operation failed", exc_info=True)
    raise
```

## Testing Backend Code

Run basic functionality tests:
```bash
cd backend

# Test configuration loading
python -c "from config import config_manager; print('✅ Config OK')"

# Test MCP client
python -c "from mcp_client import MCPToolManager; print('✅ MCP OK')"

# Test HTTP client
python -c "from http_client import create_llm_client; print('✅ HTTP OK')"
```

## Common Backend Tasks

### Adding New LLM Models
Edit `llmconfig.yml`:
```yaml
models:
  my-model:
    model_url: "https://api.example.com/v1/chat/completions"
    model_name: "my-model-name"
    api_key: "${MY_API_KEY}"
    max_tokens: 2000
    temperature: 0.8
```

### Modifying System Prompts
Edit `prompts/system_prompt.md` (moved out of backend code to root). Changes take effect immediately for new conversations.

### Adding New Authorization Groups
Modify `backend/auth.py` and update group assignments in the `mock_groups` configuration.

## Logging

All logs are written to `backend/logs/app.log` with enhanced error logging:
- **Full tracebacks** for all exceptions
- **Security auditing** for authorization failures
- **Consistent format** across all modules

## Troubleshooting

### Common Issues
1. **Import errors**: Ensure you're in the correct virtual environment with uv
2. **Configuration errors**: Check `.env` file and run config test
3. **MCP server issues**: Verify server configuration in `mcp.json`
4. **Authentication failures**: Check `DEBUG_MODE` setting or reverse proxy headers

### Debug Commands
```bash
# Check Python environment
uv pip list

# Test configuration
python -c "from config import config_manager; print(config_manager.app_settings)"

# Check logs
tail -f backend/logs/app.log
```