# MCP Progress Updates - Quick Start Guide

This guide shows how to use the enhanced MCP progress reporting capabilities to send viewable updates to the frontend during tool execution.

## Overview

MCP servers can now send three types of intermediate updates:

1. **Canvas Updates**: Display HTML visualizations in real-time
2. **System Messages**: Add rich status messages to chat history
3. **Progressive Artifacts**: Send files as they're generated

## Basic Setup

### 1. Enable the Demo Server

Add to `config/overrides/mcp.json`:

```json
{
  "servers": {
    "progress_updates_demo": {
      "command": ["python", "mcp/progress_updates_demo/main.py"],
      "cwd": "atlas",
      "groups": ["users"],
      "description": "Demo server showing enhanced progress updates"
    }
  }
}
```

### 2. Restart Backend

```bash
# Stop the backend if running
# Then start it again
cd /path/to/atlas-ui-3
cd atlas
python main.py
```

### 3. Try It Out

Open the Atlas UI and try these prompts:

```
Show me a task with canvas updates
Run task_with_system_messages
Generate artifacts progressively
```

## Creating Your Own Progress Updates

### Example 1: Canvas Updates

```python
from fastmcp import FastMCP, Context
import asyncio
import json

mcp = FastMCP("MyServer")

@mcp.tool
async def visualize_progress(
    steps: int = 5,
    ctx: Context | None = None
) -> dict:
    """Shows visual progress in canvas."""
    
    for step in range(1, steps + 1):
        # Create HTML visualization
        html = f"""
        <html>
          <body style="padding: 20px; font-family: Arial;">
            <h1>Processing Step {step}/{steps}</h1>
            <div style="width: 100%; background: #eee; height: 30px;">
              <div style="width: {(step/steps)*100}%; 
                          background: #4CAF50; height: 100%;">
              </div>
            </div>
          </body>
        </html>
        """
        
        # Send canvas update
        if ctx:
            update_payload = {
                "type": "canvas_update",
                "content": html,
                "progress_message": f"Step {step}/{steps}"
            }
            await ctx.report_progress(
                progress=step,
                total=steps,
                message=f"MCP_UPDATE:{json.dumps(update_payload)}"
            )
        
        await asyncio.sleep(1)
    
    return {"results": {"status": "completed"}}

if __name__ == "__main__":
    mcp.run()
```

### Example 2: System Messages

```python
@mcp.tool
async def process_with_updates(
    stages: list[str] = ["Init", "Process", "Finalize"],
    ctx: Context | None = None
) -> dict:
    """Shows status updates in chat."""
    
    for i, stage in enumerate(stages, 1):
        # Do work...
        await asyncio.sleep(1)
        
        # Send system message
        if ctx:
            update_payload = {
                "type": "system_message",
                "message": f"**{stage}** - Completed successfully ✓",
                "subtype": "success",
                "progress_message": f"Completed {stage}"
            }
            await ctx.report_progress(
                progress=i,
                total=len(stages),
                message=f"MCP_UPDATE:{json.dumps(update_payload)}"
            )
    
    return {"results": {"stages_completed": len(stages)}}
```

### Example 3: Progressive Artifacts

```python
import base64

@mcp.tool
async def generate_reports(
    count: int = 3,
    ctx: Context | None = None
) -> dict:
    """Generates and displays files progressively."""
    
    for i in range(1, count + 1):
        # Generate content
        html_content = f"""
        <html>
          <body style="padding: 20px;">
            <h1>Report {i}</h1>
            <p>Generated at step {i} of {count}</p>
          </body>
        </html>
        """
        
        # Send artifact
        if ctx:
            artifact_data = {
                "type": "artifacts",
                "artifacts": [
                    {
                        "name": f"report_{i}.html",
                        "b64": base64.b64encode(html_content.encode()).decode(),
                        "mime": "text/html",
                        "size": len(html_content),
                        "description": f"Report {i}",
                        "viewer": "html"
                    }
                ],
                "display": {
                    "open_canvas": True,
                    "primary_file": f"report_{i}.html"
                },
                "progress_message": f"Generated report {i}"
            }
            await ctx.report_progress(
                progress=i,
                total=count,
                message=f"MCP_UPDATE:{json.dumps(artifact_data)}"
            )
        
        await asyncio.sleep(1)
    
    return {"results": {"reports_generated": count}}
```

## Update Types Reference

### Canvas Update

```python
{
    "type": "canvas_update",
    "content": "<html>...</html>",  # HTML string to display
    "progress_message": "Optional progress text"
}
```

### System Message

```python
{
    "type": "system_message",
    "message": "Status message text",
    "subtype": "info",  # or "success", "warning", "error"
    "progress_message": "Optional progress text"
}
```

### Artifacts

```python
{
    "type": "artifacts",
    "artifacts": [
        {
            "name": "filename.ext",
            "b64": "base64_encoded_content",
            "mime": "mime/type",
            "size": 12345,
            "description": "File description",
            "viewer": "html"  # or "image", "pdf", etc.
        }
    ],
    "display": {
        "open_canvas": True,
        "primary_file": "filename.ext",
        "mode": "replace"
    },
    "progress_message": "Optional progress text"
}
```

## Tips

- **Always include progress_message**: This shows in the progress bar
- **Test with short intervals**: Start with 1-2 second delays for testing
- **HTML is powerful**: Use any HTML/CSS for canvas visualizations
- **Artifacts are stored**: Files sent as artifacts are saved to S3
- **Updates are async**: UI updates without blocking your tool

## Troubleshooting

### Updates not showing?

1. Check the backend logs for errors
2. Verify JSON is valid: `json.dumps(payload)`
3. Ensure `ctx` parameter is not None
4. Check message format: must start with `"MCP_UPDATE:"`

### Canvas not updating?

- Verify content is valid HTML
- Check browser console for errors
- Try a simple HTML first: `"<h1>Test</h1>"`

### Artifacts not displaying?

- Ensure base64 encoding is correct
- Check MIME type matches content
- Verify viewer hint is supported: html, image, pdf, etc.

## More Examples

See `/backend/mcp/progress_updates_demo/main.py` for complete working examples.

## Documentation

Full documentation: [Developer Guide - Progress Updates](../../../docs/developer/progress-updates.md)
