# Poisoned Tool MCP Server

**FOR SECURITY TESTING ONLY**

This MCP server intentionally returns dangerous content to test security checks.

## Purpose

This server is designed to verify that:
- Tool output security checks are working
- Dangerous content is properly detected
- Content is blocked before reaching the user
- Security warnings are displayed in the UI

## Tool

### `get_printer_status`

Appears to be a normal printer status tool but returns poisoned content containing dangerous keywords that should trigger security checks.

**Args:**
- `printer_name` (str): Printer identifier (ignored)

**Returns:**
- Dict with status information containing dangerous content

## Usage

To enable this MCP server for testing:

1. Add to `config/overrides/mcp.json`:
```json
{
  "name": "poisoned-tool",
  "description": "Printer status tool (POISONED - FOR TESTING)",
  "transport": "stdio",
  "command": "uv",
  "args": ["--directory", "backend/mcp/poisoned-tool", "run", "poisoned-tool"],
  "groups": ["admin"],
  "compliance_level": "Internal"
}
```

2. Restart the application

3. Use the tool in a chat:
   - "Check the status of printer HP-LaserJet-5000"
   - Should trigger security check and block the response

## Expected Behavior

When the tool is called:
1. Tool executes and returns dangerous content
2. Security check scans the output
3. Content is flagged as dangerous (contains "bomb")
4. Security warning is sent to frontend
5. User sees blocked message, not the dangerous content

## Notes

- This tool always returns the same dangerous content regardless of input
- The function name and docstring appear benign to test real-world scenarios
- Only the internal comment indicates this is poisoned
