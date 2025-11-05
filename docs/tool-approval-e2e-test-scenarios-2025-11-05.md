# Tool Approval E2E Test Scenarios

**Last updated: 2025-11-05**

This document provides manual E2E test scenarios for the tool approval and auto-approval features in Atlas UI 3. These tests should be performed in the UI to verify the complete approval workflow.

## Test Environment Setup

Before testing, ensure:
- Backend is running with the latest changes
- Frontend is built and loaded
- Configuration file `config/overrides/tool_approvals.yml` exists
- At least one MCP tool is configured (e.g., calculator)

---

## Basic Approval Tests

### Test 1: Approve a Tool Call
**Objective:** Verify basic approval flow works end-to-end

**Steps:**
1. Send message: "use hte tool, what is 9879*tan(0.12354)"
2. Wait for approval request modal to appear
3. Review the tool name (calculator_evaluate) and arguments
4. Click "Approve" button

**Expected Result:**
- Tool executes successfully
- Result is displayed in chat
- Approval modal disappears

---

### Test 2: Reject a Tool Call
**Objective:** Verify rejection prevents tool execution

**Steps:**
1. Send message: "calculate 500 * 250"
2. Wait for approval request
3. Enter rejection reason: "Not needed right now"
4. Click "Reject" button

**Expected Result:**
- Tool does not execute
- Chat shows rejection message with reason
- User can continue chatting

---

### Test 3: Edit Arguments Before Approval
**Objective:** Verify argument editing functionality

**Steps:**
1. Send message: "calculate 100 + 50"
2. Wait for approval request
3. Click "Edit Arguments" button
4. Modify expression to "100 + 500"
5. Click "Approve" button

**Expected Result:**
- Tool executes with edited arguments (100 + 500 = 600)
- Result reflects the edited calculation

---

### Test 4: Multiple Sequential Tool Calls
**Objective:** Verify approval works for multiple tools in sequence

**Steps:**
1. Send message: "calculate 10 * 20, then calculate 5 + 5"
2. Approve first tool call (10 * 20)
3. Wait for second approval request
4. Approve second tool call (5 + 5)

**Expected Result:**
- Both tools execute in order
- Both results appear in chat
- Approval requests appear one at a time

---

## Auto-Approval Tests

### Test 5: Enable User-Level Auto-Approval
**Objective:** Verify user can enable auto-approval for all tools

**Steps:**
1. Locate auto-approval toggle in UI (user settings or approval modal)
2. Enable "Auto-approve all tools"
3. Send message: "calculate 777 * 3"

**Expected Result:**
- Tool executes immediately without approval prompt
- Result appears in chat automatically

---

### Test 6: Disable User-Level Auto-Approval
**Objective:** Verify auto-approval can be turned off

**Steps:**
1. With auto-approval enabled, disable the toggle
2. Send message: "calculate 123 + 456"

**Expected Result:**
- Approval modal appears as normal
- User must manually approve

---

### Test 7: Function-Specific Auto-Approval
**Objective:** Verify auto-approval for specific functions only

**Steps:**
1. Edit `config/overrides/tool_approvals.yml`:
```yaml
tools:
  calculator_evaluate:
    require_approval: false
    allow_edit: true
```
2. Restart backend
3. Send message: "calculate 999 / 3"

**Expected Result:**
- Calculator tool executes without approval
- Other tools (if available) still require approval

---

### Test 8: Mixed Auto-Approval and Manual Approval
**Objective:** Verify some tools auto-approve while others require approval

**Steps:**
1. Configure calculator to NOT require approval
2. Configure another tool (e.g., PDF tool) to require approval
3. Send message that uses both tools

**Expected Result:**
- Calculator executes immediately
- PDF tool shows approval modal
- Workflow continues smoothly

---

## Edge Cases and Error Handling

### Test 9: Approval Timeout
**Objective:** Verify system handles no response gracefully

**Steps:**
1. Send message: "calculate 50 * 50"
2. Wait for approval modal
3. Do not click approve or reject
4. Wait 5+ minutes (timeout period)

**Expected Result:**
- System times out gracefully
- Error message appears: "Tool execution timed out waiting for user approval"
- Chat remains functional

---

### Test 10: Cancel Chat During Approval Wait
**Objective:** Verify cancellation doesn't break system

**Steps:**
1. Send message: "calculate 88 * 11"
2. Wait for approval modal
3. Refresh page or reset session
4. Send new message

**Expected Result:**
- Previous approval request is cleared
- New chat works normally
- No hanging approvals

---

### Test 11: Rapid Approve Button Clicks
**Objective:** Verify no duplicate executions from double-clicking

**Steps:**
1. Send message: "calculate 10 + 10"
2. Wait for approval modal
3. Rapidly click "Approve" button 5 times

**Expected Result:**
- Tool executes only once
- No duplicate results
- No error messages

---

### Test 12: Invalid Edited Arguments
**Objective:** Verify validation of edited arguments

**Steps:**
1. Send message: "calculate 5 * 5"
2. Wait for approval modal
3. Edit arguments to invalid JSON: `{expression: "broken}`
4. Click "Approve"

**Expected Result:**
- Either: validation error before approval
- Or: tool execution fails gracefully with error message
- User can retry or cancel

---

## Admin Configuration Tests

### Test 13: Admin-Mandated Approval (Cannot Override)
**Objective:** Verify admin settings override user preferences

**Steps:**
1. Edit `config/overrides/tool_approvals.yml`:
```yaml
require_approval_by_default: true
tools:
  calculator_evaluate:
    require_approval: true
    allow_edit: false
```
2. Restart backend
3. Enable user-level auto-approval toggle
4. Send message: "calculate 100 * 2"

**Expected Result:**
- Approval modal appears despite user auto-approval setting
- Admin requirement overrides user preference
- "Edit Arguments" button is disabled (allow_edit: false)

---

### Test 14: Disable Argument Editing
**Objective:** Verify admin can prevent argument editing

**Steps:**
1. Configure tool with `allow_edit: false`
2. Send message: "calculate 7 * 7"
3. Approval modal appears

**Expected Result:**
- "Edit Arguments" button is hidden or disabled
- User can only approve or reject with original arguments

---

## Agent Mode Tests

### Test 15: Approvals in Agent Mode
**Objective:** Verify approval works with multi-step agent loops

**Steps:**
1. Enable agent mode in UI
2. Send message: "calculate 10 * 10, then use that result to calculate another operation"
3. Approve first tool call
4. Wait for agent to process
5. Approve any subsequent tool calls

**Expected Result:**
- Agent pauses at each approval request
- Agent continues after each approval
- Multi-step reasoning completes successfully
- All intermediate steps and results are visible

---

## Testing Checklist

Use this checklist to track test completion:

- [ ] Test 1: Basic approval
- [ ] Test 2: Basic rejection
- [ ] Test 3: Argument editing
- [ ] Test 4: Sequential tools
- [ ] Test 5: Enable auto-approval
- [ ] Test 6: Disable auto-approval
- [ ] Test 7: Function-specific auto-approval
- [ ] Test 8: Mixed approval modes
- [ ] Test 9: Timeout handling
- [ ] Test 10: Session cancellation
- [ ] Test 11: Duplicate click prevention
- [ ] Test 12: Invalid argument validation
- [ ] Test 13: Admin override
- [ ] Test 14: Disabled editing
- [ ] Test 15: Agent mode approvals

---

## Common Issues and Debugging

**Approval modal doesn't appear:**
- Check `config/overrides/tool_approvals.yml` exists
- Verify `require_approval: true` for the tool
- Check browser console for WebSocket errors

**Approve button does nothing:**
- Check backend logs for `tool_approval_response` message
- Verify WebSocket connection is open
- Look for approval manager logs

**Auto-approval not working:**
- Verify configuration is loaded (check backend startup logs)
- Ensure backend was restarted after config changes
- Check user-level setting is enabled in UI

**Tool executes twice:**
- This is a bug - report immediately
- Check for duplicate WebSocket messages in logs
