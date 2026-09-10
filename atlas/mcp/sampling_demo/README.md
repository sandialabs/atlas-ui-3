# Sampling Demo MCP Server

This MCP server keeps sampling-oriented tool shapes but now follows a **client-driven LLM** pattern compatible with FastMCP 4.x. Tools no longer call `ctx.sample()` from inside the server. Instead, the MCP client/model can provide optional generated fields, and each tool includes deterministic fallback behavior when those fields are omitted.

## Overview

Client-driven generation enables workflows where tools can:
- Accept model-generated text from the calling client
- Preserve multi-step tool interfaces without server back-channel requests
- Return deterministic fallbacks when generated content is not provided

## Available Tools

### Basic Sampling

1. **`summarize_text(text)`** - Text Summarization
   - Demonstrates basic LLM sampling
   - Requests the LLM to generate a concise summary
   - Uses simple prompt without additional parameters

2. **`analyze_sentiment(text)`** - Sentiment Analysis
   - Demonstrates sampling with system prompts
   - Uses lower temperature (0.3) for consistent analysis
   - System prompt establishes LLM role as sentiment analyzer

3. **`generate_code(description, language)`** - Code Generation
   - Demonstrates sampling with model preferences
   - Hints which models should be preferred (gpt-4, claude-3-sonnet, etc.)
   - Uses higher max_tokens (1000) for code generation

4. **`creative_story(prompt)`** - Creative Writing
   - Demonstrates high temperature sampling for creativity
   - Uses temperature=0.9 for varied, creative outputs
   - Limited to 500 tokens for short stories

### Advanced Sampling

5. **`multi_turn_conversation(topic)`** - Multi-turn Conversation
   - Demonstrates maintaining conversation context
   - Multiple sequential sampling calls with message history
   - Builds up conversation across sampling requests

6. **`research_question(question)`** - Agentic Research
   - Demonstrates agentic workflow with sampling
   - Multiple sampling calls to break down and answer questions
   - Shows complex reasoning and synthesis

7. **`translate_and_explain(text, target_language)`** - Sequential Tasks
   - Demonstrates multi-step workflows
   - First sampling for translation, second for explanation
   - Shows how to chain sampling results

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

### Model Selection

The sampling handler selects models based on:
1. **Model preferences** provided in the sampling request
2. **Configured models** in Atlas llmconfig.yml
3. **Default model** as fallback

Model preferences are hints, not requirements. The backend uses the first matching configured model or falls back to the default.

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
| Parameters | Response schema | Temperature, max_tokens, model preferences |

## Support

For issues or questions about sampling:
- Check Atlas UI documentation in `/docs` folder
- Review FastMCP sampling docs at https://gofastmcp.com
- Report bugs via GitHub issues
