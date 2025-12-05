# Content Security Check

The content security check feature provides pre and post moderation capabilities for user input and LLM-generated output. This allows administrators to integrate external security APIs to validate content before and after processing.

## Overview

The security check feature consists of two independent checks:

1. **Input Security Check (Pre-check)**: Validates user input before sending it to the LLM
2. **Output Security Check (Post-check)**: Validates LLM-generated responses before showing them to users

Both checks are optional and can be enabled independently via feature flags.

## Configuration

### Feature Flags

Enable the security check features in your `.env` file:

```bash
# Enable pre-check security moderation for user input
FEATURE_SECURITY_CHECK_INPUT_ENABLED=true

# Enable post-check security moderation for LLM output
FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
```

### API Endpoint Configuration

Configure the external security check API:

```bash
# API endpoint URL for security checks (required if security checks are enabled)
SECURITY_CHECK_API_URL=https://security-check-api.example.com/check

# API key for authentication with security check endpoint
SECURITY_CHECK_API_KEY=your-api-key-here

# Timeout in seconds for security check API calls (default: 10)
SECURITY_CHECK_TIMEOUT=10
```

## API Contract

The security check API must accept POST requests with the following payload:

```json
{
  "content": "The user input or LLM output to check",
  "check_type": "input" | "output",
  "username": "user@example.com",
  "message_history": [
    {"role": "user", "content": "Previous user message"},
    {"role": "assistant", "content": "Previous assistant message"}
  ]
}
```

The API must respond with one of the following statuses:

### Response Format

```json
{
  "status": "blocked" | "allowed-with-warnings" | "good",
  "message": "Optional human-readable message explaining the result",
  "details": {
    "Optional": "Additional details about the check"
  }
}
```

### Status Values

- **`blocked`**: Content violates security policies and should be rejected
- **`allowed-with-warnings`**: Content has minor issues but is acceptable (warnings shown to user)
- **`good`**: Content passes all security checks

## Behavior

### Input Check (Pre-check)

When input security checking is enabled:

1. User submits a message
2. System performs security check on user input with message history context
3. If **blocked**:
   - Message is rejected
   - User sees error message explaining why
   - Message is removed from history
   - LLM is not called
4. If **allowed-with-warnings**:
   - Warning message is shown to user
   - Processing continues normally
5. If **good**:
   - Processing continues normally

### Output Check (Post-check)

When output security checking is enabled:

1. LLM generates a response
2. System performs security check on LLM output with message history context
3. If **blocked**:
   - Response is rejected
   - User sees error message explaining why
   - Response is removed from history
4. If **allowed-with-warnings**:
   - Warning message is shown to user
   - Response is delivered to user
5. If **good**:
   - Response is delivered normally

## Error Handling

The security check service is designed to fail open for reliability:

- If the security check API is unreachable, content is allowed by default
- If the API returns an invalid status, content is allowed by default
- If the API times out, content is allowed by default
- All errors are logged for monitoring

This ensures that temporary API issues do not block legitimate user interactions.

## User Experience

### Blocked Content

When content is blocked, users see a clear error message:

```
Input blocked: Your input was blocked by content security policy.
[Details provided by the security API]
```

or

```
Response blocked: The response was blocked by content security policy.
[Details provided by the security API]
```

### Warnings

When content has warnings, users see a notification but can proceed:

```
Warning: Your input triggered security warnings.
[Details provided by the security API]
```

## Implementation Details

### Architecture

The security check is implemented as a service layer (`SecurityCheckService`) that:

1. Integrates with the chat orchestrator
2. Calls external APIs via HTTP
3. Handles timeouts and errors gracefully
4. Provides structured responses

### Integration Points

- **Orchestrator**: Performs checks before and after mode execution
- **Event Publisher**: Notifies users of blocked content or warnings
- **Session Management**: Removes blocked messages from history

### Performance

- Security checks add latency to chat operations
- Default timeout is 10 seconds (configurable)
- Checks are performed sequentially (not in parallel)
- Message history is sent for context (consider size limits)

## Security Considerations

### API Authentication

- Always use HTTPS for the security check API
- Rotate API keys regularly
- Use least-privilege API keys

### Data Privacy

- Message history is sent to the security check API
- Ensure your security check API complies with data privacy regulations
- Consider data retention policies on the security check service

### Rate Limiting

- The security check API may implement rate limiting
- Configure appropriate timeouts to avoid blocking users
- Monitor for API errors and adjust configuration as needed

## Monitoring

Monitor the following metrics:

- Security check API response times
- Number of blocked inputs/outputs
- Number of warnings generated
- API error rates
- Timeout occurrences

Check application logs for security check events:

```
WARNING: User input blocked by security check for user@example.com: Offensive content detected
INFO: LLM output has warnings from security check for user@example.com: Potentially sensitive topics
```

## Testing

### Unit Tests

Run security check service tests:

```bash
pytest backend/tests/test_security_check.py -v
```

### Integration Tests

Run orchestrator integration tests:

```bash
pytest backend/tests/test_orchestrator_security_integration.py -v
```

### Manual Testing

1. Enable security checks in `.env`
2. Configure a test API endpoint
3. Send test inputs with various content
4. Verify blocking, warnings, and normal flow work correctly

## Troubleshooting

### Security Checks Not Working

1. Verify feature flags are enabled
2. Check API URL and key are configured correctly
3. Test API endpoint independently
4. Check application logs for errors

### All Content Being Allowed

This is expected behavior when:
- Feature flags are disabled
- API URL or key is not configured
- API is unreachable (fail-open design)

### Timeout Issues

If security checks frequently timeout:
1. Increase `SECURITY_CHECK_TIMEOUT`
2. Check API performance
3. Consider reducing message history size sent to API

## Example Implementation

### Sample Security Check API (Python/FastAPI)

```python
from fastapi import FastAPI, Header
from pydantic import BaseModel
from typing import List, Dict, Optional

app = FastAPI()

class SecurityCheckRequest(BaseModel):
    content: str
    check_type: str
    username: str
    message_history: List[Dict[str, str]]

class SecurityCheckResponse(BaseModel):
    status: str  # "blocked", "allowed-with-warnings", or "good"
    message: Optional[str] = None
    details: Dict = {}

@app.post("/check")
async def check_content(
    request: SecurityCheckRequest,
    authorization: str = Header(None)
):
    # Verify API key
    if not authorization or not authorization.startswith("Bearer "):
        return {"status": "blocked", "message": "Invalid authentication"}
    
    api_key = authorization[7:]  # Remove "Bearer " prefix
    if api_key != "your-secret-key":
        return {"status": "blocked", "message": "Invalid API key"}
    
    # Perform your security checks here
    content_lower = request.content.lower()
    
    # Example: Block offensive words
    offensive_words = ["badword1", "badword2"]
    if any(word in content_lower for word in offensive_words):
        return SecurityCheckResponse(
            status="blocked",
            message="Content contains offensive language",
            details={"reason": "profanity"}
        )
    
    # Example: Warn about sensitive topics
    sensitive_topics = ["password", "credit card"]
    if any(topic in content_lower for topic in sensitive_topics):
        return SecurityCheckResponse(
            status="allowed-with-warnings",
            message="Content may contain sensitive information",
            details={"topics": sensitive_topics}
        )
    
    # Content is good
    return SecurityCheckResponse(
        status="good"
    )
```

## API Contract Validation

The security check API must:

1. Accept POST requests
2. Require Bearer token authentication
3. Return JSON with `status`, optional `message`, and optional `details`
4. Respond within the configured timeout period
5. Handle errors gracefully
