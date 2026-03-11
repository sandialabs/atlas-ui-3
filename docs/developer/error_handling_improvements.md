```markdown
# Error Handling Improvements

Last updated: 2026-03-10

## Problem
When backend errors occurred (especially rate limiting from services like Cerebras), users were left staring at a non-responsive UI with no indication of what went wrong. Errors were only visible in backend logs.

## Solution
Implemented comprehensive error classification and user-friendly error messaging system.

## Changes

### 1. New Error Types (`atlas/domain/errors.py`)
- `RateLimitError` - For rate limiting scenarios
- `LLMTimeoutError` - For timeout scenarios
- `LLMAuthenticationError` - For authentication failures
- `LLMServiceError` - For generic LLM service failures

### 2. Error Classification (`atlas/application/chat/utilities/error_utils.py`)
Added `classify_llm_error()` function that:
- Detects error type from exception class name or message content
- Returns appropriate domain error class
- Provides user-friendly message (shown in UI)
- Provides detailed log message (for debugging)

### 3. WebSocket Error Handling (`atlas/main.py`)
Enhanced error handling to:
- Catch specific error types (RateLimitError, LLMTimeoutError, etc.)
- Send user-friendly messages to frontend
- Include `error_type` field for frontend categorization
- Log full error details for debugging

### 4. Tests
- `atlas/tests/test_error_classification.py` - Unit tests for error classification
- `atlas/tests/test_error_flow_integration.py` - Integration tests
- `scripts/demo_error_handling.py` - Visual demonstration

## Example: Rate Limiting Error

### Before
```
User sends message → Rate limit hit → UI sits there thinking forever
Backend logs: "litellm.RateLimitError: CerebrasException - We're experiencing high traffic..."
User: *No idea what's happening*
```

### After
```
User sends message → Rate limit hit → Error displayed in chat
UI shows: "The AI service is experiencing high traffic. Please try again in a moment."
Backend logs: "Rate limit error: litellm.RateLimitError: CerebrasException - We're experiencing high traffic..."
User: Knows to wait and try again
```

## Error Messages

| Error Type | User Message | When It Happens |
|------------|--------------|-----------------|
| **RateLimitError** | "The AI service is experiencing high traffic. Please try again in a moment." | API rate limits exceeded |
| **LLMTimeoutError** | "The AI service request timed out. Please try again." | Request takes too long |
| **LLMAuthenticationError** | "There was an authentication issue with the AI service. Please contact your administrator." | Invalid API keys, auth failures |
| **LLMServiceError** | "The AI service encountered an error. Please try again or contact support if the issue persists." | Generic LLM service errors |

### 5. Domain Error Raising in LiteLLMCaller (2026-03-10)

Added `_raise_llm_domain_error()` static method to `LiteLLMCaller` that maps litellm exception types (e.g., `litellm.RateLimitError`, `litellm.Timeout`, `litellm.AuthenticationError`) directly to domain errors. This replaces all `raise Exception(...)` calls in `call_plain` and `call_with_tools` so errors propagate as typed domain errors through the WebSocket handler.

**When to use which:**
- `LiteLLMCaller._raise_llm_domain_error()` -- use in `litellm_caller.py` except blocks where you have the raw litellm exception and want to raise the domain error immediately. This uses `isinstance()` checks against litellm types for accurate classification.
- `classify_llm_error()` in `error_handler.py` -- use in higher-level orchestration code (e.g., `safe_call_llm_with_tools`) where you receive a generic `Exception` and need to classify it by error string heuristics. Returns a tuple of `(error_class, user_msg, log_msg)`.

### 6. AgentModeRunner Error Cleanup (2026-03-10)

`AgentModeRunner.run()` now wraps the agent loop execution in try/except. On failure, it sends an `agent_completion` event (best-effort) so the frontend clears agent UI state (step counter, thinking indicator) before the error message arrives via the WebSocket error handler.

### 7. Frontend Safety Timeout (2026-03-10)

`ChatContext.jsx` includes a 5-minute (`THINKING_TIMEOUT_MS`) safety timeout via `useEffect` + `useRef`. If `isThinking` stays true for 5 minutes without any backend response, the timeout fires and:
- Resets `isThinking` and `isSynthesizing` to false
- Resets `currentAgentStep` to 0
- Adds a system error message telling the user to retry

The timeout is cleared whenever `isThinking` becomes false (normal completion or error).

### 8. Auto-Retry for Transient LLM Errors (2026-03-10)

`LiteLLMCaller._acompletion_with_retry()` wraps litellm `acompletion` calls with automatic retry and exponential backoff. Transient errors (rate limit, timeout, 5xx server errors) are retried up to `MAX_LLM_RETRIES` (3) times. Auth errors and other non-transient errors raise immediately without retry.

- `_is_retryable_error()` classifies exceptions: rate limits, timeouts, and server errors (502/503/429) are retryable; auth errors are not
- Backoff: `RETRY_BASE_DELAY_SECONDS * 2^attempt + jitter` (1s, 2s, 4s base with random 0-0.5s jitter)
- Each retry attempt is logged at WARNING level for observability
- `call_with_rag` and `call_with_rag_and_tools` now re-raise LLM domain errors instead of masking them with RAG fallback retries
- Mock flaky LLM (`atlas/tests/mocks/mock_flaky_llm.py`) available for testing retry scenarios

## Security & Privacy
- Sensitive details (API keys, etc.) NOT exposed to users
- Full error details logged for admin debugging
- User messages are helpful but non-technical

## Testing
Run the demonstration:
```bash
python scripts/demo_error_handling.py
```

Run tests:
```bash
cd atlas
export PYTHONPATH=/path/to/atlas-ui-3
python -m pytest tests/test_error_classification.py -v
python -m pytest tests/test_error_flow_integration.py -v
```
```
