# Progress Updates Demo MCP Server

This MCP server demonstrates the enhanced progress reporting capabilities that allow MCP servers to send viewable updates to the frontend during tool execution.

## Features

This demo shows three types of enhanced progress updates:

1. **Canvas Updates**: Display HTML visualizations in the canvas panel during execution
2. **System Messages**: Send rich messages that appear in chat history
3. **Progress Artifacts**: Share file artifacts progressively as they're generated

## Tools

### `task_with_canvas_updates`

Demonstrates sending HTML progress visualizations to the canvas during execution.

**Parameters:**
- `task_name` (str): Name of the task (default: "demo")
- `steps` (int): Number of steps to process (default: 5)
- `interval_seconds` (int): Delay between steps (default: 2)

### `task_with_system_messages`

Demonstrates sending rich system messages to chat history during execution.

**Parameters:**
- `task_name` (str): Name of the analysis task (default: "analysis")
- `stages` (int): Number of stages to process (default: 4)
- `interval_seconds` (int): Delay between stages (default: 2)

### `task_with_artifacts`

Demonstrates sending file artifacts progressively during execution.

**Parameters:**
- `task_name` (str): Name of the processing task (default: "data_processing")
- `files_to_generate` (int): Number of intermediate files (default: 3)
- `interval_seconds` (int): Delay between file generation (default: 2)

## How It Works

MCP servers can send structured progress updates by encoding JSON data in the progress message field with the prefix `"MCP_UPDATE:"`.

### Supported Update Types

#### 1. Canvas Update
```python
update_payload = {
    "type": "canvas_update",
    "content": "<html>...</html>",  # HTML content to display
    "progress_message": "Processing..."  # Optional progress text
}
await ctx.report_progress(
    progress=step,
    total=total_steps,
    message=f"MCP_UPDATE:{json.dumps(update_payload)}"
)
```

#### 2. System Message
```python
update_payload = {
    "type": "system_message",
    "message": "Data validation complete!",
    "subtype": "success",  # or "info", "warning", "error"
    "progress_message": "Validating data..."
}
await ctx.report_progress(
    progress=step,
    total=total_steps,
    message=f"MCP_UPDATE:{json.dumps(update_payload)}"
)
```

#### 3. Artifacts
```python
update_payload = {
    "type": "artifacts",
    "artifacts": [
        {
            "name": "result.html",
            "b64": base64_encoded_content,
            "mime": "text/html",
            "size": content_size,
            "description": "Intermediate result",
            "viewer": "html"
        }
    ],
    "display": {
        "open_canvas": True,
        "primary_file": "result.html",
        "mode": "replace"
    },
    "progress_message": "Generated result..."
}
await ctx.report_progress(
    progress=step,
    total=total_steps,
    message=f"MCP_UPDATE:{json.dumps(update_payload)}"
)
```

## Usage

Try these example prompts:

```
Show me a task with canvas updates
Run task_with_system_messages
Generate artifacts progressively
```

## Benefits

- **Better UX**: Users see real-time progress with visual feedback
- **Reduced perceived latency**: Long-running tasks feel more responsive
- **More informative**: Rich context about what's happening at each stage
- **Flexible**: Can display any HTML content, images, or file artifacts
