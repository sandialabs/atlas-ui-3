#!/usr/bin/env python3
"""Sampling-themed MCP demo tools without server-initiated sampling."""

from atlas.mcp_shared.server_factory import create_stdio_server

# Initialize the MCP server
mcp = create_stdio_server("Sampling Demo")


def _clean_or_default(value: str | None, default: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return default


def _truncate_words(text: str, max_words: int = 40) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


@mcp.tool
async def summarize_text(text: str, summary: str | None = None) -> str:
    """Return the optional client-supplied `summary` or a deterministic fallback."""
    fallback = _truncate_words(text.strip(), max_words=40)
    return _clean_or_default(summary, fallback or "Unable to generate summary")


@mcp.tool
async def analyze_sentiment(text: str, analysis: str | None = None) -> str:
    """Return the optional client-supplied `analysis` or simple keyword-based sentiment."""
    provided = _clean_or_default(analysis, "")
    if provided:
        return provided

    lowered = text.lower()
    positive_hits = sum(word in lowered for word in ("love", "great", "excellent", "good", "happy"))
    negative_hits = sum(word in lowered for word in ("hate", "bad", "terrible", "awful", "sad"))
    if positive_hits > negative_hits:
        return "Positive sentiment."
    if negative_hits > positive_hits:
        return "Negative sentiment."
    return "Neutral sentiment."


@mcp.tool
async def generate_code(description: str, language: str, generated_code: str | None = None) -> str:
    """Return the optional client-supplied `generated_code` or a minimal starter snippet."""
    fallback = (
        f"# {language} starter snippet\n"
        f"# Goal: {description}\n"
        "def main():\n"
        "    raise NotImplementedError('Provide generated_code from the MCP client')\n"
    )
    return _clean_or_default(generated_code, fallback)


@mcp.tool
async def creative_story(prompt: str, story: str | None = None) -> str:
    """Return the optional client-supplied `story` or a short deterministic fallback."""
    fallback = (
        f"Story prompt: {prompt}\n"
        "A curious traveler set out to explore this idea and discovered something meaningful."
    )
    return _clean_or_default(story, fallback)


@mcp.tool
async def multi_turn_conversation(
    topic: str,
    initial_response: str | None = None,
    follow_up_response: str | None = None,
) -> str:
    """Return optional client-supplied conversation turns or deterministic defaults."""
    initial = _clean_or_default(initial_response, f"Key aspects of {topic} include context, tradeoffs, and outcomes.")
    follow_up = _clean_or_default(follow_up_response, f"The top priority in {topic} is understanding the constraints.")
    return f"**Discussion on {topic}**\n\n**Initial Response:**\n{initial}\n\n**Follow-up:**\n{follow_up}"


@mcp.tool
async def research_question(
    question: str,
    breakdown: str | None = None,
    answer: str | None = None,
) -> str:
    """Return optional client-supplied `breakdown`/`answer` or deterministic placeholders."""
    breakdown_text = _clean_or_default(breakdown, "1. Define scope\n2. Gather evidence\n3. Compare alternatives")
    answer_text = _clean_or_default(answer, "Provide a synthesized answer from the client-side model output.")
    return f"**Research Question:** {question}\n\n**Analysis:**\n{breakdown_text}\n\n**Answer:**\n{answer_text}"


@mcp.tool
async def translate_and_explain(
    text: str,
    target_language: str,
    translation: str | None = None,
    explanation: str | None = None,
) -> str:
    """Return optional client-supplied `translation`/`explanation` or deterministic placeholders."""
    translated = _clean_or_default(translation, f"[{target_language} translation needed] {text}")
    notes = _clean_or_default(explanation, "Provide translation rationale from the client-side model output.")
    return f"**Translation to {target_language}:**\n{translated}\n\n**Translation Notes:**\n{notes}"


if __name__ == "__main__":
    mcp.run(show_banner=False)
