# Metrics Logging

## Overview

This document describes the metrics logging feature added to Atlas UI 3. The metrics logging provides visibility into key user activities without logging sensitive data.

## What is Logged

The application logs the following events with the `[METRIC]` prefix:

### 1. LLM Calls

Logged when a user makes an LLM (Language Model) call:

- **Initiation**: When the LLM call starts
- **Completion**: When the LLM call finishes
- **Metadata**: Call type (plain, tools, rag, rag_and_tools), model name, message count, response length

**Example logs:**
```
[METRIC] LLM call initiated: type=plain, model=gpt-4, message_count=3
[METRIC] LLM call completed: type=plain, model=gpt-4, response_length=1234
```

**What is NOT logged:**
- User prompts/messages
- LLM responses content
- Any message content

### 2. Tool Calls

Logged when tools are executed:

- **Initiation**: When a tool call starts
- **Completion**: When a tool call finishes
- **Metadata**: Tool name, success status

**Example logs:**
```
[METRIC] Tool call initiated: tool_name=read_file
[METRIC] Tool call completed: tool_name=read_file, success=True
```

**What is NOT logged:**
- Tool arguments
- Tool results
- Any sensitive parameters

### 3. Errors

Logged when errors occur:

- **Error Type**: The type of exception that occurred
- **Category**: Where the error occurred (llm_error, tool_execution, file_upload, etc.)
- **Tool Name**: If applicable, which tool failed

**Example logs:**
```
[METRIC] Error occurred: error_type=ValueError, category=llm_error
[METRIC] Error occurred: error_type=TimeoutError, category=tool_execution, tool_name=slow_tool
```

**What is NOT logged:**
- Sensitive error details
- User data in error messages
- Full stack traces at INFO level

### 4. File Operations

Logged when users upload or store files:

- **Upload Initiation**: When a file upload starts
- **Upload Completion**: When a file upload finishes
- **File Storage**: When a file is stored in the session
- **Metadata**: File size, content type

**Example logs:**
```
[METRIC] File upload initiated: content_type=application/pdf
[METRIC] File upload completed: size_bytes=102400, content_type=application/pdf
[METRIC] File stored: size_bytes=51200, content_type=image/png
```

**What is NOT logged:**
- File names
- File content
- File paths

## Privacy and Security

### Design Principles

1. **No Sensitive Data**: The metrics logging is designed to never log sensitive data such as:
   - User prompts or messages
   - LLM responses
   - Tool arguments
   - File names or content
   - API keys or credentials

2. **Metadata Only**: Only non-sensitive metadata is logged:
   - Counts (number of messages, tools)
   - Sizes (file sizes, response lengths)
   - Types (content types, error types)
   - Success status (true/false)
   - Tool names (but not arguments)

3. **Easy Filtering**: All metrics logs are prefixed with `[METRIC]` for easy filtering and analysis.

## Usage

### Viewing Metrics Logs

To view only metrics logs, filter the application logs by the `[METRIC]` prefix:

```bash
# View all metrics logs
grep '\[METRIC\]' application.log

# View only LLM call metrics
grep '\[METRIC\] LLM call' application.log

# View only tool call metrics
grep '\[METRIC\] Tool call' application.log

# View only error metrics
grep '\[METRIC\] Error occurred' application.log

# View only file operation metrics
grep '\[METRIC\] File' application.log
```

### Log Level

All metrics logs are emitted at the `INFO` level. To see them, ensure your logging configuration is set to `INFO` or lower:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

### Parsing Metrics

Metrics logs follow a consistent format that makes them easy to parse:

```
[METRIC] <action>: key1=value1, key2=value2, key3=value3
```

Example parsing with Python:

```python
import re

log_line = "[METRIC] LLM call initiated: type=plain, model=gpt-4, message_count=3"

# Extract action
action_match = re.search(r'\[METRIC\] (.*?):', log_line)
action = action_match.group(1) if action_match else None

# Extract key-value pairs
pairs = re.findall(r'(\w+)=([^,]+)', log_line)
metrics = dict(pairs)

print(f"Action: {action}")
print(f"Metrics: {metrics}")
# Output:
# Action: LLM call initiated
# Metrics: {'type': 'plain', 'model': 'gpt-4', 'message_count': '3'}
```

## Implementation

### Files Modified

1. **backend/modules/llm/litellm_caller.py**
   - Added metrics logging for all LLM call types (plain, rag, tools, rag_and_tools)

2. **backend/application/chat/utilities/tool_executor.py**
   - Added metrics logging for tool execution initiation and completion

3. **backend/application/chat/utilities/error_handler.py**
   - Added metrics logging for error occurrences

4. **backend/routes/files_routes.py**
   - Added metrics logging for file upload operations

5. **backend/application/chat/utilities/file_processor.py**
   - Added metrics logging when files are stored in session context

### Testing

Comprehensive tests are available in `backend/tests/test_metrics_logging.py` which verify:
- LLM call logging
- Tool call logging (without arguments)
- Error logging
- File operation logging
- That sensitive data is never logged

Run tests with:
```bash
pytest backend/tests/test_metrics_logging.py -v
```

## Future Enhancements

Potential future enhancements to metrics logging:

1. **Aggregation**: Aggregate metrics over time periods
2. **Dashboards**: Create dashboards for metrics visualization
3. **Alerts**: Set up alerts for error rates or unusual patterns
4. **Export**: Export metrics to external monitoring systems
5. **User Analytics**: Track usage patterns while respecting privacy

## References

- Issue: [Better metrics in the logging](https://github.com/sandialabs/atlas-ui-3/issues/XXX)
- Tests: `backend/tests/test_metrics_logging.py`
- Demo: `/tmp/demo_metrics_logging.py`
