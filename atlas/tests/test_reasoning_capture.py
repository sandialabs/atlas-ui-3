"""Unit tests for the reasoning capture helpers (issue #1230).

``reasoning_capture`` collects a model's reasoning from complete messages and
streamed deltas so it can be sent back on the assistant tool-call message;
these tests pin the shapes it accepts and that the reasoning stays internal.
The end-to-end round trip lives in ``test_reasoning_round_trip.py``.
"""

from types import SimpleNamespace

from litellm.types.utils import Message as LiteLLMMessage

from atlas.application.chat.capture.capture_context import _sanitize_messages
from atlas.interfaces.llm import LLMResponse
from atlas.modules.llm.reasoning_capture import (
    ReasoningAccumulator,
    extract_reasoning,
    strip_reasoning,
)

REASONING = "SECRET-REASONING: the user wants the weather, call the tool."
DETAILS = [{"type": "reasoning.encrypted", "id": "rd-1", "data": "opaque-blob", "index": 0}]


# -- Capture helpers -----------------------------------------------------

class TestExtractReasoning:

    def test_litellm_message(self):
        msg = LiteLLMMessage(content="hi", reasoning_content=REASONING)
        assert extract_reasoning(msg) == {"reasoning_content": REASONING}

    def test_reasoning_details_and_thinking_blocks(self):
        msg = SimpleNamespace(
            reasoning_content=None,
            reasoning_details=DETAILS,
            thinking_blocks=[{"type": "thinking", "thinking": "t", "signature": "sig"}],
        )
        assert extract_reasoning(msg) == {
            "reasoning_details": DETAILS,
            "thinking_blocks": [{"type": "thinking", "thinking": "t", "signature": "sig"}],
        }

    def test_nothing_present(self):
        assert extract_reasoning(SimpleNamespace(content="x")) == {}
        assert extract_reasoning(None) == {}


class TestReasoningAccumulator:

    def test_concatenates_reasoning_content(self):
        acc = ReasoningAccumulator()
        for piece in ("a", "b", None, "c"):
            acc.observe(SimpleNamespace(reasoning_content=piece))
        assert acc.fields() == {"reasoning_content": "abc"}

    def test_merges_reasoning_details_by_index(self):
        acc = ReasoningAccumulator()
        acc.observe({"reasoning_details": [{"type": "reasoning.text", "index": 0, "text": "Hel"}]})
        acc.observe({"reasoning_details": [{"type": "reasoning.text", "index": 0, "text": "lo", "signature": "s"}]})
        acc.observe({"reasoning_details": [{"type": "reasoning.encrypted", "index": 1, "data": "blob"}]})
        assert acc.fields() == {"reasoning_details": [
            {"type": "reasoning.text", "index": 0, "text": "Hello", "signature": "s"},
            {"type": "reasoning.encrypted", "index": 1, "data": "blob"},
        ]}

    def test_thinking_blocks_need_a_signature(self):
        acc = ReasoningAccumulator()
        acc.observe({"thinking_blocks": [{"type": "thinking", "thinking": "step 1 "}]})
        acc.observe({"thinking_blocks": [{"type": "thinking", "thinking": "step 2"}]})
        acc.observe({"thinking_blocks": [{"type": "thinking", "signature": "sig-1"}]})
        acc.observe({"thinking_blocks": [{"type": "redacted_thinking", "data": "enc"}]})
        acc.observe({"thinking_blocks": [{"type": "thinking", "thinking": "unsigned"}]})
        assert acc.fields() == {"thinking_blocks": [
            {"type": "thinking", "thinking": "step 1 step 2", "signature": "sig-1"},
            {"type": "redacted_thinking", "data": "enc"},
        ]}

    def test_reset_discards_a_failed_attempt(self):
        acc = ReasoningAccumulator()
        acc.observe({"reasoning_content": "stale"})
        acc.reset()
        acc.observe({"reasoning_content": "fresh"})
        assert acc.fields() == {"reasoning_content": "fresh"}


class TestReasoningStaysInternal:

    def test_llm_response_repr_hides_reasoning(self):
        response = LLMResponse(content="x", reasoning={"reasoning_content": REASONING})
        assert REASONING not in repr(response)

    def test_capture_strips_reasoning(self):
        captured = _sanitize_messages([{
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            "reasoning_content": REASONING,
            "reasoning_details": DETAILS,
            "thinking_blocks": [{"type": "thinking", "thinking": "t", "signature": "s"}],
        }])
        assert set(captured[0]) == {"role", "content", "tool_calls"}

    def test_strip_reasoning_returns_same_object_when_absent(self):
        msg = {"role": "user", "content": "hi"}
        assert strip_reasoning(msg) is msg

def test_token_estimates_count_reasoning():
    """Reasoning sent back within a turn is part of the request, so the
    context-window and compaction estimates must count it."""
    from atlas.application.chat.context.compaction_engine import approx_tokens
    from atlas.modules.llm.token_estimation import _text_only_messages

    plain = {"role": "assistant", "content": "x", "tool_calls": [{"id": "1"}]}
    with_reasoning = {**plain, "reasoning_content": "r" * 4000}
    assert approx_tokens(with_reasoning) >= approx_tokens(plain) + 900
    stripped, _ = _text_only_messages([with_reasoning])
    assert "r" * 4000 in stripped[0]["content"]

def test_thinking_signature_before_text():
    acc = ReasoningAccumulator()
    acc.observe({"thinking_blocks": [{"type": "thinking", "signature": "sig-a"}]})
    acc.observe({"thinking_blocks": [{"type": "thinking", "thinking": "block a"}]})
    acc.observe({"thinking_blocks": [{"type": "thinking", "signature": "sig-b"}]})
    acc.observe({"thinking_blocks": [{"type": "thinking", "thinking": "block b"}]})
    assert acc.fields() == {"thinking_blocks": [
        {"type": "thinking", "thinking": "block a", "signature": "sig-a"},
        {"type": "thinking", "thinking": "block b", "signature": "sig-b"},
    ]}


def test_index_less_reasoning_details_merge_by_id():
    acc = ReasoningAccumulator()
    acc.observe({"reasoning_details": [{"type": "reasoning.text", "id": "r1", "text": "Hel"}]})
    acc.observe({"reasoning_details": [{"type": "reasoning.text", "id": "r1", "text": "lo"}]})
    acc.observe({"reasoning_details": [{"type": "reasoning.summary", "summary": "s"}]})
    assert acc.fields() == {"reasoning_details": [
        {"type": "reasoning.text", "id": "r1", "text": "Hello"},
        {"type": "reasoning.summary", "summary": "s"},
    ]}
