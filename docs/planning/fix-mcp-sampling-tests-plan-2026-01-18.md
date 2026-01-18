# Fix Plan: MCP Sampling Test Failures (2026-01-18)

## Problem Summary

Multiple backend tests are failing due to a mismatch between the test code and the actual implementation. The root cause is that the MCP sampling implementation was removed from `backend/modules/mcp_tools/client.py` in commit `a502798` but the related tests, documentation, and demo server were left in place.

### Current State

**What exists:**
- Tests: `backend/tests/test_sampling_integration.py` (tries to import `_SamplingRoutingContext`)
- Demo server: `backend/mcp/sampling_demo/`
- Documentation: `docs/developer/sampling.md`, updates in `docs/admin/mcp-servers.md`
- Implementation summary: `IMPLEMENTATION_SUMMARY_SAMPLING.md`
- Manual test: `backend/tests/manual_test_sampling.py`

**What's missing:**
- The actual implementation in `backend/modules/mcp_tools/client.py`:
  - `_SamplingRoutingContext` class
  - `_SAMPLING_ROUTING` dictionary
  - `_create_sampling_handler()` method
  - `_use_sampling_context()` context manager
  - `sampling_handler` parameter in Client initialization

### Git History

```
a502798 - working on sampling (REMOVED 218 lines from client.py)
920f42b - Add implementation summary for MCP sampling feature
12d7bac - Add comprehensive documentation for MCP sampling support
db56da2 - Fix sampling handler to return proper CreateMessageResult
4caa51d - Fix sampling handler to use first available model as fallback
80cdc43 - Add MCP sampling support - backend infrastructure
```

The sampling code was added in commits `80cdc43`, `4caa51d`, and `db56da2`, then completely removed in `a502798`.

## Root Cause Analysis

The test failures are NOT due to mock assertion issues (as initially suggested). The actual problems are:

1. **Import Error**: `test_sampling_integration.py` cannot import `_SamplingRoutingContext` because it doesn't exist
2. **Missing Implementation**: The sampling handler code was removed but artifacts (tests, docs) remain
3. **Incomplete Removal**: If sampling was meant to be removed, the cleanup was incomplete

## Decision Point

We need to determine whether to:

**Option A: Restore the sampling implementation** (if it was accidentally removed)
- Pros: Feature is complete with tests and documentation
- Cons: Need to understand why it was removed; may have had issues

**Option B: Remove all sampling artifacts** (if removal was intentional)
- Pros: Clean codebase with no broken tests
- Cons: Loss of feature work; need to update CHANGELOG

## Recommended Solution: Option A - Restore Implementation

Based on the PR context and the comprehensive documentation/tests, it appears the removal was likely accidental or part of debugging. The implementation should be restored.

## Implementation Steps

### Step 1: Restore Sampling Implementation
**File:** `backend/modules/mcp_tools/client.py`

**Location:** After `_ELICITATION_ROUTING` definition (around line 48)

**Add:**
```python
# Dictionary-based routing for sampling requests (similar to elicitation)
# Key: server_name, Value: _SamplingRoutingContext
_SAMPLING_ROUTING: Dict[str, "_SamplingRoutingContext"] = {}


class _SamplingRoutingContext:
    """Context for routing sampling requests to the correct tool execution."""
    def __init__(
        self,
        server_name: str,
        tool_call: ToolCall,
        update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ):
        self.server_name = server_name
        self.tool_call = tool_call
        self.update_cb = update_cb
```

**Location:** After `_create_elicitation_handler()` method (around line 339)

**Add:**
```python
@asynccontextmanager
async def _use_sampling_context(
    self,
    server_name: str,
    tool_call: ToolCall,
    update_cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
) -> AsyncIterator[None]:
    """
    Set up sampling routing for a tool call.
    Uses dictionary-based routing (not contextvars) because MCP receive loop runs in a different task.
    """
    routing = _SamplingRoutingContext(server_name, tool_call, update_cb)
    _SAMPLING_ROUTING[server_name] = routing
    try:
        yield
    finally:
        _SAMPLING_ROUTING.pop(server_name, None)


def _create_sampling_handler(self, server_name: str):
    """
    Create a sampling handler for a specific MCP server.

    This handler intercepts MCP sampling requests and routes them to the LLM.
    Returns a handler function that captures the server_name for routing.
    """
    async def handler(messages, params, context):
        """Per-server sampling handler with captured server_name."""
        from mcp.types import SamplingMessage, TextContent, CreateMessageResult

        routing = _SAMPLING_ROUTING.get(server_name)
        if routing is None:
            logger.warning(
                f"Sampling request for server '{server_name}' but no routing context - "
                f"sampling cancelled."
            )
            raise Exception("No routing context for sampling request")

        try:
            # Convert MCP SamplingMessage objects to plain dicts
            message_dicts = []
            for msg in messages:
                if isinstance(msg, SamplingMessage):
                    # Extract text from content
                    text = ""
                    if isinstance(msg.content, TextContent):
                        text = msg.content.text
                    elif isinstance(msg.content, list):
                        for item in msg.content:
                            if isinstance(item, TextContent):
                                text += item.text
                    else:
                        text = str(msg.content)
                    message_dicts.append({
                        "role": msg.role,
                        "content": text
                    })
                elif isinstance(msg, str):
                    message_dicts.append({
                        "role": "user",
                        "content": msg
                    })
                else:
                    message_dicts.append(msg)

            # Extract sampling parameters
            system_prompt = getattr(params, 'systemPrompt', None) if params else None
            temperature = getattr(params, 'temperature', None) if params else None
            max_tokens = getattr(params, 'maxTokens', 512) if params else 512
            model_preferences_raw = getattr(params, 'modelPreferences', None) if params else None

            # Normalize model_preferences to list
            model_preferences = None
            if model_preferences_raw:
                if isinstance(model_preferences_raw, str):
                    model_preferences = [model_preferences_raw]
                elif isinstance(model_preferences_raw, list):
                    model_preferences = model_preferences_raw

            # Add system prompt to messages if provided
            if system_prompt:
                message_dicts.insert(0, {
                    "role": "system",
                    "content": system_prompt
                })

            logger.info(
                f"Sampling request from server '{server_name}' tool '{routing.tool_call.name}': "
                f"{len(message_dicts)} messages, temperature={temperature}, max_tokens={max_tokens}"
            )

            # Call the LLM directly using LiteLLM
            from modules.llm.litellm_caller import LiteLLMCaller
            from modules.config import config_manager

            llm_caller = LiteLLMCaller()

            # Determine which model to use based on preferences or default
            llm_config = config_manager.llm_config
            model_name = None

            if model_preferences:
                # Try to find a matching model from preferences
                for pref in model_preferences:
                    # Check if preference matches any configured model
                    if pref in llm_config.models:
                        model_name = pref
                        break
                    # Check if preference matches model_name field
                    for name, model_config in llm_config.models.items():
                        if model_config.model_name == pref:
                            model_name = name
                            break
                    if model_name:
                        break

            # Fall back to first available model if no preference matched
            if not model_name:
                model_name = next(iter(llm_config.models.keys()))

            logger.debug(
                f"Using model '{model_name}' for sampling "
                f"(preferences: {model_preferences})"
            )

            # Call the LLM
            response = await llm_caller.call_plain(
                model_name=model_name,
                messages=message_dicts,
                temperature=temperature,
                max_tokens=max_tokens
            )

            logger.info(
                f"Sampling completed for server '{server_name}': "
                f"response_length={len(response) if response else 0}"
            )

            # Return a CreateMessageResult that FastMCP expects
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text=response),
                model=model_name
            )

        except Exception as e:
            logger.error(f"Error handling sampling for server '{server_name}': {e}", exc_info=True)
            raise

    return handler
```

### Step 2: Update Client Initialization to Pass sampling_handler

**Locations to update in `_initialize_single_client()` method:**

1. **HTTP/SSE clients** (lines ~502, ~506):
```python
# Before:
client = Client(url, auth=token, log_handler=log_handler, elicitation_handler=self._create_elicitation_handler(server_name))

# After:
client = Client(
    url,
    auth=token,
    log_handler=log_handler,
    elicitation_handler=self._create_elicitation_handler(server_name),
    sampling_handler=self._create_sampling_handler(server_name)
)
```

2. **STDIO clients with cwd** (line ~557):
```python
# Before:
client = Client(transport, log_handler=log_handler, elicitation_handler=self._create_elicitation_handler(server_name))

# After:
client = Client(
    transport,
    log_handler=log_handler,
    elicitation_handler=self._create_elicitation_handler(server_name),
    sampling_handler=self._create_sampling_handler(server_name)
)
```

3. **STDIO clients without cwd** (line ~567):
```python
# Same change as above
```

4. **Legacy STDIO clients** (line ~577):
```python
# Same change as above
```

### Step 3: Update execute_tool() Method

**Location:** Around line 1560 in `execute_tool()` method

**Restore the sampling context:**
```python
# Before:
if update_cb is not None:
    async with self._use_log_callback(_tool_log_callback):
        async with self._use_elicitation_context(server_name, tool_call, update_cb):
            raw_result = await self.call_tool(...)
else:
    async with self._use_elicitation_context(server_name, tool_call, update_cb):
        raw_result = await self.call_tool(...)

# After:
if update_cb is not None:
    async with self._use_log_callback(_tool_log_callback):
        async with self._use_elicitation_context(server_name, tool_call, update_cb):
            async with self._use_sampling_context(server_name, tool_call, update_cb):
                raw_result = await self.call_tool(...)
else:
    async with self._use_elicitation_context(server_name, tool_call, update_cb):
        async with self._use_sampling_context(server_name, tool_call, update_cb):
            raw_result = await self.call_tool(...)
```

### Step 4: Verify LiteLLM Caller Supports Required Parameters

**File:** `backend/modules/llm/litellm_caller.py`

Ensure `call_plain()` method accepts `temperature` and `max_tokens` parameters. According to the implementation summary, this was already done. Verify it's still present.

### Step 5: Run Tests

```bash
# Run sampling tests specifically
python -m pytest backend/tests/test_sampling_integration.py -v

# Run all backend tests
./test/run_tests.sh backend

# Run manual test if needed
python -m pytest backend/tests/manual_test_sampling.py -v
```

### Step 6: Update CHANGELOG

**File:** `CHANGELOG.md`

Add entry for this PR:
```markdown
### PR #XXX - 2026-01-18
- Fix: Restored MCP sampling implementation that was accidentally removed
- Fix: All backend tests now passing with sampling support
```

## Alternative Solution: Option B - Remove Sampling Artifacts

If the decision is to remove sampling (not recommended based on evidence):

### Files to Remove:
1. `backend/tests/test_sampling_integration.py`
2. `backend/tests/manual_test_sampling.py`
3. `backend/mcp/sampling_demo/` (entire directory)
4. `docs/developer/sampling.md`
5. `IMPLEMENTATION_SUMMARY_SAMPLING.md`

### Files to Update:
1. `docs/admin/mcp-servers.md` - Remove sampling references
2. `config/overrides/mcp.json` - Remove sampling_demo server entry
3. `CHANGELOG.md` - Add entry explaining removal

### Revert commits (optional):
```bash
git revert db56da2 4caa51d 80cdc43
```

## Testing Strategy

### Unit Tests
- `test_sampling_integration.py` should pass all 4 tests
- No other test should be affected

### Integration Tests
- Manual test with `manual_test_sampling.py`
- Test with actual sampling_demo server if available

### Regression Testing
- Run full backend test suite
- Verify no new failures introduced
- Check that existing MCP tests still pass

## Success Criteria

- [ ] All backend tests passing (465 tests)
- [ ] No import errors for `_SamplingRoutingContext`
- [ ] `test_sampling_integration.py` passes all 4 tests
- [ ] No regressions in existing MCP functionality
- [ ] CHANGELOG updated
- [ ] Documentation matches implementation

## Risk Assessment

**Low Risk** - The implementation was previously working and tested. Restoration is straightforward.

**Potential Issues:**
1. If sampling was removed for a reason (e.g., FastMCP API change), that reason still exists
2. Dependencies might have changed (check FastMCP version compatibility)
3. Other code may have been refactored that conflicts with sampling

**Mitigation:**
- Carefully review git diff to ensure all changes are intentional
- Run comprehensive test suite before considering complete
- Test with actual MCP server that uses sampling if available

## Timeline Estimate

- Step 1 (Restore implementation): 15-20 minutes
- Step 2 (Update Client init): 10 minutes
- Step 3 (Update execute_tool): 5 minutes
- Step 4 (Verify LiteLLM): 5 minutes
- Step 5 (Run tests): 5 minutes
- Step 6 (Update CHANGELOG): 2 minutes

**Total: ~45-50 minutes**

## Notes

- The original implementation was well-documented and tested
- The removal in commit `a502798` appears to be accidental or experimental
- No indication in commit message why removal was done
- All infrastructure (tests, docs, demo) suggests feature should exist
- Recommend reaching out to commit author for clarification if possible
