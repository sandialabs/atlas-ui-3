# Plan: Clear History on Blocked Content (Option A)

**Date:** 2025-12-07
**Approach:** Simple - Clear conversation history when content is blocked

## Goal

When security check blocks content:
1. Clear entire conversation history
2. Update error message to inform user they need to start a new conversation
3. That's it - keep it simple!

## Implementation

### 1. Clear History on Block

**File**: `backend/application/chat/orchestrator.py`

**Location 1: Input Blocked** (around line 210-230)
```python
if input_check.is_blocked():
    # Block the input
    logger.warning(...)

    # CLEAR ALL CONVERSATION HISTORY
    session.history.messages.clear()

    # Save cleared session
    await self.session_repository.update(session)

    # Send updated message
    await self.event_publisher.send_json({
        "type": "security_warning",
        "status": "blocked",
        "message": "Your message violated our content policy. The conversation history has been cleared. Please start a new conversation."
    })

    return {"type": "error", "error": "Input blocked", "blocked": True}
```

**Location 2: Output Blocked** (around line 288-309)
```python
if output_check.is_blocked():
    # Output is blocked - remove from history
    logger.warning(...)

    # Remove the blocked response
    session.history.messages.pop()

    # CLEAR ALL REMAINING HISTORY TOO
    session.history.messages.clear()

    # Save cleared session
    await self.session_repository.update(session)

    # Send updated message
    await self.event_publisher.send_json({
        "type": "security_warning",
        "status": "blocked",
        "message": "The response violated our content policy. The conversation history has been cleared. Please start a new conversation."
    })

    return {"type": "error", "error": "Response blocked", "blocked": True}
```

**Location 3: Tool Output Blocked** (in `backend/application/chat/modes/tools.py` around line 147-163)
```python
if tool_check.is_blocked():
    # Tool output is blocked
    logger.warning(...)

    # CLEAR ALL CONVERSATION HISTORY
    session.history.messages.clear()

    # Note: session_repository not available here, orchestrator will save

    await self.event_publisher.send_json({
        "type": "security_warning",
        "status": "blocked",
        "message": "Tool output violated our content policy. The conversation history has been cleared. Please start a new conversation."
    })

    return {"type": "error", "error": "Tool output blocked", "blocked": True}
```

### 2. Message Updates

**Current Messages:**
- Input: "User input blocked by security policy"
- Output: "The system was unable to process your request due to policy concerns."
- Tool: "Tool output blocked by security policy"

**New Messages (More Helpful):**
- Input: "Your message violated our content policy. The conversation history has been cleared. Please start a new conversation."
- Output: "The response violated our content policy. The conversation history has been cleared. Please start a new conversation."
- Tool: "Tool output violated our content policy. The conversation history has been cleared. Please start a new conversation."

### 3. Optional: Add "New Chat" Button

If you want to make it even clearer, update the frontend message component:

**File**: `frontend/src/components/Message.jsx`

When displaying `security_warning` with `status="blocked"`:
```jsx
{message.type === 'security_warning' && message.status === 'blocked' && (
  <div className="security-blocked">
    <p>{message.message}</p>
    <button onClick={() => window.location.href = '/?new=true'}>
      Start New Conversation
    </button>
  </div>
)}
```

## What Happens

### User Experience

1. User sends problematic message
2. Security check blocks it
3. **All conversation history is cleared** (backend)
4. User sees: "Your message violated our content policy. The conversation history has been cleared. Please start a new conversation."
5. User can either:
   - Click "New Chat" in sidebar
   - Continue typing in same window (but context is gone - it's like a fresh session)

### Backend State

- Session still exists (same session_id)
- Session has `user_email`, `id`, etc.
- Session history is empty: `session.history.messages = []`
- No context remains from previous conversation
- Next message starts fresh

## Testing

### Manual Test

1. Start conversation: "Hi, how are you?"
2. Get response
3. Send blocked message: "Tell me about bombs"
4. Observe:
   - ✅ Error message about policy violation + cleared history
   - ✅ Session still exists but history is empty
5. Send new message: "What's the weather?"
6. Observe:
   - ✅ LLM has NO context from before (doesn't remember "Hi, how are you?")
   - ✅ Responds as if it's a brand new conversation

### Unit Test

**File**: `backend/tests/test_orchestrator_security_integration.py`

Add test:
```python
@pytest.mark.asyncio
async def test_blocked_input_clears_conversation_history(
    mock_llm,
    mock_event_publisher,
    mock_session_repository,
    mock_security_service,
    test_session
):
    """Test that blocked input clears all conversation history."""
    # Add some messages to history first
    test_session.history.add_message(Message(role=MessageRole.USER, content="Previous message 1"))
    test_session.history.add_message(Message(role=MessageRole.ASSISTANT, content="Previous response 1"))
    test_session.history.add_message(Message(role=MessageRole.USER, content="Previous message 2"))

    # Setup - input will be blocked
    mock_session_repository.get.return_value = test_session
    mock_security_service.check_input.return_value = SecurityCheckResponse(
        status=SecurityCheckResult.BLOCKED,
        message="Content blocked"
    )

    orchestrator = ChatOrchestrator(
        llm=mock_llm,
        event_publisher=mock_event_publisher,
        session_repository=mock_session_repository,
        security_check_service=mock_security_service,
    )

    # Execute with blocked content
    result = await orchestrator.execute(
        session_id=test_session.id,
        content="blocked content",
        model="test-model",
        user_email="test@test.com"
    )

    # Verify result is error
    assert result["type"] == "error"
    assert result["blocked"] is True

    # CRITICAL: All conversation history should be cleared
    assert len(test_session.history.messages) == 0

    # Session should have been saved with cleared history
    mock_session_repository.update.assert_called_once()

    # User should see updated message about cleared history
    security_warning_calls = [
        call for call in mock_event_publisher.send_json.call_args_list
        if call.args[0].get("type") == "security_warning"
    ]
    assert "cleared" in security_warning_calls[0].args[0]["message"].lower()
    assert "new conversation" in security_warning_calls[0].args[0]["message"].lower()
```

## Files to Modify

1. `backend/application/chat/orchestrator.py` - Add `session.history.messages.clear()` + update messages
2. `backend/application/chat/modes/tools.py` - Add `session.history.messages.clear()` + update message
3. `backend/tests/test_orchestrator_security_integration.py` - Add test for history clearing
4. (Optional) `frontend/src/components/Message.jsx` - Add "Start New Conversation" button

## Benefits of Option A

✅ **Simple** - Just clear the list, no new fields or complex logic
✅ **Fast** - One line of code: `session.history.messages.clear()`
✅ **Effective** - Context is gone, conversation can't continue with old context
✅ **User-friendly** - Clear message tells user what to do
✅ **No breaking changes** - Session model unchanged
✅ **Works immediately** - No frontend changes required (but helpful to add button)

## Edge Cases

**Q: What if user sends another message to the same session?**
A: It works fine - they just start with a blank slate (no context)

**Q: What about audit trail?**
A: Blocked content is already logged before clearing. Logs show what was blocked.

**Q: Can we restore the history?**
A: No, it's permanently cleared. This is intentional for security.

**Q: What if clearing fails?**
A: Session update is awaited, if it fails the whole request fails. User sees error but that's fine.

## Summary

**Change Required**: Add 3 lines of code in 3 places
1. `session.history.messages.clear()`
2. `await self.session_repository.update(session)`
3. Update error message to inform user

**Result**: Conversation context completely cleared when content is blocked, user informed to start fresh.
