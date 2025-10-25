# MCP Progress Reporting Guide

This note shows how to implement progress reporting in your MCP servers and how our app consumes progress to update the UI. Use it for any long‑running tools (downloads, data processing, exports, etc.).

See also: `docs/mcp_progress_note.md` (same content).

## Why progress reporting?
- Better UX: visible progress bar/percentage for long operations
- Avoid timeouts: prove that work is advancing
- Debugging: track where time is spent

## What you need
- Server: FastMCP with `ctx.report_progress()` (supported in FastMCP ≥ 2.3.5)
- Client (this app): supports per‑call `progress_handler` and forwards progress to UI as `tool_progress` events

## Server‑side: sending progress
Use the injected `Context` in your tool and call `await ctx.report_progress(progress=..., total=..., message=...)`.

```
from fastmcp import FastMCP, Context
import asyncio

mcp = FastMCP("ProgressDemo")

@mcp.tool
async def long_task(task: str = "demo", duration_seconds: int = 12, interval_seconds: int = 3, ctx: Context | None = None) -> dict:
    total = max(1, int(duration_seconds))
    step = max(1, int(interval_seconds))

    # initial
    if ctx is not None:
        await ctx.report_progress(progress=0, total=total, message=f"{task}: starting")

    elapsed = 0
    while elapsed < total:
        await asyncio.sleep(step)
        elapsed = min(total, elapsed + step)
        if ctx is not None:
            await ctx.report_progress(progress=elapsed, total=total, message=f"{task}: {elapsed}/{total}s")

    if ctx is not None:
        await ctx.report_progress(progress=total, total=total, message=f"{task}: done")
    return {"results": {"task": task, "status": "completed", "duration_seconds": total, "interval_seconds": step}}
```

Patterns:
- Percentage: set `total=100` and report `progress` in 0–100.
- Absolute units: report `progress=i` and `total=N`.
- Indeterminate: omit `total`—the UI shows a spinner with message.
- Multi‑stage: reuse 0–100 range per stage and emit stage messages with `ctx.info` and `ctx.report_progress`.

## Client‑side: how this app handles progress
- The backend passes a `progress_handler` to each FastMCP `call_tool`.
- The handler emits a websocket message:
  - `{ type: "tool_progress", tool_call_id, tool_name, progress, total, percentage, message }`
- The frontend updates the in‑flight tool message (status `in_progress`, progress %, message).

No changes are needed to enable this—already wired in this repo.

## Try the built‑in demo server
A sample server is provided at `backend/mcp/progress_demo/main.py`.

Add this server to your MCP config (overrides or old paths). Example (JSON):

```
{
  "progress_demo": {
    "transport": "stdio",
    "command": ["python", "main.py"],
    "cwd": "backend/mcp/progress_demo",
    "groups": ["mcp_basic"]
  }
}
```

- Select the `progress_demo_long_task` tool in the UI.
- Run it; you should see progress updates every 3 seconds for ~12 seconds.

## Tips
- Emit an initial 0% and a final 100% update (or `progress=total`).
- Keep messages short but useful (e.g., processed 5/20 files).
- For very chatty tasks, throttle updates (e.g., every few seconds or % change ≥ 1).
- If `ctx` is absent or the client didn’t request progress, calls are no‑ops (safe).

## Troubleshooting
- No progress in UI: ensure the server calls `ctx.report_progress` and FastMCP version ≥ 2.3.5.
- Tool finishes but no updates: check your MCP configuration (command/cwd) and server logs.
- Indeterminate tasks: omit `total`—the UI shows a spinner with the provided message.
