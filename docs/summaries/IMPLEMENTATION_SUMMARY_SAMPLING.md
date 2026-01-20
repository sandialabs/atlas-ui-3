# MCP Sampling Implementation Summary

Last updated: 2026-01-19

## Overview
This PR adds support for MCP LLM sampling, allowing MCP tools to request text generation from the LLM during execution. This enables powerful agentic workflows where tools can leverage AI capabilities for analysis, generation, and reasoning.

## What Was Implemented

### Core Infrastructure
- **Sampling Handler in MCP Client** (`backend/modules/mcp_tools/client.py`)
  - Created `_create_sampling_handler()` that intercepts sampling requests
  - Handler calls LiteLLM directly with proper parameters
  - Returns `CreateMessageResult` as expected by FastMCP
  - Integrated into all MCP client initialization (stdio, HTTP, SSE)

- **Enhanced LiteLLM Caller** (`backend/modules/llm/litellm_caller.py`)
  - Updated `call_plain()` to accept `temperature` and `max_tokens` parameters
  - Maintains backward compatibility with existing callers

- **Model Selection Logic**
  - Supports model preferences from sampling requests
  - Falls back to first available model when no preference matches
  - Respects configured models in llmconfig.yml

### Demo Server
- **sampling_demo MCP Server** (`backend/mcp/sampling_demo/`)
  - 7 example tools demonstrating different sampling capabilities:
    - `summarize_text` - Basic text summarization
    - `analyze_sentiment` - Sentiment analysis with system prompt
    - `generate_code` - Code generation with model preferences
    - `creative_story` - High-temperature creative writing
    - `multi_turn_conversation` - Building conversation context
    - `research_question` - Multi-step agentic research
    - `translate_and_explain` - Sequential sampling workflow
  - Complete README with usage examples

### Configuration
- Added sampling_demo to `config/overrides/mcp.json`
- Configured for "users" group with Public compliance level

### Testing
- **Integration Tests** (`backend/tests/test_sampling_integration.py`)
  - 4 tests covering handler creation, context management, and routing
  - All tests passing with mocked LLM calls

- **Manual E2E Test** (`backend/tests/manual_test_sampling.py`)
  - Verified end-to-end functionality with actual sampling_demo server
  - Tests basic sampling with mock LLM responses
  - Confirmed proper FastMCP integration

### Documentation
- **Developer Guide** (`docs/developer/sampling.md`)
  - Complete guide with examples and use cases
  - Best practices for temperature, max_tokens, and model selection
  - Comparison table: Sampling vs Elicitation
  - Troubleshooting section

- **Admin Guide** (`docs/admin/mcp-servers.md`)
  - Added section on Advanced MCP Features
  - Links to sampling documentation

## How It Works

1. MCP tool calls `ctx.sample(messages, params)`
2. FastMCP client invokes the sampling handler we registered
3. Sampling handler:
   - Extracts and converts messages to proper format
   - Adds system prompt if provided
   - Selects appropriate model based on preferences
   - Calls LiteLLM with parameters
4. LiteLLM routes request to configured model
5. LLM generates response
6. Handler wraps response in `CreateMessageResult`
7. Response returned to tool execution
8. Tool processes result and returns to user

## Key Design Decisions

### Direct LLM Calls vs WebSocket Flow
**Decision:** Call LLM directly in the sampling handler instead of routing through WebSocket to frontend.

**Rationale:**
- Simpler implementation - no need for frontend UI
- Faster response - eliminates round-trip through frontend
- Matches FastMCP's design intent - sampling is server-side LLM access
- Frontend doesn't need to know about sampling requests
- Maintains security - all LLM access controlled by backend

### Sampling Manager Not Used
**Decision:** Keep sampling_manager.py for future use but don't use it in current implementation.

**Rationale:**
- Originally designed for WebSocket-based flow
- Not needed for direct LLM calls
- May be useful for future enhancements (e.g., queuing, rate limiting)
- No harm in keeping it - tests still pass

### Model Selection Strategy
**Decision:** Try preferences first, fall back to first available model.

**Rationale:**
- Respects tool developer's model preferences
- Guarantees a model is always selected
- Simple and predictable behavior
- Matches LiteLLM's provider fallback pattern

## Example Usage

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("My Server")

@mcp.tool
async def summarize(content: str, ctx: Context) -> str:
    """Summarize content using LLM sampling."""
    result = await ctx.sample(
        messages=f"Summarize this: {content}",
        system_prompt="You are a concise summarizer.",
        temperature=0.5,
        max_tokens=200
    )
    return result.text
```

## Testing Results

All tests passing:
- ✅ 4/4 sampling integration tests
- ✅ E2E manual test with sampling_demo server
- ✅ Verified proper CreateMessageResult format
- ✅ Confirmed model selection logic
- ✅ Tested with multiple sampling parameters

## Files Changed

**Core Implementation:**
- `backend/modules/mcp_tools/client.py` (+135 lines)
- `backend/modules/llm/litellm_caller.py` (+20 lines)
- `backend/main.py` (+24 lines, placeholder for future use)

**Demo Server:**
- `backend/mcp/sampling_demo/main.py` (new, 262 lines)
- `backend/mcp/sampling_demo/README.md` (new, 172 lines)

**Configuration:**
- `config/overrides/mcp.json` (+14 lines)

**Tests:**
- `backend/tests/test_sampling_integration.py` (new, 109 lines)
- `backend/tests/manual_test_sampling.py` (new, 97 lines)

**Documentation:**
- `docs/developer/sampling.md` (new, 240 lines)
- `docs/admin/mcp-servers.md` (+14 lines)

## Next Steps

Potential future enhancements:
1. Add sampling with tools (ctx.sample with tools parameter)
2. Implement structured output support (result_type parameter)
3. Add sampling rate limiting or queuing
4. Support for streaming sampling responses
5. Enhanced logging and monitoring for sampling requests

## References

- [FastMCP Sampling Documentation](https://gofastmcp.com/clients/sampling)
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- FastMCP Version: 2.14.3 (supports sampling)
- Implementation follows FastMCP 2.0.0+ sampling API
