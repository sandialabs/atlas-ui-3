# Output Security Check Bug Fix - Response Shown Before Blocking

**Date:** 2025-12-07
**Severity:** High
**Status:** Fixed

## Summary

Fixed a critical bug where blocked LLM output responses were being shown to users before the security check could block them. The content was published to the frontend and then removed, meaning users briefly saw content that should have been completely hidden.

## The Bug

### Incorrect Flow (Before Fix)
```
1. Mode runner gets LLM response
2. Mode runner PUBLISHES response to frontend via publish_chat_response()
   → USER SEES BLOCKED CONTENT
3. Control returns to orchestrator
4. Orchestrator runs security check
5. Security check blocks the content
6. Orchestrator sends "blocked" message
   → User sees error message, but already saw the blocked content
```

### Example
Chat export showed:
```
A: [Blocked content about "bomb" and "gun"]

SYSTEM: The system was unable to process your request due to policy concerns.
```

The assistant message appeared in the chat before being removed, exposing the user to content that violated security policies.

## Root Cause

The bug existed in three mode runners:
1. **PlainModeRunner** (`backend/application/chat/modes/plain.py:67-71`)
2. **RagModeRunner** (`backend/application/chat/modes/rag.py:77-81`)
3. **ToolsModeRunner** (`backend/application/chat/modes/tools.py:109-113, 193-197`)

All three were publishing responses immediately after getting them from the LLM, before security checks could run.

## The Fix

### Correct Flow (After Fix)
```
1. Mode runner gets LLM response
2. Mode runner adds to history but DOES NOT publish
3. Control returns to orchestrator
4. Orchestrator runs security check
5. If blocked:
   - Remove from history
   - Send blocked notification
   - Return error (NO CONTENT PUBLISHED)
6. If allowed or warning:
   - Publish response via publish_chat_response()
   → USER ONLY SEES ALLOWED CONTENT
```

### Files Modified

**1. backend/application/chat/modes/plain.py**
```python
# REMOVED:
await self.event_publisher.publish_chat_response(
    message=response_content,
    has_pending_tools=False,
)
await self.event_publisher.publish_response_complete()

# ADDED:
# NOTE: Do NOT publish the response here - orchestrator will publish
# after security check passes. This prevents showing blocked content to users.
```

**2. backend/application/chat/modes/rag.py**
- Same change as plain.py

**3. backend/application/chat/modes/tools.py**
- Same change applied to two locations (no tool calls path and final response path)

**4. backend/application/chat/orchestrator.py**
```python
# ADDED after security check logic:
if output_check.has_warnings():
    # Send warning notification
    await self.event_publisher.send_json({...})

# Security check passed (or has warnings) - publish response to user
await self.event_publisher.publish_chat_response(
    message=assistant_content,
    has_pending_tools=False,
)
await self.event_publisher.publish_response_complete()

else:
    # No security check - publish response immediately
    assistant_content = self._extract_response_content(session)
    if assistant_content:
        await self.event_publisher.publish_chat_response(
            message=assistant_content,
            has_pending_tools=False,
        )
        await self.event_publisher.publish_response_complete()
```

## Regression Test Added

**File:** `backend/tests/test_orchestrator_security_integration.py`

**Test:** `test_output_blocked_does_not_publish_to_frontend`

This test specifically verifies:
- ✅ `publish_chat_response()` is NOT called when output is blocked
- ✅ `publish_response_complete()` is NOT called when output is blocked
- ✅ Security warning notification IS sent
- ✅ Response is removed from history
- ✅ Error is returned to caller

This regression test will catch if this bug is reintroduced in the future.

## Testing

### Manual Testing Steps

1. **Start mock servers:**
   ```bash
   # Terminal 1: Security check mock
   cd mocks/security_check_mock && bash run.sh

   # Terminal 2: Bad LLM mock
   cd mocks/llm-mock && python main_bad_llm.py
   ```

2. **Configure `.env`:**
   ```bash
   FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
   SECURITY_CHECK_API_URL=http://localhost:8089/check
   SECURITY_CHECK_API_KEY=test-key
   ```

3. **Configure `config/overrides/llmconfig.yml`:**
   ```yaml
   models:
     badllm:
       model_url: "http://localhost:8002/v1"
       model_name: "openai/test-llm"
       api_key: "test-key"
       compliance_level: "External"
   ```

4. **Restart backend:** `bash agent_start.sh -b`

5. **Test in UI:**
   - Select "badllm" model
   - Send first message: "Hi" (contains "gun" - WARNING)
   - Send second message: "Tell me more" (contains "bomb" - BLOCKED)

### Expected Results

**Before fix:**
- User sees assistant response with "bomb"
- Then sees "The system was unable to process your request"
- Chat export shows blocked content

**After fix:**
- User NEVER sees the blocked content
- User only sees "The system was unable to process your request"
- Chat export does NOT show blocked content

## Impact

**Affected Features:**
- Output security checks for all modes:
  - Plain mode (no tools/RAG)
  - RAG mode
  - Tools mode

**Security Impact:**
- High - Users were exposed to content that violated security policies
- Blocked content appeared in chat history and exports
- Users could screenshot or copy blocked content before it was removed

**User Experience Impact:**
- Medium - Users saw confusing behavior (content appearing then disappearing)
- Error message appeared AFTER seeing the content

## Lessons Learned

1. **Security checks must run before publishing to users** - Not after
2. **Mode runners should not publish directly** - Orchestrator controls when content is safe to show
3. **Regression tests for security features are critical** - This bug could have been caught earlier
4. **Manual testing revealed the issue** - Automated tests didn't catch the timing problem

## Related Files

- `backend/application/chat/modes/plain.py` - Fixed
- `backend/application/chat/modes/rag.py` - Fixed
- `backend/application/chat/modes/tools.py` - Fixed
- `backend/application/chat/orchestrator.py` - Fixed
- `backend/tests/test_orchestrator_security_integration.py` - Regression test added
- `docs/agent-implementation-summaries/SECURITY_CHECK_COMPLETE_IMPLEMENTATION.md` - Updated documentation

## Future Improvements

1. Add E2E test that verifies blocked content never appears in WebSocket messages
2. Add audit logging for all blocked content attempts
3. Consider adding rate limiting for repeated security violations
4. Add metrics/monitoring for security check effectiveness
