# Progress Updates and Intermediate Results

Last updated: 2026-01-19

Long-running MCP tools can now send intermediate updates to the frontend during execution, providing users with real-time feedback. This includes:

- **Canvas Updates**: Display HTML visualizations, plots, or images in the canvas panel as the tool progresses
- **System Messages**: Add rich, formatted messages to the chat history to show what's happening at each stage
- **Progressive Artifacts**: Send file artifacts as they're generated, rather than only at the end

## Basic Progress Reporting

FastMCP provides a `Context` object that tools can use to report progress:

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("MyServer")

@mcp.tool
async def long_task(
    steps: int = 5,
    ctx: Context | None = None
) -> dict:
    """A tool that reports progress."""
    
    for i in range(steps):
        # Standard progress reporting
        if ctx:
            await ctx.report_progress(
                progress=i,
                total=steps,
                message=f"Processing step {i+1} of {steps}"
            )
        
        # Do work...
        await asyncio.sleep(1)
    
    return {"results": {"status": "completed", "steps": steps}}
```

This shows a progress bar in the UI with percentage and message updates.

## Enhanced Progress Updates

To send richer updates (canvas content, system messages, or artifacts), encode structured data in the progress message with the `MCP_UPDATE:` prefix:

### 1. Canvas Updates

Display HTML content in the canvas panel during execution:

```python
import json

@mcp.tool
async def task_with_visualization(
    steps: int = 5,
    ctx: Context | None = None
) -> dict:
    """Shows visual progress in the canvas."""
    
    for step in range(1, steps + 1):
        # Create HTML visualization
        html_content = f"""
        <html>
          <body style="padding: 20px;">
            <h1>Processing Step {step}/{steps}</h1>
            <div style="width: 100%; background: #eee; height: 30px;">
              <div style="width: {(step/steps)*100}%; background: #4CAF50; height: 100%;"></div>
            </div>
          </body>
        </html>
        """
        
        # Send canvas update
        if ctx:
            update_payload = {
                "type": "canvas_update",
                "content": html_content,
                "progress_message": f"Step {step}/{steps}"
            }
            await ctx.report_progress(
                progress=step,
                total=steps,
                message=f"MCP_UPDATE:{json.dumps(update_payload)}"
            )
    
    return {"results": {"status": "completed"}}
```

### 2. System Messages

Add informative messages to the chat history:

```python
@mcp.tool
async def task_with_status_updates(
    stages: list[str],
    ctx: Context | None = None
) -> dict:
    """Reports status updates as chat messages."""
    
    for i, stage in enumerate(stages, 1):
        # Do work for this stage...
        await process_stage(stage)
        
        # Send system message
        if ctx:
            update_payload = {
                "type": "system_message",
                "message": f"**{stage}** completed successfully",
                "subtype": "success",  # or "info", "warning", "error"
                "progress_message": f"Completed {stage}"
            }
            await ctx.report_progress(
                progress=i,
                total=len(stages),
                message=f"MCP_UPDATE:{json.dumps(update_payload)}"
            )
    
    return {"results": {"status": "completed", "stages": len(stages)}}
```

### 3. Progressive Artifacts

Send file artifacts as they're generated:

```python
import base64

@mcp.tool
async def task_with_intermediate_files(
    files_to_generate: int = 3,
    ctx: Context | None = None
) -> dict:
    """Generates and displays files progressively."""
    
    for file_num in range(1, files_to_generate + 1):
        # Generate file content
        html_content = f"<html><body><h1>Result {file_num}</h1></body></html>"
        
        # Send artifact
        if ctx:
            artifact_data = {
                "type": "artifacts",
                "artifacts": [
                    {
                        "name": f"result_{file_num}.html",
                        "b64": base64.b64encode(html_content.encode()).decode(),
                        "mime": "text/html",
                        "size": len(html_content),
                        "description": f"Intermediate result {file_num}",
                        "viewer": "html"
                    }
                ],
                "display": {
                    "open_canvas": True,
                    "primary_file": f"result_{file_num}.html",
                    "mode": "replace"
                },
                "progress_message": f"Generated file {file_num}"
            }
            await ctx.report_progress(
                progress=file_num,
                total=files_to_generate,
                message=f"MCP_UPDATE:{json.dumps(artifact_data)}"
            )
    
    return {"results": {"files_generated": files_to_generate}}
```

## Update Types Reference

| Type | Fields | Description |
|------|--------|-------------|
| `canvas_update` | `content` (HTML string), `progress_message` (optional) | Displays HTML content in the canvas panel |
| `system_message` | `message` (string), `subtype` (info/success/warning/error), `progress_message` (optional) | Adds a formatted message to chat history |
| `artifacts` | `artifacts` (list), `display` (object), `progress_message` (optional) | Sends file artifacts with display hints |

## Example: Complete Demo Server

See `/backend/mcp/progress_updates_demo/` for a complete working example with three tools demonstrating all update types. To try it:

1. Add the server to your `config/defaults/mcp.json` (or an overrides directory set via `APP_CONFIG_OVERRIDES`):
```json
{
  "progress_updates_demo": {
    "command": ["python", "mcp/progress_updates_demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Demo server showing enhanced progress updates"
  }
}
```

2. Restart the backend and ask: "Show me a task with canvas updates"
