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

@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_repeated_stream_usage_is_counted_once(monkeypatch, with_tools):
    """A cumulative provider snapshot must not be added per SSE chunk."""
    from atlas.modules.llm import litellm_streaming

    caller = LiteLLMCaller(llm_config=LLMConfig.model_validate({
        "models": {"test": {
            "model_name": "openai/test-model",
            "model_url": "http://127.0.0.1:1/v1",
            "api_key": "synthetic-only",
            "request_stream_usage": True,
        }},
    }))

    async def completion(**_kwargs):
        async def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason=None,
                )],
                usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
            )
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=" world", tool_calls=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3),
            )
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3),
            )
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    messages = [{"role": "user", "content": "test"}]
    with usage_scope() as sink:
        if with_tools:
            tools = [{"type": "function", "function": {
                "name": "noop", "parameters": {"type": "object"},
            }}]
            result = [x async for x in caller.stream_with_tools("test", messages, tools)]
        else:
            result = [x async for x in caller.stream_plain("test", messages)]
    assert "".join(x for x in result if isinstance(x, str)) == "hello world"
    assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (4, 3, 1)


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_split_prompt_and_completion_snapshots(monkeypatch, with_tools):
    """A completion-only chunk may not erase an earlier prompt count."""
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
    }}})
    caller = LiteLLMCaller(llm_config=cfg)

    async def completion(**_kwargs):
        async def chunks():
            for index, usage in enumerate([
                {"prompt_tokens": 270, "completion_tokens": 1},
                {"prompt_tokens": 0, "completion_tokens": 12},
            ]):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="hello", tool_calls=None),
                        finish_reason="stop",
                    )] if index == 0 else [],
                    usage=usage,
                )
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    messages = [{"role": "user", "content": "hello"}]
    with usage_scope() as sink:
        if with_tools:
            tools = [{"type": "function", "function": {
                "name": "noop", "parameters": {"type": "object"},
            }}]
            result = [x async for x in caller.stream_with_tools("test", messages, tools)]
        else:
            result = [x async for x in caller.stream_plain("test", messages)]
    assert "hello" in result
    assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (270, 12, 1)
    assert sink.last_prompt_tokens == 270


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("interruption", ["empty", "failure", "close"])
@pytest.mark.asyncio
async def test_stream_interruption_retains_observed_usage(monkeypatch, with_tools, interruption):
    """Provider usage observed before a failed/empty/closed stream is not lost."""
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
    }}})
    caller = LiteLLMCaller(llm_config=cfg)
    # Empty-stream retries intentionally back off in production. This test
    # exercises accounting on the first attempt, without the retry delay.
    monkeypatch.setattr(caller, "_is_retryable_error", lambda exc: False)

    async def completion(**_kwargs):
        async def chunks():
            yield SimpleNamespace(
                choices=[] if interruption == "empty" else [SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason=None,
                )],
                usage=SimpleNamespace(prompt_tokens=27, completion_tokens=4),
            )
            if interruption == "failure":
                raise RuntimeError("synthetic provider stream broke")
            if interruption == "close":
                # Keep the async generator suspended at the first yielded token.
                yield SimpleNamespace(
                    choices=[], usage=SimpleNamespace(prompt_tokens=30, completion_tokens=6),
                )
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    messages = [{"role": "user", "content": "hello"}]
    if with_tools:
        tools = [{"type": "function", "function": {
            "name": "noop", "parameters": {"type": "object"},
        }}]
        stream = caller.stream_with_tools("test", messages, tools)
    else:
        stream = caller.stream_plain("test", messages)

    with usage_scope() as sink:
        if interruption == "close":
            assert await anext(stream) == "hello"
            await stream.aclose()
        else:
            with pytest.raises(Exception):
                [x async for x in stream]
    assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (27, 4, 1)


@pytest.mark.parametrize(
    "default,override,expected",
    [
        (True, None, True),
        (False, True, True),
        (True, False, False),
        (False, None, False),
    ],
)
def test_gateway_usage_setting_is_resolved(default, override, expected):
    """Gateway defaults and allowlist entries must affect streaming kwargs."""
    gateway = {
        "base_url": "https://litellm.invalid/v1",
        "api_key": "synthetic-only",
        "model_defaults": {"request_stream_usage": default},
        "models": {"test-model": (
            {"request_stream_usage": override} if override is not None else {}
        )},
    }
    cfg = LLMConfig.model_validate({"litellm_gateways": {"enterprise": gateway}})
    caller = LiteLLMCaller(llm_config=cfg)
    model_key = "enterprise::team::test-model"
    assert cfg.get_model(model_key).request_stream_usage is expected
    kwargs = caller._stream_usage_kwargs(model_key, {"api_base": gateway["base_url"]})
    assert kwargs.get("stream_options") == (
        {"include_usage": True} if expected else None
    )


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_retry_records_usage_for_both_provider_attempts(monkeypatch, with_tools):
    """An empty but billable attempt must not vanish when a retry succeeds."""
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
    }}})
    caller = LiteLLMCaller(llm_config=cfg)
    attempts = []

    async def completion(**kwargs):
        attempts.append(kwargs)

        async def chunks():
            yield SimpleNamespace(
                choices=[] if len(attempts) == 1 else [SimpleNamespace(
                    delta=SimpleNamespace(content="recovered", tool_calls=None),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10 if len(attempts) == 1 else 20,
                    completion_tokens=1 if len(attempts) == 1 else 2,
                ),
            )
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    monkeypatch.setattr(litellm_streaming, "_retry_backoff_delay", lambda *args: 0.0)
    messages = [{"role": "user", "content": "test"}]
    with usage_scope() as sink:
        if with_tools:
            tools = [{"type": "function", "function": {
                "name": "noop", "parameters": {"type": "object"},
            }}]
            result = [x async for x in caller.stream_with_tools("test", messages, tools)]
        else:
            result = [x async for x in caller.stream_plain("test", messages)]
    assert "recovered" in result
    assert len(attempts) == 2
    assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (30, 3, 2)


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_opted_in_stream_without_usage_logs_diagnostic(monkeypatch, caplog, with_tools):
    """An endpoint ignoring stream_options must not look like a working flag."""
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
        "request_stream_usage": True,
    }}})
    caller = LiteLLMCaller(llm_config=cfg)

    async def completion(**_kwargs):
        async def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )],
                usage=None,
            )
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    with caplog.at_level("WARNING", logger=litellm_streaming.__name__):
        with usage_scope() as sink:
            if with_tools:
                tools = [{"type": "function", "function": {
                    "name": "noop", "parameters": {"type": "object"},
                }}]
                result = [x async for x in caller.stream_with_tools("test", [], tools)]
            else:
                result = [x async for x in caller.stream_plain("test", [])]
    assert "ok" in result
    assert sink.calls == 0
    assert "request_stream_usage is enabled for model test" in caplog.text


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.asyncio
async def test_provider_rejecting_stream_options_is_actionable(monkeypatch, with_tools, caplog):
    """User should be told to disable the specific unsupported flag."""
    from atlas.domain.errors import LLMBadRequestError
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
        "request_stream_usage": True,
    }}})
    caller = LiteLLMCaller(llm_config=cfg)

    async def completion(**_kwargs):
        raise ValueError("unknown parameter: stream_options.include_usage")

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    with caplog.at_level("WARNING", logger=litellm_streaming.__name__):
        if with_tools:
            tools = [{"type": "function", "function": {
                "name": "noop", "parameters": {"type": "object"},
            }}]
            stream = caller.stream_with_tools("test", [], tools)
        else:
            stream = caller.stream_plain("test", [])
        with pytest.raises(LLMBadRequestError, match="Disable request_stream_usage"):
            [x async for x in stream]
    assert "Provider rejected request_stream_usage" in caplog.text


@pytest.mark.parametrize("via_tools", [False, True])
@pytest.mark.asyncio
async def test_delegated_stream_closure_records_usage(monkeypatch, via_tools):
    """Closing RAG/no-tools wrappers must close their inner plain stream."""
    from atlas.modules.llm import litellm_streaming

    cfg = LLMConfig.model_validate({"models": {"test": {
        "model_name": "openai/test-model",
        "model_url": "http://127.0.0.1:1/v1",
        "api_key": "synthetic-only",
    }}})
    caller = LiteLLMCaller(llm_config=cfg)

    async def completion(**_kwargs):
        async def chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason="stop",
                )],
                usage={"prompt_tokens": 23, "completion_tokens": 5},
            )
            yield SimpleNamespace(choices=[], usage=None)
        return chunks()

    monkeypatch.setattr(litellm_streaming, "acompletion", completion)
    messages = [{"role": "user", "content": "test"}]
    if via_tools:
        stream = caller.stream_with_tools("test", messages, [])
    else:
        stream = caller.stream_with_rag("test", messages, [], "synthetic@example.invalid")

    with usage_scope() as sink:
        assert await anext(stream) == "hello"
        await stream.aclose()
        assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (23, 5, 1)


def test_stream_usage_diagnostics_resist_log_forgery(caplog):
    """Both flagged log sites must emit a single safe entry for hostile names."""
    from atlas.domain.errors import LLMBadRequestError
    from atlas.modules.llm import litellm_streaming
    from atlas.modules.llm.usage_tracking import StreamUsageAccumulator

    malicious = "m\r\n[CRITICAL] forged\x1b[31m\u2028\u2029\x7f\x85"
    with caplog.at_level("WARNING", logger=litellm_streaming.__name__):
        with pytest.raises(LLMBadRequestError, match="Disable request_stream_usage"):
            LiteLLMCaller._check_stream_usage_rejection(
                ValueError("invalid stream_options.include_usage"), malicious, True
            )
        LiteLLMCaller._warn_missing_stream_usage(
            malicious, True, True, StreamUsageAccumulator()
        )

    messages = [r.getMessage() for r in caplog.records if r.name == litellm_streaming.__name__]
    assert len(messages) == 2
    assert messages[0].startswith("Provider rejected request_stream_usage")
    assert messages[1].startswith("request_stream_usage is enabled for model")
    assert all("m[CRITICAL] forged[31m" in msg for msg in messages)
    assert all(
        not any(char in msg for char in "\r\n\x1b\u2028\u2029\x7f\x85")
        for msg in messages
    )
