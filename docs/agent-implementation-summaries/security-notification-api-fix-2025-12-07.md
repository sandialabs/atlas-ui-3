# Security Check Notification API Fix

**Date:** December 7, 2025  
**Author:** GitHub Copilot (AI Agent)  
**Issue:** AttributeError when security checks attempted to send notifications

## Summary

Fixed critical bug where security check notifications were calling non-existent `publish_message()` method on WebSocketEventPublisher, causing AttributeError crashes. Replaced all occurrences with correct `send_json()` API and added comprehensive regression tests.

## Problem

Production bug discovered during manual testing:
```
AttributeError: 'WebSocketEventPublisher' object has no attribute 'publish_message'
```

The WebSocketEventPublisher class uses `send_json()` for sending messages, but security check code in both orchestrator and tools mode was incorrectly calling `publish_message()`.

## Changes Made

### 1. Backend API Fixes (6 locations)

**File: `backend/application/chat/orchestrator.py`**
- Line ~170: Input security check notification
- Line ~200: Input warning notification  
- Line ~300: Output security check notification
- Line ~325: Output warning notification

**File: `backend/application/chat/modes/tools.py`**
- Line ~153: Tool/RAG blocked notification
- Line ~172: Tool/RAG warning notification

All changed from:
```python
await self.event_publisher.publish_message({...})
```

To:
```python
await self.event_publisher.send_json({...})
```

### 2. Frontend Integration

**File: `frontend/src/handlers/chat/websocketHandlers.js`**
- Added `case 'security_warning':` handler to route messages to handleSecurityWarning()

**File: `frontend/src/components/Message.jsx`**
- Added rendering for security_warning message type
- Visual indicators: 🚫 for blocked, ⚠️ for warnings
- Color-coded backgrounds: red for blocked, yellow for warnings

### 3. Mock Server Enhancement

**File: `mocks/security_check_mock/app.py`**
- Added "bomb" keyword blocking for testing
- Added console logging of security check results
- Maintains existing functionality for other check types

### 4. Comprehensive Test Coverage

**File: `backend/tests/test_orchestrator_security_integration.py`**

Added 15 total tests across 3 test classes:

**TestOrchestratorSecurityCheckIntegration (7 tests):**
- Input blocked prevents LLM call
- Input with warnings allows processing
- Good input proceeds normally
- Output blocked removes response
- Output with warnings allows response
- No security service allows all
- Message history sent to security check

**TestOrchestratorSecurityNotificationAPI (5 tests):**
- Blocked input uses send_json (not publish_message)
- Warning input uses send_json
- Blocked output uses send_json
- Warning output uses send_json
- Event publisher does not have publish_message method

**TestToolRagSecurityNotificationAPI (3 tests):**
- Tool security check service called with correct params
- Event publisher send_json available (regression test)
- Security check formats tool vs RAG correctly

All tests verify:
1. Correct method name (send_json, not publish_message)
2. Proper message structure for security warnings
3. Integration with SecurityCheckService
4. Message history context passing
5. Tool vs RAG source type differentiation

### 5. Documentation Updates

**File: `docs/admin/security-check.md`**
- Added image showing security warning UI
- Expanded Tool/RAG Security Checks section
- Added detailed flow diagrams for tool and RAG checks
- Documented message format for security warnings

## Technical Details

### Message Format
Security warnings sent via WebSocket use this structure:
```json
{
  "type": "security_warning",
  "status": "blocked" | "warning",
  "message": "Human-readable explanation",
  "check_type": "input" | "output" | "tool" | "rag"
}
```

### Security Check Types
1. **Input Check**: User message before LLM processing
2. **Output Check**: LLM response before returning to user
3. **Tool/RAG Check**: Tool execution results and RAG retrieved content

### Event Publisher API
- **Correct method**: `send_json(dict)` - sends JSON message via WebSocket
- **Incorrect method**: `publish_message(dict)` - does not exist, causes AttributeError

## Testing Results

**Backend Tests:** 376 passed, 7 skipped
- All existing tests continue to pass
- 15 new security integration tests added
- Regression prevention for publish_message error

**Frontend:** Security warnings display correctly with proper styling

**Mock Server:** Enhanced with keyword blocking and console logging for testing

## Key Decisions

1. **Test Strategy**: Used integration tests at orchestrator level rather than attempting to test ToolsModeRunner methods directly, as security check logic is embedded in the run() method without separate callable methods.

2. **API Consistency**: Standardized on send_json() throughout codebase for all WebSocket message sending.

3. **Frontend UX**: Visual distinction between blocked (🚫, red) and warning (⚠️, yellow) states for immediate user feedback.

4. **Mock Enhancement**: Added "bomb" keyword blocking to make it easy to test security check flow without complex setup.

## Files Modified

**Backend:**
- `backend/application/chat/orchestrator.py` (4 changes)
- `backend/application/chat/modes/tools.py` (2 changes)
- `backend/tests/test_orchestrator_security_integration.py` (15 new tests)

**Frontend:**
- `frontend/src/handlers/chat/websocketHandlers.js` (1 case added)
- `frontend/src/components/Message.jsx` (security_warning rendering)

**Mocks:**
- `mocks/security_check_mock/app.py` (keyword blocking + logging)

**Documentation:**
- `docs/admin/security-check.md` (expanded tool/RAG coverage)
- `docs/agent-implementation-summaries/security-notification-api-fix-2025-12-07.md` (this file)

## Lessons Learned

1. **Method Name Consistency**: Critical to verify correct API method names, especially for error-prone async event publishing patterns.

2. **Integration Testing**: Sometimes higher-level integration tests are more appropriate than unit tests when functionality is embedded in complex orchestration logic.

3. **UI Feedback**: Security warnings need immediate, clear visual feedback to users - icons and colors are important.

4. **Regression Prevention**: Comprehensive tests prevent regressions better than bug fixes alone.

## Future Considerations

1. Consider refactoring ToolsModeRunner to make tool result processing more testable as separate methods.

2. Add E2E tests that verify security warnings appear in the actual UI for all check types.

3. Consider logging security check results for audit trail purposes.

4. Evaluate whether tool/RAG security checks should have different severity levels or handling.
