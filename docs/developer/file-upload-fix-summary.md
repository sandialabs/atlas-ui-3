# File Upload Issue Fix - Technical Summary

## Problem
When users uploaded a file and immediately tried to interact with it via chat, the LLM could not see the file on the first attempt. On the second attempt ("try again"), the file was visible and the LLM responded correctly.

## Root Cause Analysis

### Architecture Before Fix
```
WebSocket Connection 1                WebSocket Connection 2
        ↓                                     ↓
   ChatService A                         ChatService B
        ↓                                     ↓
InMemorySessionRepository A    InMemorySessionRepository B
        ↓                                     ↓
   Session X (with files)              Session X (no files)
```

**Problem**: Each WebSocket connection created its own `ChatService` instance, and each `ChatService` created its own `InMemorySessionRepository`. When a file was attached via Connection 1, it was stored in Repository A. When a chat message was sent (even via the same connection), the session might be retrieved from Repository B, which didn't have the file.

### The Bug Flow
1. User uploads file → WebSocket Connection 1
2. `ChatService A` creates session X in `Repository A`
3. File is attached to session X in `Repository A`
4. User sends chat message → WebSocket Connection 1 (or Connection 2)
5. `ChatService` retrieves session from its own repository
6. **IF** the ChatService instance is different or uses a different repository, session X might not have the file
7. LLM doesn't see files manifest
8. On retry, session is eventually synced, and files become visible

## Solution

### Architecture After Fix
```
WebSocket Connection 1                WebSocket Connection 2
        ↓                                     ↓
   ChatService A                         ChatService B
        ↓                                     ↓
        └─────────────┬───────────────────────┘
                      ↓
         Shared InMemorySessionRepository
                      ↓
              Session X (with files)
```

**Solution**: Created a single shared `InMemorySessionRepository` in `AppFactory` that is passed to all `ChatService` instances via dependency injection.

### Code Changes

**1. app_factory.py**: Added shared session repository
```python
class AppFactory:
    def __init__(self) -> None:
        # ... existing code ...
        
        # NEW: Shared session repository for all ChatService instances
        self.session_repository = InMemorySessionRepository()
```

**2. app_factory.py**: Pass shared repository to ChatService
```python
def create_chat_service(self, connection: Optional[ChatConnectionProtocol] = None) -> ChatService:
    return ChatService(
        llm=self.llm_caller,
        tool_manager=self.mcp_tools,
        connection=connection,
        config_manager=self.config_manager,
        file_manager=self.file_manager,
        session_repository=self.session_repository,  # NEW: Pass shared repository
    )
```

## Impact

### Before Fix
- Files attached in one connection might not be visible in another
- Files might not be visible on first chat attempt
- Inconsistent behavior frustrating to users

### After Fix
- Files attached in any connection are immediately visible in all connections
- Files are always visible on first chat attempt
- Consistent, predictable behavior
- All existing tests continue to pass
- New regression tests prevent future issues

## Testing

### New Tests Added
1. `test_sessions_shared_across_chat_service_instances`: Verifies sessions are shared across ChatService instances
2. `test_session_repository_shared_across_app_factory_calls`: Verifies repository sharing at AppFactory level

### Existing Tests Verified
- All 40 session and file-related tests pass
- File attachment tests pass
- File library tests pass
- Session management tests pass

## Conclusion

This was a **dependency injection issue** where each service instance was creating its own isolated storage. The fix ensures proper separation of concerns while maintaining shared state where needed. This is a textbook example of why dependency injection and proper state management are critical in multi-connection applications.
