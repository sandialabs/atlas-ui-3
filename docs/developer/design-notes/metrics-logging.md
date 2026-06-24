# Metrics Logging Feature

Last updated: 2026-01-28

## Overview

The metrics logging feature provides a centralized way to track user activities without capturing sensitive data. This feature is controlled by a feature flag and logs only metadata about user actions.

## Configuration

### Environment Variable

Add to your `.env` file:

```
FEATURE_METRICS_LOGGING_ENABLED=true
```

Set to `true` to enable metrics logging, `false` to disable.

## Metrics Format

All metrics follow a consistent pattern:

```
[METRIC] [username] event_type key1=value1 key2=value2 ...
```

Example:
```
[METRIC] [user@example.com] llm_call model=gpt-4 message_count=5
[METRIC] [user@example.com] tool_call tool_name=calculator
[METRIC] [user@example.com] file_upload file_size=1024 content_type=application/pdf
[METRIC] [user@example.com] error error_type=rate_limit
```

## Event Types

### 1. LLM Calls

Logged when the system makes a call to an LLM provider.

**Metrics:**
- `model`: Model name (e.g., "gpt-4", "claude-3")
- `message_count`: Number of messages in the conversation
- `tool_count`: Number of tool calls made (optional, for tool-enabled calls)

**Example:**
```
[METRIC] [user@example.com] llm_call model=gpt-4 message_count=5 tool_count=2
```

### 2. Tool Calls

Logged when a user invokes an MCP tool.

**Metrics:**
- `tool_name`: Name of the tool executed (without sensitive args)

**Example:**
```
[METRIC] [user@example.com] tool_call tool_name=calculator
```

### 3. Tool Errors

Logged when a tool execution fails.

**Metrics:**
- `tool_name`: Name of the tool that failed

**Example:**
```
[METRIC] [user@example.com] tool_error tool_name=file_reader
```

### 4. File Uploads

Logged when a user uploads a file via the API.

**Metrics:**
- `file_size`: Size in bytes
- `content_type`: MIME type (e.g., "application/pdf", "image/png")

**Example:**
```
[METRIC] [user@example.com] file_upload file_size=1024 content_type=application/pdf
```

### 5. File Storage

Logged when a file is successfully stored in S3.

**Metrics:**
- `file_size`: Size in bytes
- `content_type`: MIME type
- `category`: Storage category ("uploads", "generated", "other")

**Example:**
```
[METRIC] [user@example.com] file_stored file_size=2048 content_type=image/png category=uploads
```

### 6. Errors

Logged when an error occurs for a user.

**Metrics:**
- `error_type`: Type of error (e.g., "rate_limit", "timeout", "authentication", "validation")

**Example:**
```
[METRIC] [user@example.com] error error_type=rate_limit
```

## Privacy and Security

### What is NOT Logged

The metrics logging system explicitly DOES NOT log:
- LLM prompts or message content
- Tool arguments or parameters
- File names or paths
- Detailed error messages or stack traces
- User input content
- Any personally identifiable information beyond the username

### What IS Logged

Only non-sensitive metadata:
- Counts (message count, file count)
- Sizes (file size in bytes)
- Types (model name, content type, error type, tool name)
- Categories (storage category)

## Usage in Code

### Importing

```python
from core.metrics_logger import log_metric
```

### Examples

#### Log an LLM Call
```python
log_metric("llm_call", user_email, model="gpt-4", message_count=5)
```

#### Log a Tool Call
```python
log_metric("tool_call", user_email, tool_name="calculator")
```

#### Log a File Upload
```python
log_metric("file_upload", user_email, file_size=1024, content_type="application/pdf")
```

#### Log an Error
```python
log_metric("error", user_email, error_type="rate_limit")
```

## Querying Metrics

Since all metrics use the `[METRIC]` prefix, you can easily filter logs:

```bash
# Get all metrics
grep "\[METRIC\]" application.log

# Get metrics for a specific user
grep "\[METRIC\] \[user@example.com\]" application.log

# Get all LLM calls
grep "\[METRIC\].*llm_call" application.log

# Get all errors
grep "\[METRIC\].*error" application.log

# Count tool calls per user
grep "\[METRIC\].*tool_call" application.log | cut -d' ' -f2 | sort | uniq -c
```

## Implementation Details

The metrics logging feature is implemented in `atlas/core/metrics_logger.py` and is integrated into:

1. **LLM calls** (`atlas/modules/llm/litellm_caller.py`)
   - `call_plain()` - Basic LLM calls
   - `call_with_tools()` - LLM calls with tool support

2. **Tool execution** (`atlas/modules/mcp_tools/client.py`)
   - `execute_tool()` - Tool call execution and errors

3. **File uploads** (`atlas/routes/files_routes.py`)
   - `upload_file()` - File upload API endpoint

4. **File storage** (`atlas/modules/file_storage/`)
   - `s3_client.py` and `mock_s3_client.py` - File storage operations

5. **Error handling** (`atlas/main.py`)
   - WebSocket error handlers for all error types

## Testing

The feature has been manually verified to:
1. Respect the feature flag (no logging when disabled)
2. Use the correct format pattern `[METRIC] [username] event_type ...`
3. Log only non-sensitive metadata
4. Handle missing user emails gracefully (logs as `[unknown]`)

To test manually:
1. Set `FEATURE_METRICS_LOGGING_ENABLED=true` in your `.env` file
2. Start the application
3. Perform various actions (make LLM calls, use tools, upload files)
4. Check the logs for `[METRIC]` entries
