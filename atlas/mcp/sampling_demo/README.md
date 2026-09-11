# Sampling Demo MCP Server

This MCP server keeps sampling-oriented tool shapes but now follows a **client-driven LLM** pattern. Tools do not call `ctx.sample()` from inside the server. Instead, the MCP client/model can provide optional generated fields, and each tool includes deterministic fallback behavior when those fields are omitted.

## Overview

Client-driven generation enables workflows where tools can:
- Accept model-generated text from the calling client
- Preserve multi-step tool interfaces without server back-channel requests
- Return deterministic fallbacks when generated content is not provided

## Available Tools

### Basic Tools

1. **`summarize_text(text, summary=None)`**
   - Client may provide `summary`
   - Fallback truncates the source text to a short summary

2. **`analyze_sentiment(text, analysis=None)`**
   - Client may provide `analysis`
   - Fallback uses simple keyword-based sentiment classification

3. **`generate_code(description, language, generated_code=None)`**
   - Client may provide `generated_code`
   - Fallback returns a minimal starter snippet

4. **`creative_story(prompt, story=None)`**
   - Client may provide `story`
   - Fallback returns a short deterministic story stub

### Multi-Step Tools

5. **`multi_turn_conversation(topic, initial_response=None, follow_up_response=None)`**
   - Client may provide one or both response fields
   - Fallback returns a deterministic two-turn conversation template

6. **`research_question(question, breakdown=None, answer=None)`**
   - Client may provide `breakdown` and `answer`
   - Fallback returns placeholder analysis steps and answer guidance

7. **`translate_and_explain(text, target_language, translation=None, explanation=None)`**
   - Client may provide `translation` and `explanation`
   - Fallback returns placeholder translation/explanation text

## Usage Examples

### In Atlas UI Chat

After the sampling_demo server is enabled, you can test it with prompts like:

```
"Summarize this text using the sampling demo: [your text]"
"Analyze the sentiment of this review: [review text]"
"Generate Python code that calculates fibonacci numbers"
"Write a creative story about a robot learning to paint"
"Have a conversation about artificial intelligence"
"Research this question: What are the benefits of renewable energy?"
"Translate 'Hello, how are you?' to Spanish and explain the choices"
```

## Technical Details

### Client-Driven Flow

1. Client/model decides whether to generate text for optional tool fields (for example `summary`, `generated_code`, `translation`).
2. Client calls the tool with those values.
3. Tool returns provided text, or deterministic fallback output if values are omitted.

### Client Responsibilities

The server does not pick a model or run an LLM on its own. The caller is responsible for:
1. Generating any richer content it wants to pass in optional fields
2. Choosing the model/provider on the client side
3. Omitting optional fields when deterministic fallback output is acceptable

### Relationship to Server-Side Sampling

Atlas still supports server-initiated `ctx.sample()` flows elsewhere while FastMCP 3.x is pinned. This demo intentionally avoids that back-channel so its tool shapes remain usable with client-driven orchestration.

## Configuration

This server is configured in `config/mcp.json`:

```json
{
  "sampling_demo": {
    "command": ["python", "mcp/sampling_demo/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "description": "Demonstrates client-driven MCP text-generation tool contracts...",
    "compliance_level": "Public"
  }
}
```

## Development

To run the server standalone for testing:

```bash
cd atlas
python mcp/sampling_demo/main.py
```

The FastMCP framework will display available tools and their schemas.

## References

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- FastMCP Version: 4.x compatible

## Example Tool Implementation (Client-Driven)

```python
from fastmcp import FastMCP

mcp = FastMCP("My Server")

@mcp.tool
async def analyze_text(text: str, analysis: str | None = None) -> str:
    """Use client-provided analysis, with deterministic fallback."""
    if analysis:
        return analysis
    return f"Analysis placeholder for: {text}"
```

## Comparison with Elicitation

| Feature | Elicitation | Sampling |
|---------|------------|----------|
| Purpose | Get user input | Carry client-generated text through tool calls |
| Who responds | Human user | Calling client/model |
| Use cases | Forms, confirmations | Analysis, generation |
| Timeout | 5 minutes | 5 minutes |
| Multiple turns | Supported | Supported |
| Parameters | Response schema | Optional generated text/code fields |

## Support

For issues or questions about this demo:
- Check Atlas UI documentation in `/docs` folder
- Review FastMCP sampling docs at https://gofastmcp.com if you need true server-side `ctx.sample()` examples
- Report bugs via GitHub issues
