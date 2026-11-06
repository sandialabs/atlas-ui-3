"""Opt-in stream usage should not change default model request behavior."""
from types import SimpleNamespace

import pytest

from atlas.modules.config.models import LLMConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.usage_tracking import usage_scope


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_stream_usage_requires_model_opt_in(monkeypatch, enabled, with_tools):
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
        "max_tokens": 10,
        "request_stream_usage": enabled,
    }}})
    caller = LiteLLMCaller(llm_config=cfg)
    requests = []

    async def fake_completion(**kwargs):
        requests.append(kwargs)

        async def response():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        return response()

    monkeypatch.setattr(litellm_streaming, "acompletion", fake_completion)
    messages = [{"role": "user", "content": "hello"}]
    with usage_scope() as sink:
        if with_tools:
            tools = [{"type": "function", "function": {
                "name": "noop", "description": "no operation",
                "parameters": {"type": "object", "properties": {}},
            }}]
            result = [x async for x in caller.stream_with_tools("test", messages, tools)]
        else:
            result = [x async for x in caller.stream_plain("test", messages)]
    assert requests and any(x == "hello" for x in result)
    if enabled:
        assert requests[0]["stream_options"] == {"include_usage": True}
    else:
        assert "stream_options" not in requests[0]
    assert sink.prompt_tokens == 3
    assert sink.completion_tokens == 2
    assert sink.calls == 1
