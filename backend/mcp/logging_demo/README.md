# Logging Demo MCP Server

A demonstration MCP server that showcases the logging capabilities of Atlas UI 3's MCP integration.

## Purpose

This server provides a simple tool that emits log messages at various levels to test and demonstrate the MCP logging infrastructure.

## Usage

### Configuration

Add to your `mcp.json`:

```json
{
  "logging_demo": {
    "command": ["python", "main.py"],
    "cwd": "backend/mcp/logging_demo",
    "transport": "stdio"
  }
}
```

### Tool: test_logging

Test MCP server logging at various levels.

**Parameters:**
- `operation` (string): Which logging levels to test
  - `"all"` - Test all log levels (debug, info, warning, error)
  - `"debug"` - Debug level only
  - `"info"` - Info level only
  - `"warning"` - Warning level only
  - `"error"` - Error level only
  - `"mixed"` - A realistic mix of levels simulating an operation

**Examples:**

```
Use the tool: logging_demo_test_logging with operation="all"
```

```
Use the tool: logging_demo_test_logging with operation="mixed"
```

## Expected Output

When you call this tool with `operation="all"`, you should see:
1. The tool result showing success
2. Log messages appearing in the chat window with colored badges:
   - DEBUG (gray) - "This is a DEBUG message..."
   - INFO (blue) - "This is an INFO message..."
   - WARNING (yellow) - "This is a WARNING message..."
   - ERROR (red) - "This is an ERROR message..."

**Note:** DEBUG logs will only appear if `LOG_LEVEL=DEBUG` is set in your environment.

## Testing Log Levels

To test filtering:

1. Set `LOG_LEVEL=WARNING` in `.env`
2. Restart the backend
3. Call the tool with `operation="all"`
4. You should only see WARNING and ERROR messages (INFO and DEBUG filtered out)

## See Also

- [MCP Server Logging Documentation](../../docs/developer/mcp-server-logging.md)
- [FastMCP Logging Documentation](https://gofastmcp.com/clients/logging)
