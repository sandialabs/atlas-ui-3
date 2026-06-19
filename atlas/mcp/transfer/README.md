# MCP Transfer Example

This local-development MCP server demonstrates moving files between disk and an Atlas chat session.
It is not intended for production use.

## Tools

- `read_file_from_disk(path)`: reads a file below the configured base directory and returns it as an MCP artifact. UTF-8 files also include decoded text in the result.
- `write_file_to_disk(path, content, content_is_base64=false)`: writes text or base64-encoded bytes from the chat session to a file below the configured base directory.

## Base directory

Set `MCP_TRANSFER_BASE_DIR` to choose the directory the server can access. If unset, paths are limited to the MCP server working directory. Path traversal outside that directory is denied.

## Configuration

Use the example config at `atlas/config/mcp-example-configs/mcp-transfer.json` as a starting point and merge it into your local `config/mcp.json`.
