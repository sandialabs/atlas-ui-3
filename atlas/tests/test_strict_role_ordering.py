"""Tests for strict_role_ordering message sanitization.

Verifies that when strict_role_ordering is enabled for a model, system
messages appearing after tool messages are converted to user role.
"""

import pytest

from atlas.modules.config.config_manager import LLMConfig, ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller


def _make_caller(strict: bool = False) -> LiteLLMCaller:
    """Create a LiteLLMCaller with a test model."""
    config = LLMConfig(models={
        "test-model": ModelConfig(
            model_name="test/model",
            model_url="http://localhost:8005/v1",
            api_key="fake",
            strict_role_ordering=strict,
        ),
    })
    return LiteLLMCaller(config)


class TestEnforceStrictRoleOrdering:
    """Unit tests for _enforce_strict_role_ordering."""

    def test_system_after_tool_converted_to_user(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "system", "content": "Files manifest"},
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        # First system message preserved
        assert result[0]["role"] == "system"
        # Post-tool system message converted
        assert result[4]["role"] == "user"
        assert result[4]["content"] == "Files manifest"

    def test_system_before_tool_preserved(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Sure"},
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        assert result[0]["role"] == "system"

    def test_multiple_system_after_tool(self):
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "done", "tool_call_id": "1"},
            {"role": "system", "content": "files manifest"},
            {"role": "system", "content": "synthesis prompt"},
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        assert result[0]["role"] == "system"
        assert result[4]["role"] == "user"
        assert result[5]["role"] == "user"

    def test_no_tool_messages_unchanged(self):
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "extra context"},
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        # No tool messages, so all system messages stay as system
        assert result[0]["role"] == "system"
        assert result[3]["role"] == "system"

    def test_does_not_mutate_original(self):
        original = {"role": "system", "content": "manifest"}
        messages = [
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            original,
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        assert result[1]["role"] == "user"
        assert original["role"] == "system"  # original not mutated

    def test_multi_turn_tool_calls(self):
        """Multiple rounds of tool calls with system messages after each."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "r1", "tool_call_id": "1"},
            {"role": "system", "content": "manifest after round 1"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "2"}]},
            {"role": "tool", "content": "r2", "tool_call_id": "2"},
            {"role": "system", "content": "manifest after round 2"},
        ]
        result = LiteLLMCaller._enforce_strict_role_ordering(messages)
        assert result[0]["role"] == "system"  # initial prompt kept
        assert result[4]["role"] == "user"    # post-tool system → user
        assert result[7]["role"] == "user"    # post-tool system → user


class TestPrepareMessages:
    """Integration tests for _prepare_messages with strict_role_ordering config."""

    def test_strict_model_converts_roles(self):
        caller = _make_caller(strict=True)
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "system", "content": "files"},
        ]
        result = caller._prepare_messages("test-model", messages)
        assert result[4]["role"] == "user"

    def test_non_strict_model_preserves_roles(self):
        caller = _make_caller(strict=False)
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "system", "content": "files"},
        ]
        result = caller._prepare_messages("test-model", messages)
        assert result[4]["role"] == "system"

    def test_also_strips_empty_tool_calls(self):
        """Verify _sanitize_messages still runs alongside strict ordering."""
        caller = _make_caller(strict=True)
        messages = [
            {"role": "assistant", "content": "ok", "tool_calls": []},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "system", "content": "manifest"},
        ]
        result = caller._prepare_messages("test-model", messages)
        assert "tool_calls" not in result[0]  # empty tool_calls stripped
        assert result[2]["role"] == "user"     # strict ordering applied


class TestModelConfigStrictRoleOrdering:
    """Verify the config field is parsed correctly."""

    def test_default_is_false(self):
        mc = ModelConfig(model_name="x", model_url="http://x")
        assert mc.strict_role_ordering is False

    def test_set_to_true(self):
        mc = ModelConfig(model_name="x", model_url="http://x", strict_role_ordering=True)
        assert mc.strict_role_ordering is True

    def test_parsed_from_dict(self):
        config = LLMConfig(models={
            "m": {
                "model_name": "x",
                "model_url": "http://x",
                "strict_role_ordering": True,
            }
        })
        assert config.models["m"].strict_role_ordering is True
