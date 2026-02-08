#!/usr/bin/env python3
"""
Sampling Demo MCP Server using FastMCP

This server demonstrates LLM sampling capabilities - requesting
LLM text generation from the client during tool execution.

Supports:
- Basic text generation
- Multi-turn conversations
- System prompts
- Temperature control
- Model preferences
- Agentic workflows with tool use
"""

from fastmcp import Context, FastMCP

# Initialize the MCP server
mcp = FastMCP("Sampling Demo")


@mcp.tool
async def summarize_text(text: str, ctx: Context) -> str:
    """
    Summarize the provided text using LLM sampling.

    This tool demonstrates basic LLM sampling - requesting the LLM
    to generate a summary of the given text.

    Args:
        text: The text to summarize

    Returns:
        A summary of the input text
    """
    result = await ctx.sample(f"Please provide a concise summary of the following text:\n\n{text}")
    return result.text or "Unable to generate summary"


@mcp.tool
async def analyze_sentiment(text: str, ctx: Context) -> str:
    """
    Analyze the sentiment of the provided text using LLM sampling.

    This tool demonstrates sampling with a system prompt that establishes
    the LLM's role as a sentiment analyzer.

    Args:
        text: The text to analyze

    Returns:
        Sentiment analysis result
    """
    result = await ctx.sample(
        messages=f"Analyze the sentiment of this text: {text}",
        system_prompt="You are a sentiment analysis expert. Provide clear, concise sentiment analysis with reasoning.",
        temperature=0.3  # Lower temperature for more consistent analysis
    )
    return result.text or "Unable to analyze sentiment"


@mcp.tool
async def generate_code(description: str, language: str, ctx: Context) -> str:
    """
    Generate code based on a description using LLM sampling.

    This tool demonstrates sampling with model preferences - hinting
    which models should be used for code generation.

    Args:
        description: Description of what the code should do
        language: Programming language to use

    Returns:
        Generated code
    """
    result = await ctx.sample(
        messages=f"Generate {language} code that does the following: {description}",
        system_prompt=f"You are an expert {language} programmer. Write clean, well-commented code.",
        temperature=0.7,
        max_tokens=1000,
        model_preferences=["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]  # Prefer larger models for code
    )
    return result.text or "Unable to generate code"


@mcp.tool
async def creative_story(prompt: str, ctx: Context) -> str:
    """
    Generate a creative story using LLM sampling with high temperature.

    This tool demonstrates sampling with higher temperature for more
    creative and varied outputs.

    Args:
        prompt: Story prompt or theme

    Returns:
        Generated creative story
    """
    result = await ctx.sample(
        messages=f"Write a creative short story based on this prompt: {prompt}",
        system_prompt="You are a creative fiction writer known for imaginative storytelling.",
        temperature=0.9,  # High temperature for creativity
        max_tokens=500
    )
    return result.text or "Unable to generate story"


@mcp.tool
async def multi_turn_conversation(topic: str, ctx: Context) -> str:
    """
    Have a multi-turn conversation about a topic using LLM sampling.

    This tool demonstrates multi-turn sampling - building up a conversation
    history and maintaining context across multiple sampling calls.

    Args:
        topic: The topic to discuss

    Returns:
        Summary of the conversation
    """
    from mcp.types import SamplingMessage, TextContent

    # Start with an introduction
    messages = [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=f"Let's discuss {topic}. What are the key aspects to consider?")
        )
    ]

    # First sampling: Get initial response
    result1 = await ctx.sample(
        messages=messages,
        system_prompt="You are a knowledgeable assistant engaging in a thoughtful discussion.",
        temperature=0.7
    )

    # Add response to history
    messages.append(
        SamplingMessage(
            role="assistant",
            content=TextContent(type="text", text=result1.text)
        )
    )

    # Second turn: Ask a follow-up question
    messages.append(
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text="That's interesting. Can you elaborate on the most important point?")
        )
    )

    # Second sampling: Get follow-up response
    result2 = await ctx.sample(
        messages=messages,
        temperature=0.7
    )

    # Return summary of conversation
    return f"**Discussion on {topic}**\n\n**Initial Response:**\n{result1.text}\n\n**Follow-up:**\n{result2.text}"


@mcp.tool
async def research_question(question: str, ctx: Context) -> str:
    """
    Research a question using multiple LLM sampling calls.

    This tool demonstrates agentic behavior - using sampling to break down
    a complex question, gather information, and synthesize an answer.

    Args:
        question: The research question to answer

    Returns:
        Comprehensive research-based answer
    """
    # Step 1: Break down the question
    breakdown_result = await ctx.sample(
        messages=f"Break down this question into 3 key sub-questions that need to be answered: {question}",
        system_prompt="You are a research assistant. Identify key sub-questions clearly and concisely.",
        temperature=0.5,
        max_tokens=200
    )

    # Step 2: Research each aspect
    research_result = await ctx.sample(
        messages=f"Based on these sub-questions:\n{breakdown_result.text}\n\nProvide a comprehensive answer to the original question: {question}",
        system_prompt="You are a research expert. Provide well-reasoned, evidence-based answers.",
        temperature=0.6,
        max_tokens=800
    )

    return f"**Research Question:** {question}\n\n**Analysis:**\n{breakdown_result.text}\n\n**Answer:**\n{research_result.text}"


@mcp.tool
async def translate_and_explain(text: str, target_language: str, ctx: Context) -> str:
    """
    Translate text and explain translation choices using LLM sampling.

    This tool demonstrates sequential sampling for multi-step tasks.

    Args:
        text: Text to translate
        target_language: Target language for translation

    Returns:
        Translation with explanation
    """
    # Step 1: Translate
    translation_result = await ctx.sample(
        messages=f"Translate the following text to {target_language}:\n\n{text}",
        system_prompt=f"You are an expert translator. Provide accurate {target_language} translations.",
        temperature=0.3
    )

    # Step 2: Explain translation choices
    explanation_result = await ctx.sample(
        messages=f"Explain the key translation choices made in translating to {target_language}:\n\nOriginal: {text}\n\nTranslation: {translation_result.text}",
        system_prompt="You are a translation expert. Explain translation decisions clearly.",
        temperature=0.4,
        max_tokens=300
    )

    return f"**Translation to {target_language}:**\n{translation_result.text}\n\n**Translation Notes:**\n{explanation_result.text}"


if __name__ == "__main__":
    mcp.run()
