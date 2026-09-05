"""Unit tests for per-model reasoning effort in the LiteLLM payload.

OpenAI's GPT-5.6 family rejects every tool-carrying request on
/v1/chat/completions unless reasoning effort is explicitly set:

    Function tools with reasoning_effort are not supported for gpt-5.6-luna in
    /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.

``ModelConfig.reasoning_effort`` carries the setting and ``_get_model_kwargs``
forwards it, so every call path (plain, tools, and both streaming twins) picks
it up from one place and no model that leaves it unset changes at all.

Added: GH #756.
"""

from unittest.mock import MagicMock

from atlas.modules.config.config_manager import ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller


def _make_caller(models_dict):
    """Build a LiteLLMCaller with a mocked LLMConfig."""
    mock_config = MagicMock()
    mock_models = {}
    for name, overrides in models_dict.items():
        defaults = {
            "model_name": name,
            "model_url": "https://api.openai.com/v1/chat/completions",
            "api_key": "sk-test",
            "api_key_source": "system",
        }
        defaults.update(overrides)
        mock_models[name] = ModelConfig(**defaults)
    mock_config.models = mock_models
    return LiteLLMCaller(llm_config=mock_config)


class TestReasoningEffortKwarg:
    def test_effort_is_sent_when_configured(self):
        caller = _make_caller({"gpt-5.6-luna": {"reasoning_effort": "none"}})
        kwargs = caller._get_model_kwargs("gpt-5.6-luna")
        assert kwargs["reasoning_effort"] == "none"

    def test_key_is_absent_when_not_configured(self):
        """Every model that predates reasoning control must keep today's payload."""
        caller = _make_caller({"gpt-4.1": {}})
        kwargs = caller._get_model_kwargs("gpt-4.1")
        assert "reasoning_effort" not in kwargs

    def test_empty_string_is_treated_as_unset(self):
        """An operator blanking the field in YAML must not send an empty effort."""
        caller = _make_caller({"gpt-4.1": {"reasoning_effort": ""}})
        kwargs = caller._get_model_kwargs("gpt-4.1")
        assert "reasoning_effort" not in kwargs

    def test_a_non_none_effort_is_passed_through_unchanged(self):
        """The field is an operator setting, not a hardcoded 'none' switch."""
        caller = _make_caller({"reasoner": {"reasoning_effort": "high"}})
        kwargs = caller._get_model_kwargs("reasoner")
        assert kwargs["reasoning_effort"] == "high"

    def test_effort_does_not_disturb_the_other_kwargs(self):
        caller = _make_caller({
            "gpt-5.6-luna": {"reasoning_effort": "none", "max_tokens": 4096},
        })
        kwargs = caller._get_model_kwargs("gpt-5.6-luna", temperature=0.7)
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 4096
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["reasoning_effort"] == "none"


class TestShippedConfigCarriesTheSetting:
    def test_model_config_defaults_to_no_effort(self):
        assert ModelConfig(model_name="m", model_url="https://x/v1").reasoning_effort is None

    def test_shipped_gpt_56_entry_pins_effort_none(self):
        """The GPT-5.6 entry in llmconfig.yml is unusable without this line."""
        import yaml
        from pathlib import Path

        config_path = (
            Path(__file__).resolve().parents[1] / "config" / "llmconfig.yml"
        )
        models = yaml.safe_load(config_path.read_text())["models"]
        assert models["gpt-5.6-luna"]["reasoning_effort"] == "none"
