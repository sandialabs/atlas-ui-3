# Fix Username Override Bypass in Tool Approval Flow

**Date:** 2025-12-11
**Status:** Planning
**Priority:** HIGH - Security Issue

## Problem Summary

The username override security feature can be bypassed when users edit tool arguments during the approval flow. This allows a malicious user to impersonate another user by editing the `username` parameter in the tool approval dialog.

## Background

Atlas UI 3 implements a username override security feature (documented in `atlas/mcp/username-override-demo/README.md`) that automatically injects the authenticated user's email into tools that accept a `username` parameter. This prevents LLMs from impersonating other users.

**How the username override is supposed to work:**
1. Tool schema declares a `username` parameter
2. LLM generates a tool call (with or without a username argument)
3. Backend detects the tool accepts `username` via `tool_accepts_username()` (tool_utils.py:136)
4. Backend injects or overrides the `username` argument via `inject_context_into_args()` (tool_utils.py:412)
5. Tool receives the correct authenticated username

**Current implementation location:**
- Detection: `atlas/application/chat/utilities/tool_utils.py:136-162` (`tool_accepts_username`)
- Injection: `atlas/application/chat/utilities/tool_utils.py:412-469` (`inject_context_into_args`)

## Security Vulnerability

### Vulnerability Flow

When tool approval with argument editing is enabled:

1. **Initial injection (SECURE):** `execute_single_tool` calls `prepare_tool_arguments` (line 182) which correctly injects username via `inject_context_into_args` (line 409)

2. **Arguments filtered (SECURE):** `_filter_args_to_schema` (line 185) ensures only schema-declared parameters are kept

3. **Approval request sent:** Frontend receives sanitized arguments including the injected username

4. **User edits arguments (INSECURE):** Frontend `ToolApprovalDialog.jsx` allows editing ALL arguments (lines 78-105), including `username`

5. **Backend receives edited args (INSECURE):** When user approves with edits, backend directly uses the edited arguments (tool_utils.py:246-256):
   ```python
   if allow_edit and response.get("arguments"):
       edited_args = response["arguments"]
       if json.dumps(edited_args, sort_keys=True) != json.dumps(original_display_args, sort_keys=True):
           arguments_were_edited = True
           filtered_args = edited_args  # <-- VULNERABILITY: No re-injection!
   ```

6. **No re-injection:** The edited arguments are used directly WITHOUT re-applying `inject_context_into_args`, bypassing the username override

7. **Tool executes with wrong user:** Tool receives the user-edited username instead of the authenticated user's email

### Attack Scenario

```
1. User alice@example.com is authenticated
2. LLM calls tool: create_user_record(username="alice@example.com", data="test")
3. Backend injects username="alice@example.com" (correct)
4. Approval dialog shows: {"username": "alice@example.com", "data": "test"}
5. Alice edits username to "admin@example.com" in the approval dialog
6. Alice clicks "Approve"
7. Backend receives edited args: {"username": "admin@example.com", "data": "test"}
8. Backend uses edited args WITHOUT re-injection
9. Tool executes with username="admin@example.com" (SECURITY BREACH)
10. Alice successfully impersonated admin@example.com
```

### Impact

- **User Impersonation:** Users can impersonate other users including administrators
- **Unauthorized Actions:** Users can perform actions on behalf of other users
- **Audit Trail Corruption:** Actions are incorrectly attributed to the wrong user
- **Authorization Bypass:** Permission checks may be performed for the wrong user

## Root Cause

The `execute_single_tool` function in `atlas/application/chat/utilities/tool_utils.py` does not re-apply security injections after receiving edited arguments from the user approval flow.

**Problematic code location:** tool_utils.py:246-256

## Solution

### Core Fix

After receiving edited arguments from the user, re-apply security injections to ensure critical parameters like `username` cannot be tampered with.

**Implementation location:** `atlas/application/chat/utilities/tool_utils.py:246-256`

### Code Changes Required

**File:** `atlas/application/chat/utilities/tool_utils.py`

**Current code (lines 246-256):**
```python
if allow_edit and response.get("arguments"):
    edited_args = response["arguments"]
    if json.dumps(edited_args, sort_keys=True) != json.dumps(original_display_args, sort_keys=True):
        arguments_were_edited = True
        filtered_args = edited_args
        logger.info(f"User edited arguments for tool {tool_call.function.name}")
    else:
        logger.debug(f"Arguments returned unchanged for tool {tool_call.function.name}")
```

**Fixed code:**
```python
if allow_edit and response.get("arguments"):
    edited_args = response["arguments"]
    if json.dumps(edited_args, sort_keys=True) != json.dumps(original_display_args, sort_keys=True):
        arguments_were_edited = True
        logger.info(f"User edited arguments for tool {tool_call.function.name}")

        # SECURITY: Re-apply security injections after user edits
        # This ensures username and other security-critical parameters cannot be tampered with
        re_injected_args = inject_context_into_args(
            edited_args,
            session_context,
            tool_call.function.name,
            tool_manager
        )

        # Re-filter to schema to ensure only valid parameters
        filtered_args = _filter_args_to_schema(
            re_injected_args,
            tool_call.function.name,
            tool_manager
        )
    else:
        logger.debug(f"Arguments returned unchanged for tool {tool_call.function.name}")
```

### Additional Considerations

**1. Frontend UI Enhancement (Optional but Recommended)**

Mark security-injected parameters as read-only in the approval dialog to provide clear UX feedback.

**File:** `frontend/src/components/ToolApprovalDialog.jsx`

Add visual indicator for injected parameters (lines 78-105):
```jsx
{Object.entries(editedArgs).map(([key, value]) => {
  const isSecurityParam = key === 'username' || key.startsWith('original_') || key === 'file_url' || key === 'file_urls';
  return (
    <div key={key} className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      <label className="block text-sm font-medium text-gray-300 mb-1">
        {key}
        {isSecurityParam && (
          <span className="ml-2 text-xs text-yellow-400">(auto-injected by system)</span>
        )}
      </label>
      <textarea
        value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
        onChange={(e) => { /* ... */ }}
        disabled={isSecurityParam}
        className={`w-full bg-gray-800 text-gray-200 border border-gray-600 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 ${isSecurityParam ? 'opacity-50 cursor-not-allowed' : ''}`}
        rows={typeof value === 'object' ? Math.min(10, JSON.stringify(value, null, 2).split('\n').length) : 3}
      />
    </div>
  );
})}
```

**2. Add Warning in Edit Note to LLM**

Update the edit note sent to the LLM to clarify that security parameters were re-injected.

**File:** `atlas/application/chat/utilities/tool_utils.py` (lines 289-300)

```python
if arguments_were_edited:
    edit_note = (
        f"[IMPORTANT: The user manually edited the tool arguments before execution. "
        f"Security-critical parameters (like username) were re-injected by the system and cannot be modified. "
        f"The ACTUAL arguments executed were: {json.dumps(filtered_args)}. "
        f"Your response must reflect these arguments as the user's true intent.]\\n\\n"
    )
```

## Testing Strategy

### Unit Tests

**File:** `atlas/tests/test_tool_approval_utils.py` or new file `atlas/tests/test_username_override_in_approval.py`

```python
import pytest
from application.chat.utilities.tool_utils import inject_context_into_args, _filter_args_to_schema
from unittest.mock import Mock

def test_username_override_after_user_edit():
    """Test that username is re-injected even after user edits it during approval."""
    # Setup
    session_context = {
        "user_email": "alice@example.com",
        "files": {}
    }

    # Simulate user editing username to a different value
    user_edited_args = {
        "username": "malicious@example.com",  # User tried to change this
        "data": "test data"
    }

    # Mock tool manager that says tool accepts username
    mock_tool_manager = Mock()
    mock_tool_manager.get_tools_schema.return_value = [{
        "function": {
            "name": "create_record",
            "parameters": {
                "properties": {
                    "username": {"type": "string"},
                    "data": {"type": "string"}
                }
            }
        }
    }]

    # Re-inject context (simulating what should happen after user approval)
    re_injected = inject_context_into_args(
        user_edited_args,
        session_context,
        "create_record",
        mock_tool_manager
    )

    # Verify username was overridden back to authenticated user
    assert re_injected["username"] == "alice@example.com"
    assert re_injected["data"] == "test data"

def test_username_override_with_tool_that_doesnt_accept_username():
    """Test that username is not injected for tools that don't accept it."""
    session_context = {
        "user_email": "alice@example.com",
        "files": {}
    }

    user_edited_args = {
        "query": "test query"
    }

    # Mock tool manager that says tool does NOT accept username
    mock_tool_manager = Mock()
    mock_tool_manager.get_tools_schema.return_value = [{
        "function": {
            "name": "search",
            "parameters": {
                "properties": {
                    "query": {"type": "string"}
                }
            }
        }
    }]

    # Inject context
    re_injected = inject_context_into_args(
        user_edited_args,
        session_context,
        "search",
        mock_tool_manager
    )

    # Verify username was NOT injected
    assert "username" not in re_injected
    assert re_injected["query"] == "test query"
```

### Integration Test

**File:** `atlas/tests/test_tool_approval_integration.py`

Test the full flow:
1. Create mock approval request
2. Simulate user editing username in approval dialog
3. Verify tool executes with correct username (authenticated user)

### Manual Testing

1. Start Atlas UI with `username-override-demo` MCP server enabled
2. Enable tool approval for the demo tools
3. Test scenarios:
   - **Scenario 1:** LLM calls `get_user_info` with different username, user approves without editing - verify correct username used
   - **Scenario 2:** LLM calls `create_user_record`, user edits username during approval - verify edited username is overridden back to authenticated user
   - **Scenario 3:** LLM calls `check_user_permissions` with admin username, user edits other args but not username - verify everything works
   - **Scenario 4:** LLM calls a tool that doesn't accept username, user edits args - verify no username injection occurs

## Implementation Steps

1. **Make core security fix** (REQUIRED)
   - Modify `atlas/application/chat/utilities/tool_utils.py` lines 246-256
   - Re-apply `inject_context_into_args` after receiving edited arguments
   - Re-apply `_filter_args_to_schema` to ensure only valid parameters

2. **Add unit tests** (REQUIRED)
   - Create tests for username re-injection after user edits
   - Create tests for tools that don't accept username
   - Ensure existing tests in `test_capability_tokens_and_injection.py` still pass

3. **Update LLM edit note** (RECOMMENDED)
   - Clarify in the edit note that security parameters were re-injected

4. **Frontend UI enhancement** (OPTIONAL)
   - Mark security-injected parameters as read-only or with visual indicator
   - Prevents user confusion about which parameters they can actually change

5. **Update documentation** (REQUIRED)
   - Update `atlas/mcp/username-override-demo/README.md` to document that the override applies even during approval edits
   - Update `docs/admin/tool-approval.md` to explain security parameter re-injection

6. **Run full test suite** (REQUIRED)
   - `./test/run_tests.sh all`
   - Verify all tests pass

## Files to Modify

### Required Changes

1. `atlas/application/chat/utilities/tool_utils.py` (lines 246-256)
   - Re-apply security injections after user edits

2. `atlas/tests/test_username_override_in_approval.py` (NEW FILE)
   - Add unit tests for username override in approval flow

3. `atlas/mcp/username-override-demo/README.md`
   - Document that override applies during approval edits

### Optional Changes

4. `frontend/src/components/ToolApprovalDialog.jsx` (lines 78-105)
   - Add visual indicator for security-injected parameters

5. `atlas/application/chat/utilities/tool_utils.py` (lines 289-300)
   - Update LLM edit note to mention security parameter re-injection

## Security Considerations

1. **Defense in Depth:** This fix ensures security parameters cannot be tampered with at any stage of the approval flow

2. **Transparency:** The LLM edit note and optional frontend UI changes make it clear to users which parameters are security-controlled

3. **Backward Compatibility:** The fix does not break existing functionality - tools that don't accept username are unaffected

4. **Future-Proof:** This pattern can be extended to other security-critical parameters if needed (e.g., session_id, permissions)

## Rollout Plan

1. **Implement and test locally**
2. **Review with security team** (if applicable)
3. **Merge to main branch**
4. **Deploy to staging environment**
5. **Perform manual security testing**
6. **Deploy to production**
7. **Monitor logs for any unexpected behavior**

## Success Criteria

- [ ] Core fix implemented and tested
- [ ] Unit tests added and passing
- [ ] Full test suite passes (`./test/run_tests.sh all`)
- [ ] Manual testing confirms username override cannot be bypassed
- [ ] Documentation updated
- [ ] Code reviewed and approved
- [ ] Changes merged to main branch

## References

- Username Override Demo: `atlas/mcp/username-override-demo/`
- Tool Approval System: `docs/admin/tool-approval.md`
- Existing Tests: `atlas/tests/test_capability_tokens_and_injection.py:155-184`
- Tool Utils Implementation: `atlas/application/chat/utilities/tool_utils.py`
