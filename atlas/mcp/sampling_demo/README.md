# Sampling Demo MCP Server

This MCP server keeps sampling-oriented tool shapes but now follows a **client-driven LLM** pattern compatible with FastMCP 4.x. Tools no longer call `ctx.sample()` from inside the server. Instead, the MCP client/model can provide optional generated fields, and each tool includes deterministic fallback behavior when those fields are omitted.

## Overview

Client-driven generation enables workflows where tools can:
- Accept model-generated text from the calling client
- Preserve multi-step tool interfaces without server back-channel requests
- Return deterministic fallbacks when generated content is not provided

## Available Tools

Each tool keeps its original demo shape, but the generated text now comes from the caller via optional fields:

1. **`summarize_text(text, summary=None)`**
   - Caller may provide `summary`
   - Fallback truncates the source text deterministically

2. **`analyze_sentiment(text, analysis=None)`**
   - Caller may provide `analysis`
   - Fallback uses simple keyword matching

3. **`generate_code(description, language, generated_code=None)`**
   - Caller may provide `generated_code`
   - Fallback returns a starter snippet for the requested language

4. **`creative_story(prompt, story=None)`**
   - Caller may provide `story`
   - Fallback returns a short deterministic story stub

5. **`multi_turn_conversation(topic, initial_response=None, follow_up_response=None)`**
   - Caller may provide one or both conversation turns
   - Fallback returns deterministic initial and follow-up text

6. **`research_question(question, breakdown=None, answer=None)`**
   - Caller may provide `breakdown` and `answer`
   - Fallback returns deterministic placeholder analysis and answer text

7. **`translate_and_explain(text, target_language, translation=None, explanation=None)`**
   - Caller may provide `translation` and `explanation`
   - Fallback returns deterministic placeholder text for both fields

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

### Caller Responsibilities

The MCP caller is responsible for any actual LLM generation:
1. Read the tool schema to find optional generated fields such as `summary`, `generated_code`, or `translation`
2. Produce those values with the caller's own model flow if desired
3. Call the tool with those values, or omit them to use the deterministic fallback path

## Configuration

This server is configured in `config/mcp.json`:

```json
{
  "sampling_demo": {
    "command": ["python", "mcp/sampling_demo/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "description": "Demonstrates MCP LLM sampling capabilities...",
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
| Purpose | Get user input | Get LLM generation |
| Who responds | Human user | LLM model |
| Use cases | Forms, confirmations | Analysis, generation |
| Timeout | 5 minutes | 5 minutes |
| Multiple turns | Supported | Supported |
| Parameters | Response schema | Optional caller-supplied generated fields |

## Support

For issues or questions about this demo:
- Check Atlas UI documentation in `/docs` folder
- Review the tool schemas for the optional caller-supplied fields
- Report bugs via GitHub issues
