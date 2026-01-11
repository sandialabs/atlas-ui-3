# MCP Server Logging

Atlas UI 3 supports capturing and displaying log messages emitted by MCP servers during tool execution. This feature allows MCP servers to provide real-time feedback and debugging information to users through the chat interface.

## Overview

MCP servers can emit log messages at various levels (debug, info, warning, error, alert, etc.) using the FastMCP logging API. These messages are:
1. Captured by the MCPToolManager's log handler
2. Filtered based on the configured `LOG_LEVEL`
3. Forwarded to the backend logger for server-side logging
4. Sent to the frontend via WebSocket for display in the chat window

## Configuration

### Environment Variables

Control which log messages are captured and displayed using the `LOG_LEVEL` environment variable in `.env`:

```bash
# Only show WARNING and above (filters out DEBUG and INFO)
LOG_LEVEL=WARNING

# Show all messages including DEBUG
LOG_LEVEL=DEBUG

# Default - show INFO and above
LOG_LEVEL=INFO
```

Available log levels (in increasing severity):
- `DEBUG` - Detailed diagnostic information
- `INFO` - General informational messages
- `WARNING` - Warning messages for unexpected but handled situations
- `ERROR` - Error messages for failures
- `CRITICAL` - Critical issues (includes ALERT and EMERGENCY from MCP)

## Using Logging in MCP Servers

### Basic Usage

MCP servers built with FastMCP can access logging through the context object:

```python
from fastmcp import FastMCP

mcp = FastMCP("My Server")

@mcp.tool
def my_tool(param: str) -> dict:
    """Tool that demonstrates logging."""
    # Get the context
    ctx = mcp.get_context()
    
    # Emit log messages at various levels
    ctx.log.debug("Detailed debugging information")
    ctx.log.info("General information about progress")
    ctx.log.warning("Something unexpected happened")
    ctx.log.error("An error occurred")
    
    return {"result": "success"}

if __name__ == "__main__":
    mcp.run()
```

### Log Levels and When to Use Them

- **DEBUG**: Use for detailed diagnostic information that's useful during development
  ```python
  ctx.log.debug(f"Processing item {i} of {total}")
  ```

- **INFO**: Use for general informational messages about normal operation
  ```python
  ctx.log.info("Starting data processing")
  ctx.log.info("Operation completed successfully")
  ```

- **WARNING**: Use when something unexpected happens but the operation can continue
  ```python
  ctx.log.warning("API rate limit approaching, slowing down requests")
  ```

- **ERROR**: Use when an operation fails
  ```python
  ctx.log.error(f"Failed to connect to database: {error}")
  ```

### Example: Complete Server with Logging

See `backend/mcp/logging_demo/main.py` for a complete example server that demonstrates logging at all levels.

## Frontend Display

Log messages appear in the chat window with visual indicators:

- **DEBUG** - Gray badge, gray text (only visible when LOG_LEVEL=DEBUG)
- **INFO** - Blue badge, blue text
- **WARNING** - Yellow badge, yellow text
- **ERROR** - Red badge, red text
- **ALERT/CRITICAL** - Orange/red badge, orange/red text

Each log message includes:
- The log level as a colored badge
- The server name (e.g., `[my_server]`)
- The log message in monospace font

## Backend Logging

All MCP server logs (after filtering) are also written to the backend log file for debugging and auditing purposes:

```json
{
  "timestamp": "2025-12-11T03:30:15.123Z",
  "level": "INFO",
  "logger": "modules.mcp_tools.client",
  "message": "[MCP:my_server] Operation completed successfully",
  "mcp_server": "my_server",
  "mcp_extra": {}
}
```

## Testing

### Unit Tests

Test coverage for MCP logging is provided in `backend/tests/test_mcp_logging.py`:
- Log level mapping verification
- Log filtering by configured level
- Callback forwarding to UI
- Error handling

Run tests with:
```bash
pytest backend/tests/test_mcp_logging.py -v
```

### Manual Testing

1. Start the application with logging enabled:
   ```bash
   LOG_LEVEL=DEBUG python backend/main.py
   ```

2. Use the logging_demo MCP server:
   - Add it to your `mcp.json` configuration
   - In the chat, use the tool: `test_logging` with `operation="all"`
   - Observe log messages appearing in the chat window with appropriate styling

## Architecture

### Components

1. **MCPToolManager** (`backend/modules/mcp_tools/client.py`)
   - Creates log_handler for each MCP server
   - Filters logs based on LOG_LEVEL
   - Forwards logs to backend logger and UI callback

2. **ChatService** (`backend/application/chat/service.py`)
   - Sets up log callback during initialization
   - Routes log messages to WebSocket connection

3. **notification_utils** (`backend/application/chat/utilities/notification_utils.py`)
   - `notify_tool_log()` function for sending logs to UI

4. **WebSocket Handlers** (`frontend/src/handlers/chat/websocketHandlers.js`)
   - Handles `tool_log` intermediate updates
   - Adds log messages to chat with appropriate styling

5. **Message Component** (`frontend/src/components/Message.jsx`)
   - Renders tool_log messages with badges and colors

### Message Flow

```
MCP Server (ctx.log.info)
    ↓
FastMCP Client (log_handler)
    ↓
MCPToolManager (_create_log_handler)
    ↓ (if level >= min_level)
    ├→ Backend Logger (for server logs)
    └→ UI Callback (ChatService._create_mcp_log_callback)
        ↓
WebSocket (intermediate_update with type=tool_log)
        ↓
Frontend Handler (handleIntermediateUpdate)
        ↓
Chat UI (Message component renders with styling)
```

## Best Practices

1. **Use appropriate log levels**: Don't log everything at ERROR level
2. **Include context**: Add relevant details in log messages
3. **Respect the configured level**: Remember that DEBUG logs won't be shown in production
4. **Avoid sensitive data**: Don't log passwords, tokens, or PII
5. **Keep messages concise**: Users see these in the chat window
6. **Use structured logging**: Pass extra metadata in the `extra` parameter when needed

## Troubleshooting

### Logs not appearing in chat

1. Check that `LOG_LEVEL` is set appropriately (e.g., DEBUG for debug messages)
2. Verify the MCP server is using FastMCP's context logging API
3. Check backend logs to see if messages are being captured
4. Ensure the WebSocket connection is active

### Too many log messages

Increase the `LOG_LEVEL` to filter out lower-priority messages:
```bash
LOG_LEVEL=WARNING  # Only show warnings and errors
```

### Logs appearing in backend but not frontend

Check that:
1. The ChatService log callback is properly initialized
2. The WebSocket connection is functioning
3. The frontend handler is processing `tool_log` updates correctly
