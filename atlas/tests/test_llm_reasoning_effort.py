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

import pytest
from pydantic import ValidationError

from atlas.domain.errors import ConfigurationError
from atlas.modules.config.config_manager import ConfigManager, LLMConfig, ModelConfig
from atlas.modules.config.models import REASONING_EFFORT_VALUES
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
        from pathlib import Path

        import yaml

        config_path = (
            Path(__file__).resolve().parents[1] / "config" / "llmconfig.yml"
        )
        models = yaml.safe_load(config_path.read_text())["models"]
        assert models["gpt-5.6-luna"]["reasoning_effort"] == "none"


class TestReasoningEffortIsValidatedAtLoad:
    """A bad effort must fail when the config file is read, not as a 400 mid-chat.

    Every value below loads without complaint if the field is a bare
    ``Optional[str]``, and then returns HTTP 400 on the user's first message.
    """

    @pytest.mark.parametrize("value", REASONING_EFFORT_VALUES)
    def test_every_documented_level_is_accepted(self, value):
        config = ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort=value)
        assert config.reasoning_effort == value

    def test_a_misspelled_level_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort="meduim")
        message = str(exc.value)
        assert "meduim" in message
        for level in REASONING_EFFORT_VALUES:
            assert level in message

    def test_an_unknown_level_is_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort="off")

    def test_the_string_None_is_rejected_and_explained(self):
        """``reasoning_effort: None`` unquoted is the YAML *string* "None"."""
        with pytest.raises(ValidationError) as exc:
            ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort="None")
        message = str(exc.value)
        assert "omit the key" in message

    def test_a_wrong_case_level_is_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort="NONE")

    def test_a_non_string_level_is_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort=5)

    def test_surrounding_whitespace_is_trimmed(self):
        """A stray space must not reach the provider as part of the value."""
        config = ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort=" none ")
        assert config.reasoning_effort == "none"

    def test_a_blank_value_is_unset_not_an_error(self):
        """Clearing the field in YAML means "send nothing", as before."""
        config = ModelConfig(model_name="m", model_url="https://x/v1", reasoning_effort="   ")
        assert config.reasoning_effort is None

    def test_a_bad_level_fails_the_whole_llmconfig(self):
        """The failure has to reach the operator loading llmconfig.yml."""
        with pytest.raises(ValidationError):
            LLMConfig(models={
                "bad": {
                    "model_name": "bad",
                    "model_url": "https://x/v1",
                    "reasoning_effort": "meduim",
                },
            })

    def test_a_rejected_level_never_reaches_the_payload(self):
        """The end-to-end point of the validator: the 400 cannot be built."""
        with pytest.raises(ValidationError):
            _make_caller({"gpt-5.6-luna": {"reasoning_effort": "meduim"}})


class TestReasoningEffortConfigLoading:
    def test_invalid_effort_raises_instead_of_loading_zero_models(self, tmp_path, monkeypatch):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  good:\n"
            "    model_name: good\n"
            "    model_url: https://x/v1\n"
            "  bad:\n"
            "    model_name: bad\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: meduim\n",
            encoding="utf-8",
        )
        manager = ConfigManager()
        monkeypatch.setattr(manager, "_search_paths", lambda _: [config_path])

        with pytest.raises(ConfigurationError, match="meduim"):
            manager.llm_config

    def test_validation_failure_is_cached_and_not_re_read(self, tmp_path, monkeypatch):
        """A schema-invalid file blocks startup; it must not be re-parsed each access."""
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  bad:\n"
            "    model_name: bad\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: meduim\n",
            encoding="utf-8",
        )
        manager = ConfigManager()
        calls = []

        def _paths(_name):
            calls.append(_name)
            return [config_path]

        monkeypatch.setattr(manager, "_search_paths", _paths)

        with pytest.raises(ConfigurationError):
            manager.llm_config
        with pytest.raises(ConfigurationError):
            manager.llm_config

        # The second access re-raised the cached error rather than re-reading.
        assert len(calls) == 1

    def test_reload_clears_a_cached_validation_failure(self, tmp_path, monkeypatch):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  bad:\n"
            "    model_name: bad\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: meduim\n",
            encoding="utf-8",
        )
        manager = ConfigManager()
        monkeypatch.setattr(manager, "_search_paths", lambda _: [config_path])

        with pytest.raises(ConfigurationError):
            manager.llm_config

        config_path.write_text(
            "models:\n"
            "  good:\n"
            "    model_name: good\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: none\n",
            encoding="utf-8",
        )
        manager.reload_configs()

        assert list(manager.llm_config.models) == ["good"]

    def test_missing_config_still_loads_empty_config(self, tmp_path, monkeypatch):
        missing_path = tmp_path / "llmconfig.yml"
        manager = ConfigManager()
        monkeypatch.setattr(manager, "_search_paths", lambda _: [missing_path])

        assert manager.llm_config.models == {}

    def test_valid_config_still_loads_models(self, tmp_path, monkeypatch):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  valid:\n"
            "    model_name: valid\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: none\n",
            encoding="utf-8",
        )
        manager = ConfigManager()
        monkeypatch.setattr(manager, "_search_paths", lambda _: [config_path])

        assert manager.llm_config.models["valid"].reasoning_effort == "none"
        assert len(manager.llm_config.models) == 1

    def test_validation_error_does_not_leak_credentials(self, tmp_path, monkeypatch):
        """A missing required field makes pydantic report the whole entry as input."""
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  bad:\n"
            "    model_name: bad\n"
            "    api_key: sk-SUPERSECRET\n"
            "    extra_headers:\n"
            "      Authorization: Bearer tok-SUPERSECRET\n",
            encoding="utf-8",
        )
        manager = ConfigManager()
        monkeypatch.setattr(manager, "_search_paths", lambda _: [config_path])

        with pytest.raises(ConfigurationError) as exc:
            manager.llm_config

        message = str(exc.value)
        assert "sk-SUPERSECRET" not in message
        assert "tok-SUPERSECRET" not in message
        # Still actionable: the failing field and the searched path are named.
        assert "model_url" in message
        assert str(config_path) in message


class TestConfigCliOnInvalidConfig:
    """The diagnostic commands must explain a bad config, not traceback on it."""

    def _manager_raising(self, monkeypatch, tmp_path):
        from atlas.modules.config import cli

        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(
            "models:\n"
            "  bad:\n"
            "    model_name: bad\n"
            "    model_url: https://x/v1\n"
            "    reasoning_effort: meduim\n",
            encoding="utf-8",
        )

        def _factory():
            manager = ConfigManager()
            manager._search_paths = lambda _: [config_path]
            return manager

        monkeypatch.setattr(cli, "ConfigManager", _factory)
        return cli

    def test_list_models_prints_the_error_and_exits_non_zero(
        self, monkeypatch, tmp_path, capsys
    ):
        cli = self._manager_raising(monkeypatch, tmp_path)

        with pytest.raises(SystemExit) as exc:
            cli.list_models(None)

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "❌" in out
        assert "meduim" in out

    def test_export_config_prints_the_error_and_exits_non_zero(
        self, monkeypatch, tmp_path, capsys
    ):
        cli = self._manager_raising(monkeypatch, tmp_path)

        with pytest.raises(SystemExit) as exc:
            cli.export_config(None)

        assert exc.value.code == 1
        assert "❌" in capsys.readouterr().out
