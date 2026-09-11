"""Request timeout validation and propagation across all LLM call paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from atlas.modules.config.models import LLMConfig, ModelConfig
from atlas.modules.config.settings import AppSettings
from atlas.modules.llm import litellm_caller, litellm_streaming
from atlas.modules.llm.litellm_caller import LiteLLMCaller


def test_default_timeout(monkeypatch):
    monkeypatch.delenv("LLM_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert AppSettings(_env_file=None).llm_request_timeout_seconds == 120.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "invalid"])
def test_global_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), "invalid"])
def test_model_timeout_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        ModelConfig(model_name="test", model_url="https://example.org/v1",
                    request_timeout_seconds=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 42.5), (7.5, 7.5)])
@pytest.mark.parametrize("mode", ["plain", "tools", "stream_plain", "stream_tools"])
async def test_timeout_reaches_completion(monkeypatch, override, expected, mode):
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "42.5")
    settings = AppSettings(_env_file=None)
    config = LLMConfig(models={"test": ModelConfig(
        model_name="openai/test", model_url="https://example.org/v1",
        request_timeout_seconds=override,
    )})
    caller = LiteLLMCaller(llm_config=config)
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "test_tool"}}]

    async def stream():
        yield SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content="hello"),
        )])

    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="hello", tool_calls=None),
    )])
    completion = AsyncMock(return_value=stream() if mode.startswith("stream") else response)
    module = litellm_streaming if mode.startswith("stream") else litellm_caller
    with (
        patch("atlas.modules.config.config_manager.get_app_settings", return_value=settings),
        patch.object(module, "acompletion", completion),
    ):
        if mode == "plain":
            await caller.call_plain("test", messages)
        elif mode == "tools":
            await caller.call_with_tools("test", messages, tools)
        elif mode == "stream_plain":
            assert [item async for item in caller.stream_plain("test", messages)]
        else:
            assert [item async for item in caller.stream_with_tools("test", messages, tools)]
    assert completion.call_args.kwargs["timeout"] == expected
