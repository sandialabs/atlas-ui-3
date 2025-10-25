from interfaces.llm import LLMResponse


def test_llm_response_has_tool_calls():
    r1 = LLMResponse(content="hello", tool_calls=None)
    assert r1.has_tool_calls() is False

    r2 = LLMResponse(content="hi", tool_calls=[{"type": "function", "function": {"name": "t"}}])
    assert r2.has_tool_calls() is True
