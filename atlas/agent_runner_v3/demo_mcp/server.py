"""Minimal Streamable-HTTP MCP server for Agent Portal V3 end-to-end testing.

Stdlib only (no pip deps) so it runs unmodified on the agent-runner image.
Speaks just enough JSON-RPC 2.0 over HTTP POST for the V3 agent runner's
MCPHttpClient: initialize, notifications/initialized, tools/list, tools/call.

Exposes two tools:
  - get_project_secret_code: returns a fixed, unguessable token. If an agent's
    final answer contains this token, the tool *definitely* executed inside the
    pod (the model cannot know it otherwise) -- this is our E2E proof.
  - multiply: returns a * b, a deterministic second tool for demos.

Listens on $PORT (default 8080), path-agnostic (any POST path is handled).
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET_CODE = "SKY-PENGUIN-42"

TOOLS = [
    {
        "name": "get_project_secret_code",
        "description": (
            "Return the secret project code for the current Atlas deployment. "
            "There is no other way to learn this code; you must call this tool."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "multiply",
        "description": "Multiply two numbers and return the product.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "first factor"},
                "b": {"type": "number", "description": "second factor"},
            },
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(name: str, args: dict) -> dict:
    if name == "get_project_secret_code":
        return _text_result(f"The secret project code is {SECRET_CODE}.")
    if name == "multiply":
        try:
            product = float(args["a"]) * float(args["b"])
        except (KeyError, TypeError, ValueError) as exc:
            return _text_result(f"multiply requires numeric 'a' and 'b': {exc}", is_error=True)
        # Render whole numbers without a trailing .0
        if product.is_integer():
            product = int(product)
        return _text_result(f"{product}")
    return _text_result(f"Unknown tool: {name}", is_error=True)


def _dispatch(req: dict) -> dict | None:
    """Return a JSON-RPC response dict, or None for notifications."""
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "atlas-demo-mcp", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = req.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method and method.startswith("notifications/"):
        return None  # notifications get no response body
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # health/readiness probe
        self._send(200, b"ok", ctype="text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error":"invalid json"}')
            return

        resp = _dispatch(req)
        if resp is None:
            self._send(202)  # accepted notification, no content
            return
        self._send(200, json.dumps(resp).encode("utf-8"))

    def log_message(self, fmt, *args):  # quieter logs, but keep one line per call
        print("mcp-demo %s - %s" % (self.address_string(), fmt % args), flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"atlas-demo-mcp listening on :{port} (secret={SECRET_CODE})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
