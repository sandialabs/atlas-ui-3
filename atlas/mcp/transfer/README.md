# MCP Transfer Example

This local-development MCP server demonstrates moving files between disk and an Atlas chat session.
It is not intended for production use.

## Tools

- `read_file_from_disk(path)`: reads a file below the configured base directory and returns it as an MCP artifact (full bytes). The tool-result text is a head+tail preview — the first and last `MCP_TRANSFER_PREVIEW_LINES` lines (default 50 each) — so the model context is not flooded with the entire file. A byte ceiling (`MCP_TRANSFER_PREVIEW_BYTES`, default 16 KiB) caps the preview so a file with few but very long lines cannot bypass the line budget. Files under both budgets are returned in full. When the preview is trimmed the text is emitted under `content_preview` (the `content` key is omitted) so a blind forward into `write_file_to_disk(content=...)` fails loudly. Binary files produce no text in the tool result; the bytes travel only as the artifact.
- `write_file_to_disk(path, content, content_is_base64=false)`: writes text or base64-encoded bytes from the chat session to a file below the configured base directory.

## Base directory

Set `MCP_TRANSFER_BASE_DIR` to choose the directory the server can access. If unset, paths are limited to the MCP server working directory. Path traversal outside that directory is denied.

> **Security note:** When `MCP_TRANSFER_BASE_DIR` is unset, the base directory defaults to the server's working directory (`atlas/` with the example config), which exposes the source tree to chat-driven reads and writes. Because this example grants disk read/write to the `users` group, always point `MCP_TRANSFER_BASE_DIR` at a dedicated, disposable folder before enabling it, and do not deploy it outside local development.

## Read size limit

`read_file_from_disk` rejects files larger than `MCP_TRANSFER_MAX_BYTES` (default 10 MiB) so a single read cannot pull unbounded content into chat context or server memory. Set `MCP_TRANSFER_MAX_BYTES` to raise or lower the cap.

## Read preview

To avoid injecting an entire file into the model context, the text in the tool result is trimmed to the first and last `MCP_TRANSFER_PREVIEW_LINES` lines (default 50 each) when the file exceeds twice that line count. A byte ceiling (`MCP_TRANSFER_PREVIEW_BYTES`, default 16 KiB) also applies, so a file with few but very long lines (minified bundles, single-line JSON) cannot bypass the line budget. The full file is still returned as a base64 artifact, so the agent can forward it to another tool (e.g. `write_file_to_disk`) without needing to re-read it. When the preview is trimmed the text is emitted under `content_preview` and the `content` key is omitted, so a caller forwarding the result into `write_file_to_disk(content=...)` fails loudly instead of silently writing the omission marker. Non-positive values of either env var are ignored and the default is used.

## Configuration

Use the example config at `atlas/config/mcp-example-configs/mcp-transfer.json` as a starting point and merge it into your local `config/mcp.json`. The `command` uses a relative path (`mcp/transfer/main.py`) resolved from `cwd: atlas`; adjust both if you run the server from a different working directory or an installed package layout.
