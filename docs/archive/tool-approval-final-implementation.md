# Tool Approval System - Final Implementation

## Overview

The tool approval system provides a two-tier approval mechanism:
1. **Admin-level**: Administrators can mandate approval for specific tools
2. **User-level**: Users control auto-approval for non-admin-required tools

## User Experience

### Approval in Chat Area

Approval requests now appear as inline system messages in the chat, similar to tool calls and agent messages:

```
┌─────────────────────────────────────────────────┐
│ [S] System                                      │
│ ┌───────────────────────────────────────────┐   │
│ │ [APPROVAL REQUIRED] code-executor_run_py…│   │
│ │                                           │   │
│ │ ▶ Tool Arguments (1 params) [Edit Args]  │   │
│ │   {                                       │   │
│ │     "code": "print('Hello, world!')"     │   │
│ │   }                                       │   │
│ │                                           │   │
│ │ Rejection Reason (optional):              │   │
│ │ [________________________]                │   │
│ │                                           │   │
│ │ [Reject] [Approve]                        │   │
│ └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### Large Text Handling

When editing large inputs (code, markdown, JSON):
- Edit mode shows each argument in a separate textarea
- Each textarea auto-sizes: min 3 rows, max 20 rows
- Container has max-height of 60vh (60% of viewport)
- Scrollable when content exceeds available space
- Full screen width for editing

Example for large code input:
```
┌─────────────────────────────────────────────────┐
│ Edit Mode Active                                │
│ ┌───────────────────────────────────────────┐   │
│ │ code                                      │   │
│ │ ┌─────────────────────────────────────┐   │   │
│ │ │ def analyze_data(df):               │   │ ← Scrollable
│ │ │     # Process data                  │   │   area
│ │ │     results = []                    │   │   (60vh)
│ │ │     for col in df.columns:          │   │
│ │ │         stats = calculate_stats(c…) │   │
│ │ │         results.append(stats)       │   │
│ │ │     return results                  │   │
│ │ └─────────────────────────────────────┘   │   │
│ └───────────────────────────────────────────┘   │
│ [Reject] [Approve (with edits)]                 │
└─────────────────────────────────────────────────┘
```

### User Settings

New toggle in Settings panel:

```
┌─────────────────────────────────────────────────┐
│ Auto-Approve Tool Calls          [●────○]       │
│                                                  │
│ When enabled, tools that don't require admin    │
│ approval will execute automatically without     │
│ prompting. Tools that require admin approval    │
│ will still prompt for confirmation.             │
│                                                  │
│ ⚠ Currently: You will be prompted to approve   │
│ all tool calls unless admin has disabled        │
│ approval for specific tools.                    │
└─────────────────────────────────────────────────┘
```

When auto-approval is enabled:
```
┌─────────────────────────────────────────────────┐
│ [S] System                                      │
│ ┌───────────────────────────────────────────┐   │
│ │ [APPROVAL REQUIRED] [AUTO-APPROVING...] │   │
│ │ calculator_eval                           │   │
│ │ ▶ Tool Arguments (1 params)               │   │
│ └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```
(Automatically approves after brief delay)

## Technical Implementation

### Backend Logic

```python
def requires_approval(tool_name, config_manager):
    """
    Returns: (needs_approval, allow_edit, admin_required)
    
    admin_required = True:  Admin mandates approval (always enforced)
    admin_required = False: User-level approval (can auto-approve)
    """
    if tool in admin_config.tools:
        if admin_config.tools[tool].require_approval:
            return (True, allow_edit, True)  # Admin-required
        else:
            return (False, True, False)  # Admin disabled
    
    if admin_config.require_approval_by_default:
        return (True, True, True)  # Admin-required by default
    
    return (True, True, False)  # User-level approval
```

### Frontend Auto-Approval Logic

```javascript
// In ToolApprovalMessage component
useEffect(() => {
  if (settings?.autoApproveTools && 
      !message.admin_required && 
      message.status === 'pending') {
    // Auto-approve after brief delay to show message
    setTimeout(() => {
      sendApprovalResponse({
        tool_call_id: message.tool_call_id,
        approved: true,
        arguments: message.arguments
      })
    }, 100)
  }
}, [settings, message])
```

## Configuration Examples

### Example 1: Admin Requires Specific Tools


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
    }
  }
}
```

**User experience:**
- Python/Bash execution: ALWAYS prompts (admin-required, no auto-approval)
- Other tools: Prompt by default, can enable auto-approval in settings
- User toggles auto-approval ON: Calculator/other tools auto-approve
- User toggles auto-approval OFF: All tools prompt

### Example 2: Strict Admin Mode

**Backend config:**
```json
{
  "require_approval_by_default": true,
  "tools": {}
}
```

**User experience:**
- ALL tools: ALWAYS prompt (admin-required)
- Auto-approval toggle has no effect (admin overrides)
- Maximum security mode

### Example 3: User-Controlled Mode

**Backend config:**
```json
{
  "require_approval_by_default": false,
  "tools": {}
}
```

**User experience:**
- All tools: Prompt by default
- User can enable auto-approval for all tools
- Maximum user flexibility

## Workflow Examples

### Scenario 1: Code Execution with Editing

1. User: "Write and run a Python script to analyze this data"
2. LLM generates code
3. System shows approval message in chat:
   - Yellow badge: "APPROVAL REQUIRED"
   - Tool name: "code-executor_run_python"
   - Arguments collapsed by default
4. User clicks expand arrow to view code
5. User clicks "Edit Arguments"
6. Large textarea appears (60vh height, scrollable)
7. User modifies code
8. User clicks "Approve (with edits)"
9. Tool executes with edited code
10. Result appears in chat

### Scenario 2: Auto-Approval Enabled

1. User enables "Auto-Approve Tool Calls" in Settings
2. User: "Calculate 15% of 250"
3. LLM calls calculator_eval
4. System shows approval message with "AUTO-APPROVING..." badge
5. After 100ms, automatically approves
6. Tool executes
7. Result appears in chat

### Scenario 3: Admin-Required Tool

1. User enables auto-approval in Settings
2. User: "Run this bash command"
3. LLM calls code-executor_run_bash
4. System shows approval message (admin-required flag set)
5. Auto-approval does NOT activate (admin override)
6. User must manually approve or reject
7. User reviews command and approves
8. Tool executes

## Benefits

1. **Inline Experience**: Approval fits naturally in chat flow
2. **Contextual**: See approval request in context of conversation
3. **Flexible**: Large text editing works well for code/markdown
4. **Two-Tier Security**:
   - Admins enforce critical tool approval
   - Users control convenience vs. security for other tools
5. **Clear Indicators**: Visual feedback for auto-approval status
6. **Backward Compatible**: Works with existing agent modes

## Migration Notes

- Modal dialog code removed but not deleted (ToolApprovalDialog.jsx still exists for reference)
- Settings persist in localStorage
- Default setting: auto-approval OFF (safe default)
- Existing admin configs work unchanged
- New `admin_required` flag is backward compatible (defaults to false if missing)
