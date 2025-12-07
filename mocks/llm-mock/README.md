# LLM Mock Servers

This directory contains mock LLM servers for testing different scenarios with Atlas UI.

## Files

### Shared Models

- **models.py** - Shared Pydantic models used by all LLM mocks to reduce code duplication
  - `ChatMessage` - Individual chat message
  - `ChatCompletionRequest` - OpenAI-compatible request format
  - `ChatCompletionChoice` - Response choice model
  - `ChatCompletionUsage` - Token usage statistics
  - `ChatCompletionResponse` - OpenAI-compatible response format

### Mock Servers

1. **main.py** - Standard Mock LLM Server
   - Port: 8001
   - Purpose: Basic testing with simple, predictable responses
   - Features:
     - Keyword-based response generation
     - OpenAI-compatible API
     - No rate limiting or errors
   - Usage: `python main.py`

2. **main_rate_limit.py** - Rate Limited Mock LLM Server
   - Port: 8002
   - Purpose: Testing reliability, error handling, and rate limiting
   - Features:
     - Rate limiting (5 requests/minute)
     - Random error simulation (10% failure rate)
     - Random network delays
     - Comprehensive logging
   - Environment Variables:
     - `MOCK_LLM_DETERMINISTIC=1` - Disable random errors and delays
   - Usage: `python main_rate_limit.py`

3. **main_bad_llm.py** - Security Testing Mock LLM Server
   - Port: 8002
   - Purpose: Testing output security checks
   - Features:
     - Intentionally generates problematic content
     - Responses contain "bomb" or "gun" keywords
     - Helps test security filtering
   - Response Pattern:
     - Message 1, 4, 7... → Contains "gun" (should trigger WARNING)
     - Message 2, 5, 8... → Contains both "bomb" and "gun" (should BLOCK)
     - Message 3, 6, 9... → Contains "bomb" (should BLOCK)
   - Usage: `python main_bad_llm.py`

## Configuration

All mock servers expose OpenAI-compatible endpoints:
- `POST /v1/chat/completions` - Chat completions
- `GET /v1/models` - List available models
- `GET /health` - Health check
- `GET /` - Server info and documentation

## Testing with Atlas UI

### Basic Testing (main.py)

Edit `config/overrides/llmconfig.yml`:
```yaml
providers:
  openai:
    api_base: "http://localhost:8001/v1"
    api_key: "test-key"

default_provider: "openai"
default_model: "gpt-3.5-turbo"
```

### Rate Limit Testing (main_rate_limit.py)

Edit `config/overrides/llmconfig.yml`:
```yaml
providers:
  openai:
    api_base: "http://localhost:8002/v1"
    api_key: "test-key"

default_provider: "openai"
default_model: "gpt-3.5-turbo"
```

### Security Testing (main_bad_llm.py)

**Prerequisites:**
1. Start mock security server: `cd mocks/security_check_mock && bash run.sh`

2. Enable output security checks in `.env`:
   ```bash
   FEATURE_SECURITY_CHECK_OUTPUT_ENABLED=true
   SECURITY_CHECK_API_URL=http://localhost:8089/check
   SECURITY_CHECK_API_KEY=test-key
   ```

3. Configure LLM to use bad mock in `config/overrides/llmconfig.yml`:
   ```yaml
   providers:
     openai:
       api_base: "http://localhost:8002/v1"
       api_key: "test-key"

   default_provider: "openai"
   default_model: "gpt-3.5-turbo"
   ```

4. Restart backend: `bash agent_start.sh -b`

5. Send messages and observe:
   - First message: WARNING (contains "gun")
   - Second message: BLOCKED (contains "bomb" and "gun")
   - Third message: BLOCKED (contains "bomb")

## Development Notes

- All mocks import shared models from `models.py` to reduce duplication
- Each mock runs on a different port to allow concurrent testing
- Mocks are designed for testing only - not production use
- All mocks support the same OpenAI-compatible API interface
