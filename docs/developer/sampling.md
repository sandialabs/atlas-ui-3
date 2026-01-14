# LLM Sampling in MCP Tools

**Last updated: 2026-01-14**

Atlas UI 3 supports **LLM sampling**, an advanced MCP feature (FastMCP 2.0.0+) that enables MCP tools to request text generation from an LLM during execution. This allows tools to leverage AI capabilities for analysis, generation, reasoning, and more—without the client needing to orchestrate multiple calls.

## What is LLM Sampling?

LLM sampling allows MCP tools to pause their execution and request text generation from the configured LLM. This creates powerful agentic workflows where:

- Tools can use LLM for analysis, summarization, or generation
- Complex reasoning tasks can be delegated to the LLM
- Multi-turn conversations can be built within tool execution
- Tools can adapt behavior based on LLM responses

## How It Works

When a tool calls `ctx.sample()`, Atlas UI 3's backend routes the sampling request directly to the configured LLM (via LiteLLM). The tool waits for the LLM response before continuing execution.

### Key Features

- **System Prompts**: Establish LLM role and behavior guidelines
- **Temperature Control**: Adjust randomness (0.0 = deterministic, 1.0 = creative)
- **Max Tokens**: Limit response length
- **Model Preferences**: Hint which models should be used
- **Multi-turn Conversations**: Build conversation context across sampling calls
- **Agentic Workflows**: Chain multiple sampling calls for complex reasoning

### Sampling Flow

1. Tool calls `ctx.sample()` with messages and parameters
2. Backend's sampling handler receives the request
3. Handler routes request to configured LLM using LiteLLM
4. LLM generates response based on parameters
5. Response is returned to tool execution
6. Tool processes result and returns to user

## Use Cases

### Text Summarization
```python
@mcp.tool
async def summarize_text(text: str, ctx: Context) -> str:
    """Summarize text using LLM sampling."""
    result = await ctx.sample(
        f"Please provide a concise summary of: {text}"
    )
    return result.text
```

### Sentiment Analysis
```python
@mcp.tool
async def analyze_sentiment(text: str, ctx: Context) -> str:
    """Analyze sentiment with system prompt."""
    result = await ctx.sample(
        messages=f"Analyze the sentiment: {text}",
        system_prompt="You are a sentiment analysis expert.",
        temperature=0.3  # Lower for consistency
    )
    return result.text
```

### Code Generation
```python
@mcp.tool
async def generate_code(description: str, language: str, ctx: Context) -> str:
    """Generate code with model preferences."""
    result = await ctx.sample(
        messages=f"Generate {language} code: {description}",
        system_prompt=f"You are an expert {language} programmer.",
        temperature=0.7,
        max_tokens=1000,
        model_preferences=["gpt-4", "claude-3-sonnet"]
    )
    return result.text
```

### Multi-turn Conversation
```python
@mcp.tool
async def research_topic(topic: str, ctx: Context) -> str:
    """Research using multi-turn sampling."""
    # First turn: break down question
    breakdown = await ctx.sample(
        f"Break down this topic into key questions: {topic}",
        temperature=0.5
    )
    
    # Second turn: answer comprehensively
    answer = await ctx.sample(
        f"Based on: {breakdown.text}\nAnswer: {topic}",
        temperature=0.6,
        max_tokens=800
    )
    
    return f"Analysis: {breakdown.text}\n\nAnswer: {answer.text}"
```

## Trying LLM Sampling

To experience sampling features:

1. **Access Admin Panel**: Log into Atlas UI 3 and go to the admin panel
2. **Enable Demo Server**: Enable the `sampling_demo` MCP server
3. **Try These Prompts**:
   - "Summarize this text using the sampling demo: [your text]"
   - "Analyze the sentiment of this review: [review text]"
   - "Generate Python code that calculates fibonacci numbers"
   - "Write a creative story about artificial intelligence"
   - "Research this question: What are renewable energy benefits?"

### Available Demo Tools

The sampling demo MCP server includes these example tools:

- **`summarize_text`**: Basic text summarization
- **`analyze_sentiment`**: Sentiment analysis with system prompt
- **`generate_code`**: Code generation with model preferences
- **`creative_story`**: High-temperature creative writing
- **`multi_turn_conversation`**: Build conversation context
- **`research_question`**: Multi-step agentic research
- **`translate_and_explain`**: Sequential sampling workflow

## Model Selection

The sampling handler selects models based on:

1. **Model preferences** provided in the sampling request
2. **Configured models** in Atlas llmconfig.yml
3. **First available model** as fallback

Model preferences are hints, not requirements. The backend uses the first matching configured model or falls back to the first available model.

## Best Practices

### For Tool Developers

When building MCP tools with sampling (requires FastMCP 2.0.0+):

- **Use appropriate temperatures**: Lower (0.0-0.3) for factual tasks, higher (0.7-0.9) for creative tasks
- **Set reasonable max_tokens**: Balance between completeness and cost
- **Provide clear prompts**: Well-structured prompts yield better results
- **Use system prompts**: Establish context and constraints
- **Handle errors gracefully**: Sampling can fail; provide fallbacks
- **Consider model preferences**: Hint at models suited for your task
- **Build conversation context**: Use multi-turn sampling for complex tasks

### Temperature Guidelines

- **0.0-0.3**: Factual, analytical, consistent responses (sentiment analysis, summarization)
- **0.4-0.6**: Balanced responses with some variation (general Q&A, translation)
- **0.7-0.9**: Creative, varied responses (story writing, brainstorming)
- **1.0+**: Maximum randomness (experimental, highly creative tasks)

## Comparison: Sampling vs Elicitation

| Feature | Sampling | Elicitation |
|---------|----------|-------------|
| **Purpose** | Get LLM generation | Get user input |
| **Who responds** | LLM model | Human user |
| **Use cases** | Analysis, generation | Forms, confirmations |
| **Timeout** | 5 minutes | 5 minutes |
| **Multiple turns** | Supported | Supported |
| **Parameters** | Temperature, max_tokens, model preferences | Response schema, field types |

## Technical Notes

- Sampling is handled entirely in the backend (no frontend UI)
- Requests route through LiteLLM for unified provider access
- Sampling timeout is 5 minutes (same as elicitation)
- Model selection respects configured models and compliance levels
- System prompts are prepended to message history
- All sampling calls are logged for debugging

## Troubleshooting

**Sampling Fails**: Check that LLM models are properly configured in llmconfig.yml.

**Model Not Found**: Verify model preferences match configured model names.

**Timeout Issues**: Reduce max_tokens or increase timeout for complex tasks.

**API Key Errors**: Ensure LLM API keys are set in environment variables.

---

For technical documentation on creating sampling-enabled MCP tools, see the [Sampling Demo MCP Server Documentation](../../../backend/mcp/sampling_demo/README.md) and [FastMCP Sampling Documentation](https://gofastmcp.com/clients/sampling).
