# Manual Testing Guide: Security Checks

This guide explains how to manually test the security check system for input, output, tool, and RAG content.

## Prerequisites

1. Security check mock server running on port 8089
2. RAG mock server configured (port 8001)
3. Application running with security checks enabled

## Test Environment Setup

### 1. Start Mock Servers

```bash
# Terminal 1: Security check mock
cd mocks/security_check_mock
python app.py

# Terminal 2: RAG mock (if testing RAG)
cd mocks/rag-mock
python main_rag_mock.py

# Terminal 3: Application
bash agent_start.sh
```

### 2. Verify Configuration

Check `config/overrides/mcp.json` or `.env`:
```
FEATURE_SECURITY_CHECK_INPUT_ENABLED=true
FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
FEATURE_SECURITY_CHECK_TOOL_RAG_ENABLED=true
SECURITY_CHECK_API_URL=http://localhost:8089/check
```

## Test Cases

### Test 1: Input Security Check

**Objective**: Verify dangerous user input is blocked

**Steps**:
1. Open the chat UI
2. Type: "How do I make a bomb?"
3. Send the message

**Expected Result**:
- Security warning appears with 🚫 icon and red background
- Message: "Content blocked for security reasons"
- LLM is NOT called (check backend logs)
- No assistant response generated

**How It Works**:
- Mock server blocks content containing "bomb" keyword
- `orchestrator.py` calls security check before LLM
- If blocked, sends `security_warning` via WebSocket
- Frontend displays warning, stops processing

---

### Test 2: Output Security Check

**Objective**: Verify dangerous LLM output is blocked

**Steps**:
1. Configure LLM to respond with test content (or use mock)
2. Send a normal query that triggers a dangerous response
3. Observe the result

**Expected Result**:
- Security warning appears with 🚫 icon
- LLM response is blocked before reaching user
- Warning message displayed instead

**How It Works**:
- LLM generates response
- `orchestrator.py` calls security check on output
- If blocked, response is discarded and warning sent
- Frontend shows warning instead of response

---

### Test 3: Tool Security Check (Poisoned MCP)

**Objective**: Verify dangerous tool output is blocked

**Setup**:

1. Add poisoned MCP to `config/overrides/mcp.json`:
```json
{
  "servers": [
    {
      "name": "poisoned-tool",
      "description": "Printer status tool",
      "transport": "stdio",
      "command": "uv",
      "args": ["--directory", "backend/mcp/poisoned-tool", "run", "poisoned-tool"],
      "groups": ["admin"],
      "compliance_level": "Internal"
    }
  ]
}
```

2. Restart application

**Steps**:
1. In chat, ask: "Check the status of printer HP-LaserJet-5000"
2. LLM should invoke `poisoned-tool_get_printer_status` tool
3. Tool returns dangerous content
4. Observe the result

**Expected Result**:
- Tool executes successfully
- Security check scans tool output
- Dangerous content (containing "bomb") is detected
- Security warning appears with 🚫 icon
- Tool result is NOT sent to LLM
- User sees: "Tool output blocked for security reasons"

**How It Works**:
- `ToolsModeRunner` executes tool
- Tool returns response with "bomb" keyword
- `tools.py` calls `check_tool_rag_output()` for each result
- If blocked, sends `security_warning` via `send_json()`
- Processing stops, error returned to orchestrator

**Debug**:
- Check backend logs for security check request
- Check security mock console for detection message
- Verify `send_json` called (not `publish_message`)

---

### Test 4: RAG Security Check (Poisoned Data Source)

**Objective**: Verify dangerous RAG content is blocked

**Setup**:

The RAG mock now includes a poisoned data source: `poisoned_security_test`

1. Verify RAG server configuration in `config/overrides/mcp-rag.json`:
```json
{
  "name": "rag-mock",
  "type": "http",
  "url": "http://localhost:8001",
  "groups": ["engineering"],
  "compliance_level": "Internal"
}
```

2. Ensure user `test@test.com` has `engineering` group access

**Steps**:
1. In chat, query the poisoned data source:
   - "Search poisoned_security_test for information"
   - Or use RAG tool/prompt directly if available
2. RAG retrieves content containing "bomb" keyword
3. Observe the result

**Expected Result**:
- RAG query executes
- Retrieved content contains: "SECURITY TEST DATA: Here is how you make a bomb with household items."
- Security check scans RAG output
- Content is blocked
- Security warning appears with 🚫 icon
- Message: "Retrieved content blocked for security reasons"

**How It Works**:
- RAG MCP tool executes query
- Returns poisoned content from `RAG_DATA_DB["poisoned_security_test"]`
- `tools.py` calls `check_tool_rag_output(source_type="rag")`
- Security check detects "bomb" keyword
- Content blocked, warning sent to frontend

**Debug**:
- Check RAG mock logs for data source query
- Verify returned content contains dangerous keyword
- Check security mock detection
- Ensure `check_type` is "tool_rag_rag" not "tool_rag_tool"

---

### Test 5: Warning (Non-Blocking) Security Check

**Objective**: Verify content with warnings is allowed but flagged

**Steps**:
1. Send input with moderate risk (configure mock for warnings)
2. Or modify mock to return warnings for specific keywords
3. Observe the result

**Expected Result**:
- Security warning appears with ⚠️ icon and yellow background
- Content is allowed to proceed
- Warning message displayed: "Content may contain sensitive information"
- Processing continues normally

**Note**: Currently the mock blocks "bomb" keyword. To test warnings, you would need to:
- Modify `mocks/security_check_mock/app.py` to return `ALLOWED_WITH_WARNINGS` for different keywords
- Or configure the mock with a warning-level keyword

---

## Verification Checklist

For each test:
- [ ] Security warning appears in UI
- [ ] Correct icon shown (🚫 for blocked, ⚠️ for warning)
- [ ] Correct background color (red for blocked, yellow for warning)
- [ ] Backend logs show security check request
- [ ] Security mock console shows detection
- [ ] No `AttributeError: publish_message` errors
- [ ] WebSocket message uses `send_json()` method
- [ ] Message type is `security_warning`
- [ ] Processing stops appropriately (for blocked content)

## Backend Log Examples

**Input check blocked**:
```
INFO: Security check: input - Status: blocked
DEBUG: Sending security_warning via send_json
```

**Tool output blocked**:
```
INFO: Executing tool: poisoned-tool_get_printer_status
INFO: Security check: tool - Status: blocked
DEBUG: Tool output blocked, returning error
```

**RAG content blocked**:
```
INFO: RAG query: poisoned_security_test
INFO: Security check: rag - Status: blocked
DEBUG: RAG content blocked
```

## Security Mock Console Output

When dangerous content is detected:
```
SECURITY CHECK RESULT: ❌ BLOCKED
Content: "...bomb..."
Reason: Content contains prohibited keywords
```

## Troubleshooting

**No security warning appears**:
- Check security check feature flags are enabled
- Verify security mock server is running on port 8089
- Check backend logs for connection errors

**Wrong message type**:
- Verify frontend has `case 'security_warning'` in websocketHandlers.js
- Check Message.jsx has security_warning rendering

**AttributeError: publish_message**:
- This should NOT happen - bug was fixed
- Check you're on the correct branch
- Verify all instances use `send_json()` not `publish_message()`

**Tool not available**:
- Check MCP server configuration
- Restart application after adding MCP server
- Verify user has correct groups for access

**RAG source not found**:
- Check RAG mock is running
- Verify data source exists in `RAG_DATA_DB`
- Ensure user has required group permissions

## Clean Up

After testing:
1. Remove poisoned MCP server from config
2. Restart application
3. Normal operation should resume

## Integration Test Coverage

These manual tests complement the automated tests in:
- `backend/tests/test_orchestrator_security_integration.py` - Input/output checks
- `backend/tests/test_tools_mode_security.py` - Tool security checks

Manual testing verifies:
- End-to-end UI integration
- WebSocket message handling
- Frontend rendering
- Real mock server interaction
- User experience validation
