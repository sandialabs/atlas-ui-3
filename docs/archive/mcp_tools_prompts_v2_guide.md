# MCP Integration Guide: Tools and Prompts (standalone)

This is the one doc you need to build MCP servers that work with this app. Follow the steps; no other spec is required.

## Quick setup (5 steps)

1) Register your server(s) in `config/*/mcp.json`:

```jsonc
{
  "prompts": {
    "command": ["python", "mcp/prompts/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Behavior prompts"
  },
  "reporter": {
    "url": "https://mcp.example.com",
    "transport": "http",           // or "sse"; omit for stdio via command
    "groups": ["users"],
    "description": "Report tools"
  }
}
```

Notes: `groups` gate visibility in the app; your server should still enforce ACLs.

2) Implement at least one tool with this response contract

- You can define any inputs. Common ones:
  - `username: string` (optional): if present in your schema, the app injects the authenticated user
  - `filename: string` or `file_names: string[]`: the app rewrites to downloadable URLs

Return a JSON object (the “envelope”) like this:

```json
{
  "results": { "summary": "..." },
  "meta_data": { "elapsed_ms": 120, "provider": "your-server" },
  "artifacts": [ { "name": "report.html", "b64": "<base64>", "mime": "text/html", "size": 12345 } ],
  "display": { "open_canvas": true, "primary_file": "report.html", "mode": "replace" }
}
```

Guidelines
- Keep `results` small; put files in `artifacts` with `mime` and base64 in `b64`.
- The UI opens a canvas for showable files (image/pdf/html/text). `display.primary_file` is a hint.
- Compatibility: legacy arrays `returned_file_names` + `returned_file_contents` are still accepted but discouraged.

3) Optionally implement prompts

- Define prompts on your server (e.g., FastMCP `@mcp.prompt`). The app auto‑discovers prompt names and fetches them when selected.
- You can also provide a helper tool like `list_available_prompts` to return prompt metadata for the UI.

4) Enforce access and trust injected identity

- If your tool accepts `username`, the app overwrites it with the authenticated user; trust this value.
- Use `groups` from your own policy to filter what users can do. Always re‑check on each call.

5) Test

- Run the app; select your server’s tool(s) and prompts; verify canvas renders artifacts and prompt discovery works.

## Minimal examples

Tool server (FastMCP)

```python
from typing import Any, Dict, Optional, List
from fastmcp import FastMCP

mcp = FastMCP("Reporter")

@mcp.tool
def build_report(
  username: str,
  filename: Optional[str] = None,
  file_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
  # The app rewrites filename(s) to downloadable URLs before calling this tool.
  html = "<html><body><h1>Report</h1><p>Generated for %s</p></body></html>" % username
  import base64
  b64 = base64.b64encode(html.encode("utf-8")).decode("utf-8")
  return {
    "results": { "summary": "Report generated" },
    "meta_data": { "elapsed_ms": 12 },
    "artifacts": [ { "name": "report.html", "b64": b64, "mime": "text/html" } ],
    "display": { "open_canvas": True, "primary_file": "report.html" }
  }

if __name__ == "__main__":
  mcp.run()
```

Prompts server (FastMCP)

```python
from fastmcp import FastMCP
from fastmcp.prompts.prompt import PromptMessage, TextContent

mcp = FastMCP("Prompts")

@mcp.prompt
def expert_dog_trainer() -> PromptMessage:
  text = "You are an expert dog trainer..."
  return PromptMessage(role="user", content=TextContent(type="text", text=text))

@mcp.prompt
def ask_about(topic: str) -> str:
  return f"Please explain: {topic}"

if __name__ == "__main__":
  mcp.run()
```

## Details (when you need them)

- Envelope fields
  - `results` (required): concise JSON; avoid long prose
  - `meta_data` (optional): small metrics/provenance
  - `artifacts` (optional): files to render/store `{ name, b64, mime, size?, description?, viewer? }`
  - `display` (optional): canvas hints `{ open_canvas?, primary_file?, mode?, viewer_hint? }`
- File inputs
  - The app rewrites `filename`/`file_names` to authorized URLs your tool can GET
- Username injection
  - If your tool defines `username`, the app overwrites it with the authenticated user’s identity
- Progress (optional)
  - FastMCP progress callbacks are supported for long‑running tools; keep updates brief
- Security
  - Don’t trust LLM‑provided identity; rely on injected `username`
  - Avoid secrets in any field; sanitize/validate inputs

## Quick checklist

- Add server config in `mcp.json` (command or url/transport) with `groups`
- Implement tools returning the envelope above; prefer `artifacts` for files
- Accept `filename`/`file_names`; download from provided URLs
- Include `username` if you need identity; enforce ACLs each call
- Define prompts with `@mcp.prompt` and keep names/args stable
