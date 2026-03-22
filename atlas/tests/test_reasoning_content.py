"""Tests for reasoning_content support."""

from atlas.modules.llm.models import LLMResponse, ReasoningBlock


def test_llm_response_reasoning_content_default_none():
    resp = LLMResponse(content="Hello")
    assert resp.reasoning_content is None


def test_llm_response_reasoning_content_set():
    resp = LLMResponse(content="Hello", reasoning_content="thinking")
    assert resp.reasoning_content == "thinking"


def test_reasoning_block():
    block = ReasoningBlock(content="I considered X and Y")
    assert block.content == "I considered X and Y"
