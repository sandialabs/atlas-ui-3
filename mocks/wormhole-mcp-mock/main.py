#!/usr/bin/env python3
"""Wormhole-enabled MCP mock server.

Simulates the *external* service in the Genesis Mission Wormhole flow (issue
#640): a streamable-HTTP MCP server that lives outside core Atlas and expects the
per-session Wormhole subtoken to arrive as an ``X-Token`` HTTP header.

Atlas captures the ``x-subtoken`` request header (unpacked from the Wormhole JWT
by the proxy) and forwards it to Wormhole-enabled MCP servers as ``X-Token``.
This mock lets end-to-end tests prove that forwarding works:

- It reads the configured forward header (default ``X-Token``) on every MCP
  request and records what it saw.
- ``whoami`` / ``get_protected_resource`` report whether the subtoken arrived,
  so a caller can distinguish "subtoken forwarded" from "subtoken missing".
- A ``/status`` HTML dashboard renders the live request log (tokens masked) plus
  an optional E2E report posted to ``/report`` — used to capture screenshots.

This is a TEST DOUBLE. It performs no real authorization; it only observes and
echoes the forwarded header so the Atlas side of the flow can be validated.
"""

from __future__ import annotations

import argparse
import html
import os
from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

# Header that Atlas forwards the subtoken on (configurable via WORMHOLE_FORWARD_HEADER).
FORWARD_HEADER = os.getenv("WORMHOLE_FORWARD_HEADER", "X-Token").lower()

mcp = FastMCP(
    name="Wormhole MCP Mock",
    instructions=(
        "Simulated Wormhole-enabled MCP server. Tools report whether the "
        "per-session Wormhole subtoken was forwarded as the X-Token header."
    ),
)

# In-memory observation log. Single-process HTTP server, so a module global is
# fine. Each entry records one MCP request that reached a tool.
_REQUEST_LOG: list[dict] = []
# Optional report posted by an E2E driver, rendered on the dashboard.
_E2E_REPORT: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _mask(token: str | None) -> str:
    """Mask a subtoken for display (first/last 4 chars), like Atlas does."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def _read_subtoken() -> str | None:
    """Return the forwarded subtoken from the current request headers, if any."""
    headers = get_http_headers()
    # Header lookups are case-insensitive; get_http_headers lowercases keys.
    for key, value in headers.items():
        if key.lower() == FORWARD_HEADER:
            return value or None
    return None


def _record(tool: str) -> str | None:
    token = _read_subtoken()
    _REQUEST_LOG.append(
        {
            "time": _now(),
            "tool": tool,
            "subtoken_present": bool(token),
            "subtoken_masked": _mask(token),
        }
    )
    # Keep the log bounded for the dashboard.
    if len(_REQUEST_LOG) > 100:
        del _REQUEST_LOG[: len(_REQUEST_LOG) - 100]
    return token


@mcp.tool
def whoami() -> dict:
    """Report whether the Wormhole subtoken was forwarded on this request."""
    token = _record("whoami")
    if token:
        return {
            "authenticated": True,
            "forward_header": FORWARD_HEADER,
            "subtoken_masked": _mask(token),
            "message": f"Received Wormhole subtoken via '{FORWARD_HEADER}' header.",
        }
    return {
        "authenticated": False,
        "forward_header": FORWARD_HEADER,
        "message": f"No '{FORWARD_HEADER}' header present on this request.",
    }


@mcp.tool
def get_protected_resource() -> dict:
    """Return a protected payload only when the subtoken was forwarded.

    Mirrors a real Wormhole MCP server that rejects unauthenticated calls.
    """
    token = _record("get_protected_resource")
    if not token:
        return {
            "ok": False,
            "error": "MCP session rejected (check authentication/token)",
            "detail": f"Missing '{FORWARD_HEADER}' header.",
        }
    return {
        "ok": True,
        "subtoken_masked": _mask(token),
        "data": {
            "mission": "Genesis",
            "secret": "wormhole-authorized-payload",
        },
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "forward_header": FORWARD_HEADER})


@mcp.custom_route("/log", methods=["GET"])
async def log(_request: Request) -> JSONResponse:
    """Machine-readable view of observed requests (for E2E assertions)."""
    return JSONResponse({"forward_header": FORWARD_HEADER, "requests": _REQUEST_LOG})


@mcp.custom_route("/report", methods=["POST"])
async def report(request: Request) -> JSONResponse:
    """Accept an E2E summary to render on the dashboard."""
    global _E2E_REPORT
    _E2E_REPORT = await request.json()
    return JSONResponse({"stored": True})


@mcp.custom_route("/reset", methods=["POST"])
async def reset(_request: Request) -> JSONResponse:
    global _E2E_REPORT
    _REQUEST_LOG.clear()
    _E2E_REPORT = None
    return JSONResponse({"reset": True})


@mcp.custom_route("/status", methods=["GET"])
async def status(_request: Request) -> HTMLResponse:
    """Human-readable dashboard of forwarded subtokens (for screenshots)."""
    rows = "".join(
        f"<tr class='{'ok' if e['subtoken_present'] else 'miss'}'>"
        f"<td>{html.escape(e['time'])}</td>"
        f"<td><code>{html.escape(e['tool'])}</code></td>"
        f"<td>{'YES' if e['subtoken_present'] else 'NO'}</td>"
        f"<td><code>{html.escape(e['subtoken_masked'])}</code></td>"
        f"</tr>"
        for e in reversed(_REQUEST_LOG)
    ) or "<tr><td colspan='4'><em>No MCP requests observed yet.</em></td></tr>"

    report_html = ""
    if _E2E_REPORT:
        scenarios = "".join(
            f"<li class='{'ok' if s.get('passed') else 'miss'}'>"
            f"<b>{'PASS' if s.get('passed') else 'FAIL'}</b> — "
            f"{html.escape(str(s.get('name', '')))}: "
            f"{html.escape(str(s.get('detail', '')))}</li>"
            for s in _E2E_REPORT.get("scenarios", [])
        )
        overall = "PASS" if _E2E_REPORT.get("passed") else "FAIL"
        report_html = (
            f"<h2>E2E report: <span class='{'ok' if _E2E_REPORT.get('passed') else 'miss'}'>"
            f"{overall}</span></h2>"
            f"<p>{html.escape(str(_E2E_REPORT.get('summary', '')))}</p>"
            f"<ul>{scenarios}</ul>"
        )

    body = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Wormhole MCP Mock</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
 h1 {{ margin-bottom: 0; }}
 .sub {{ color: #616e7c; margin-top: .25rem; }}
 table {{ border-collapse: collapse; margin-top: 1rem; width: 100%; max-width: 880px; }}
 th, td {{ border: 1px solid #cbd2d9; padding: .5rem .75rem; text-align: left; }}
 th {{ background: #f5f7fa; }}
 tr.ok td {{ background: #e6f6ec; }}
 tr.miss td {{ background: #fdecea; }}
 .ok {{ color: #0a7d33; }}
 .miss {{ color: #b91c1c; }}
 code {{ background: #f0f4f8; padding: .1rem .3rem; border-radius: 3px; }}
 .badge {{ display:inline-block; background:#1f6feb; color:#fff; padding:.15rem .5rem; border-radius: 999px; font-size:.8rem; }}
</style></head>
<body>
 <h1>Wormhole MCP Mock <span class='badge'>issue #640</span></h1>
 <p class='sub'>External service simulating a Wormhole-enabled MCP server.
    Forward header: <code>{html.escape(FORWARD_HEADER)}</code></p>
 {report_html}
 <h2>Observed MCP requests (subtokens masked)</h2>
 <table>
  <tr><th>Time</th><th>Tool</th><th>Subtoken forwarded?</th><th>X-Token (masked)</th></tr>
  {rows}
 </table>
</body></html>"""
    return HTMLResponse(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wormhole-enabled MCP mock server")
    parser.add_argument("--host", default=os.getenv("WORMHOLE_MOCK_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("WORMHOLE_MOCK_PORT", "8011"))
    )
    args = parser.parse_args()

    print(
        f"Starting Wormhole MCP mock on http://{args.host}:{args.port} "
        f"(MCP at /mcp, dashboard at /status, forward header '{FORWARD_HEADER}')"
    )
    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
