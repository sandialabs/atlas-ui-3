# Tool Approval System

## Overview

The tool approval system allows administrators to configure which tools require user approval before execution. This provides an additional security layer for potentially sensitive or dangerous operations.

## Features

- **Configurable Approval Requirements**: Specify which tools require approval on a per-tool basis
- **Global Default Setting**: Set a default approval requirement for all tools
- **Argument Editing**: Allow users to edit tool arguments before approving execution
- **Timeout Handling**: Automatically reject tool calls that don't receive approval within a configurable timeout period
- **Real-time UI**: Modal dialog shows pending approval requests with full argument details

## Configuration

### Configuration Files

Tool approval settings are managed in JSON configuration files:

- **Defaults**: `config/defaults/tool-approvals.json`
- **Overrides**: `config/overrides/tool-approvals.json`

### Configuration Format

```json
{
  "require_approval_by_default": false,
  "tools": {
    "server_toolname": {
      "require_approval": true,
      "allow_edit": true
    }
  }
}
```

### Configuration Fields

- `require_approval_by_default` (boolean): If true, all tools require approval unless explicitly configured otherwise
- `tools` (object): Tool-specific approval settings
  - Key: Tool name in the format `server_toolname` (e.g., `code-executor_run_python`)
  - Value: Tool approval configuration object:
    - `require_approval` (boolean): Whether this tool requires approval
    - `allow_edit` (boolean): Whether users can edit the tool arguments before approval

### Example Configuration

```json
{
  "require_approval_by_default": false,
  "tools": {
    "code-executor_run_python": {
      "require_approval": true,
      "allow_edit": true
    },
    "code-executor_run_bash": {
      "require_approval": true,
      "allow_edit": true
    },
    "filesystem_write_file": {
      "require_approval": true,
      "allow_edit": false
    }
  }
}
```

In this example:
- Most tools don't require approval (default is false)
- Python and Bash code execution require approval with argument editing allowed
- File write operations require approval but don't allow argument editing

## User Experience

### Approval Dialog

When a tool requiring approval is called, the user sees a modal dialog with:

1. **Tool Information**: Tool name and server
2. **Arguments Display**: Full JSON view of the tool arguments
3. **Edit Mode** (if allowed): Ability to modify arguments before approval
4. **Actions**:
   - **Approve**: Execute the tool with current (or edited) arguments
   - **Reject**: Cancel the tool execution with optional reason

### Timeout Behavior

- If the user doesn't respond within 5 minutes (300 seconds), the tool call is automatically rejected
- The timeout error is displayed in the chat interface

## Backend Architecture

### Components

1. **Configuration Manager** (`backend/modules/config/config_manager.py`)
   - Loads and validates tool approval configuration
   - Provides `ToolApprovalsConfig` model

2. **Approval Manager** (`backend/application/chat/approval_manager.py`)
   - Manages pending approval requests
   - Handles approval/rejection responses
   - Implements timeout logic using asyncio futures

3. **Tool Execution** (`backend/application/chat/utilities/tool_utils.py`)
   - Checks if tool requires approval before execution
   - Sends approval request to frontend
   - Waits for user response
   - Executes tool with approved (potentially edited) arguments

4. **WebSocket Handler** (`backend/main.py`)
   - Handles `tool_approval_response` messages from frontend
   - Routes responses to approval manager

### Workflow

```
1. LLM decides to call a tool
2. Tool execution checks if approval is required
3. If approval required:
   a. Send tool_approval_request to frontend
   b. Create approval request in approval manager
   c. Wait for response (with timeout)
   d. Handle approval/rejection
4. Execute tool (if approved)
5. Return result to LLM
```

## Frontend Architecture

### Components

1. **ToolApprovalDialog** (`frontend/src/components/ToolApprovalDialog.jsx`)
   - React component that displays the approval dialog
   - Handles argument editing (if allowed)
   - Sends approval/rejection response

2. **WebSocket Handler** (`frontend/src/handlers/chat/websocketHandlers.js`)
   - Handles `tool_approval_request` messages from backend
   - Updates approval request state

3. **ChatContext** (`frontend/src/contexts/ChatContext.jsx`)
   - Manages approval request state
   - Provides methods to send responses and clear requests

4. **App** (`frontend/src/App.jsx`)
   - Renders the approval dialog when request is present
   - Handles approval response submission

### Message Protocol

#### Backend → Frontend: `tool_approval_request`

```json
{
  "type": "tool_approval_request",
  "tool_call_id": "unique-call-id",
  "tool_name": "server_toolname",
  "arguments": {
    "param1": "value1",
    "param2": "value2"
  },
  "allow_edit": true
}
```

#### Frontend → Backend: `tool_approval_response`

```json
{
  "type": "tool_approval_response",
  "tool_call_id": "unique-call-id",
  "approved": true,
  "arguments": {
    "param1": "edited_value1",
    "param2": "value2"
  },
  "reason": "Optional rejection reason"
}
```

## Testing

### Running Tests

```bash
# Test approval manager
python -m pytest backend/tests/test_approval_manager.py -v

# Test configuration loading
python -m pytest backend/tests/test_config_manager.py -v
```

### Test Coverage

The test suite includes:
- Approval request creation and lifecycle
- Approval/rejection handling
- Timeout behavior
- Manager singleton pattern
- Full approval workflow simulation

## Security Considerations

1. **Default Deny**: When in doubt, configure tools to require approval
2. **Least Privilege**: Only enable argument editing when necessary
3. **Audit Trail**: All approval decisions are logged
4. **Timeout Protection**: Prevents indefinite hanging on approval requests
5. **User Authentication**: Approval responses are tied to authenticated sessions

## Future Enhancements

Potential improvements:
- Role-based approval requirements (different settings per user group)
- Approval history and audit log
- Bulk approval for multiple tool calls
- Approval delegation (assign to another user)
- Custom timeout per tool
- Pre-approved argument patterns (e.g., allow specific file paths)

## Troubleshooting

### Tools Not Requiring Approval

Check:
1. Configuration file is loaded correctly
2. Tool name matches format `server_toolname`
3. `require_approval` is set to `true` in config
4. Configuration cache is cleared if recently changed

### Approval Dialog Not Appearing

Check:
1. WebSocket connection is active
2. Frontend has loaded the latest build
3. Browser console for JavaScript errors
4. Backend logs for approval request sending

### Timeouts

If approval requests timeout frequently:
1. Check network connectivity
2. Verify user is actively monitoring the chat
3. Consider increasing timeout in `execute_single_tool` (currently 300 seconds)

## Example Usage Scenarios

### Scenario 1: Code Execution Review

**Configuration**:
```json
{
  "tools": {
    "code-executor_run_python": {
      "require_approval": true,
      "allow_edit": true
    }
  }
}
```

**User Experience**:
1. User asks: "Create a Python script to analyze this data"
2. LLM generates code and wants to execute it
3. User sees approval dialog with the Python code
4. User reviews code, optionally edits it
5. User approves or rejects execution

### Scenario 2: File System Protection

**Configuration**:
```json
{
  "tools": {
    "filesystem_delete_file": {
      "require_approval": true,
      "allow_edit": false
    },
    "filesystem_write_file": {
      "require_approval": true,
      "allow_edit": true
    }
  }
}
```

**User Experience**:
- File deletions always require approval (no editing to prevent accidents)
- File writes require approval and allow editing the content/path

### Scenario 3: Strict Mode

**Configuration**:
```json
{
  "require_approval_by_default": true,
  "tools": {
    "calculator_eval": {
      "require_approval": false
    }
  }
}
```

**User Experience**:
- All tools require approval by default
- Only safe tools like calculator are auto-approved
- Maximum control and security
