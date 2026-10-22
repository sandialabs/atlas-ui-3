"""Context window resolution, request token estimation and usage tracking (#877)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from atlas.modules.config.models import LLMConfig, ModelConfig
from atlas.modules.llm import token_estimation
from atlas.modules.llm.context_window import (
    SOURCE_CONFIG,
    SOURCE_FALLBACK,
    SOURCE_LITELLM,
    resolve_context_window,
)
from atlas.modules.llm.token_estimation import (
    IMAGE_TOKENS,
    METHOD_HEURISTIC,
    METHOD_TOKENIZER,
    estimate_request_tokens,
)
from atlas.modules.llm.usage_tracking import record_response_usage, record_usage, usage_scope


def _llm_config(**models):
    return LLMConfig(models={
        name: ModelConfig(model_url="http://localhost:9999/v1", **fields)
        for name, fields in models.items()
    })


class TestContextWindow:
    def test_configured_window_wins(self):
        config = _llm_config(alias={"model_name": "gpt-4o", "context_window": 32000, "max_tokens": 2000})
        window = resolve_context_window(config, "alias")
        assert window.total == 32000
        assert window.source == SOURCE_CONFIG
        assert window.output_reserve == 2000
        assert window.usable == 32000 - 2000 - int(32000 * 0.05)

    def test_alias_resolves_through_model_id(self):
        # The Atlas key is an alias; LiteLLM knows the configured model id.
        config = _llm_config(**{"Team GPT": {"model_name": "openai/gpt-4o"}})
        window = resolve_context_window(config, "Team GPT")
        assert window.source == SOURCE_LITELLM
        assert window.total == 128000

    def test_provider_prefix_is_stripped(self):
        config = _llm_config(m={"model_name": "some-proxy/gpt-4o"})
        assert resolve_context_window(config, "m").source == SOURCE_LITELLM

    def test_unknown_alias_uses_fallback_setting(self):
        config = _llm_config(m={"model_name": "in-house-llm-v3"})
        settings = SimpleNamespace(context_fallback_window_tokens=50000)
        window = resolve_context_window(config, "m", settings)
        assert window.source == SOURCE_FALLBACK
        assert window.total == 50000
        assert not window.known

    def test_unknown_model_never_raises(self):
        window = resolve_context_window(None, "missing")
        assert window.source == SOURCE_FALLBACK
        assert window.total == 128000

    def test_reserve_capped_to_quarter(self):
        config = _llm_config(m={"model_name": "x", "context_window": 8000, "max_tokens": 7000})
        window = resolve_context_window(config, "m")
        assert window.output_reserve == 2000
        assert window.usable > 0

    def test_rejects_non_positive_window(self):
        with pytest.raises(ValueError):
            ModelConfig(model_name="x", model_url="http://x", context_window=0)


class TestTokenEstimation:
    def test_tokenizer_path(self):
        estimate = estimate_request_tokens([{"role": "user", "content": "hello world " * 100}], model="gpt-4o")
        assert estimate.method == METHOD_TOKENIZER
        assert 150 < estimate.tokens < 400

    def test_falls_back_to_heuristic(self):
        with patch("litellm.token_counter", side_effect=RuntimeError("no tokenizer")):
            estimate = estimate_request_tokens([{"role": "user", "content": "x" * 3000}])
        assert estimate.method == METHOD_HEURISTIC
        assert estimate.tokens >= 1000

    def test_huge_payload_skips_tokenizer(self):
        with patch("litellm.token_counter") as counter:
            big = "y" * (token_estimation.TOKENIZER_MAX_CHARS + 1)
            estimate = estimate_request_tokens([{"role": "tool", "content": big, "tool_call_id": "c"}])
        counter.assert_not_called()
        assert estimate.method == METHOD_HEURISTIC

    def test_images_never_reach_tokenizer(self):
        seen = []

        def counter(model=None, messages=None, text=None):
            seen.append(messages)
            return 10

        content = [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}},
        ]
        with patch("litellm.token_counter", side_effect=counter):
            estimate = estimate_request_tokens([{"role": "user", "content": content}])
        assert estimate.tokens == 10 + IMAGE_TOKENS
        assert all(isinstance(m["content"], str) for m in seen[0])

    def test_tools_and_tool_calls_count(self):
        base = [{"role": "user", "content": "go"}]
        with_calls = base + [{
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{\"a\": \"" + "z" * 600 + "\"}"}}],
        }]
        tools = [{"type": "function", "function": {"name": "f", "description": "d" * 900, "parameters": {}}}]
        plain = estimate_request_tokens(base).tokens
        assert estimate_request_tokens(with_calls).tokens > plain + 50
        assert estimate_request_tokens(base, tools).tokens > plain + 50


class TestUsageTracking:
    def test_scope_collects_reported_usage(self):
        with usage_scope() as sink:
            record_response_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=7)))
            record_usage({"prompt_tokens": 250, "completion_tokens": 3})
            record_response_usage(SimpleNamespace(usage=None))
        assert sink.calls == 2
        assert sink.prompt_tokens == 350
        assert sink.completion_tokens == 10
        assert sink.last_prompt_tokens == 250

    def test_outside_scope_is_noop(self):
        record_usage({"prompt_tokens": 5})  # must not raise

    def test_garbage_usage_ignored(self):
        with usage_scope() as sink:
            record_usage({"prompt_tokens": "lots", "completion_tokens": -1})
        assert sink.calls == 0
