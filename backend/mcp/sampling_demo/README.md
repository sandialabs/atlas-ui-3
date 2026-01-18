# Sampling Demo MCP Server

This MCP server demonstrates **LLM sampling** capabilities introduced in FastMCP 2.0.0+. Sampling allows tools to request text generation from an LLM during execution, enabling AI-powered analysis, generation, reasoning, and more without the client needing to orchestrate multiple calls.

## Overview

LLM sampling enables interactive workflows where tools can:
- Request LLM text generation mid-execution
- Build multi-turn conversations with context
- Leverage AI capabilities for analysis and generation
- Implement agentic workflows with reasoning
- Control generation parameters (temperature, max_tokens, model preferences)

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

### Sampling Parameters

Tools can control LLM generation with parameters:

- **messages**: Simple string or list of SamplingMessage objects for multi-turn
- **system_prompt**: Establishes LLM role and behavior
- **temperature**: Controls randomness (0.0 = deterministic, 1.0 = creative)
- **max_tokens**: Maximum tokens to generate (default: 512)
- **model_preferences**: Hints for which models the client should prefer

### Sampling Flow

1. Tool calls `ctx.sample()` with parameters
2. FastMCP client sends sampling request to Atlas backend
3. Backend's sampling handler routes request to configured LLM (via LiteLLM)
4. LLM generates response based on parameters
5. Response is returned to the tool execution
6. Tool processes and returns result to user

### Model Selection

The sampling handler selects models based on:
1. **Model preferences** provided in the sampling request
2. **Configured models** in Atlas llmconfig.yml
3. **Default model** as fallback

Model preferences are hints, not requirements. The backend uses the first matching configured model or falls back to the default.

## Configuration

This server is configured in `config/overrides/mcp.json`:

```json
{
  "sampling_demo": {
    "command": ["python", "mcp/sampling_demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Demonstrates MCP LLM sampling capabilities...",
    "compliance_level": "Public"
  }
}
```

## Development

To run the server standalone for testing:

```bash
cd backend
python mcp/sampling_demo/main.py
```

The FastMCP framework will display available tools and their schemas.

## References

- [FastMCP Sampling Documentation](https://gofastmcp.com/clients/sampling)
- [MCP Specification - Sampling](https://spec.modelcontextprotocol.io/)
- FastMCP Version: 2.0.0+

## Example Tool Implementation

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("My Server")

@mcp.tool
async def analyze_text(text: str, ctx: Context) -> str:
    """Analyze text using LLM sampling."""
    result = await ctx.sample(
        messages=f"Analyze this text: {text}",
        system_prompt="You are an expert analyst.",
        temperature=0.5,
        max_tokens=500
    )
    return result.text or "Analysis failed"
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
