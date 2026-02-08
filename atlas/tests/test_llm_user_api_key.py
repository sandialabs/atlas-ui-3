"""Unit tests for per-user LLM API key resolution.

Tests that LiteLLMCaller correctly resolves API keys from token storage
when a model is configured with api_key_source: "user".

Updated: 2026-02-08
"""

from unittest.mock import MagicMock, patch

import pytest

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
            "api_key": "${OPENAI_API_KEY}",
            "api_key_source": "system",
        }
        defaults.update(overrides)
        mock_models[name] = ModelConfig(**defaults)
    mock_config.models = mock_models
    return LiteLLMCaller(llm_config=mock_config)


class TestResolveUserApiKey:
    """Test _resolve_user_api_key static method."""

    def test_raises_when_no_user_email(self):
        """Should raise ValueError when user_email is None."""
        with pytest.raises(ValueError, match="no user_email was provided"):
            LiteLLMCaller._resolve_user_api_key("my-model", None)

    def test_raises_when_no_stored_token(self):
        """Should raise ValueError when no token is in storage."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_get:
            mock_ts = MagicMock()
            mock_ts.get_valid_token.return_value = None
            mock_get.return_value = mock_ts

            with pytest.raises(ValueError, match="requires a per-user API key"):
                LiteLLMCaller._resolve_user_api_key("my-model", "user@test.com")

    def test_returns_token_value_when_found(self):
        """Should return stored token value when present."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_get:
            mock_ts = MagicMock()
            mock_token = MagicMock()
            mock_token.token_value = "sk-user-key-123"
            mock_ts.get_valid_token.return_value = mock_token
            mock_get.return_value = mock_ts

            result = LiteLLMCaller._resolve_user_api_key("my-model", "user@test.com")
            assert result == "sk-user-key-123"
            mock_ts.get_valid_token.assert_called_once_with("user@test.com", "llm:my-model")


class TestGetModelKwargsApiKeySource:
    """Test _get_model_kwargs with api_key_source=user."""

    def test_system_key_resolves_env_var(self):
        """System models should resolve API key from env vars."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-system-key"}):
            caller = _make_caller({
                "gpt4": {"api_key": "${OPENAI_API_KEY}", "api_key_source": "system"},
            })
            kwargs = caller._get_model_kwargs("gpt4")
            assert kwargs["api_key"] == "sk-system-key"

    def test_user_key_looks_up_token_storage(self):
        """User models should look up key from token storage."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_get:
            mock_ts = MagicMock()
            mock_token = MagicMock()
            mock_token.token_value = "sk-user-key-456"
            mock_ts.get_valid_token.return_value = mock_token
            mock_get.return_value = mock_ts

            caller = _make_caller({
                "user-gpt4": {"api_key": "", "api_key_source": "user"},
            })
            kwargs = caller._get_model_kwargs("user-gpt4", user_email="user@test.com")
            assert kwargs["api_key"] == "sk-user-key-456"

    def test_user_key_raises_without_email(self):
        """User model should raise when user_email is not provided."""
        caller = _make_caller({
            "user-gpt4": {"api_key": "", "api_key_source": "user"},
        })
        with pytest.raises(ValueError, match="no user_email was provided"):
            caller._get_model_kwargs("user-gpt4")

    def test_user_key_raises_when_no_token_stored(self):
        """User model should raise when no token in storage."""
        with patch("atlas.modules.mcp_tools.token_storage.get_token_storage") as mock_get:
            mock_ts = MagicMock()
            mock_ts.get_valid_token.return_value = None
            mock_get.return_value = mock_ts

            caller = _make_caller({
                "user-gpt4": {"api_key": "", "api_key_source": "user"},
            })
            with pytest.raises(ValueError, match="requires a per-user API key"):
                caller._get_model_kwargs("user-gpt4", user_email="user@test.com")


class TestModelConfigApiKeySource:
    """Test ModelConfig accepts api_key_source field."""

    def test_default_is_system(self):
        config = ModelConfig(
            model_name="gpt-4",
            model_url="https://api.openai.com/v1/chat/completions",
            api_key="${OPENAI_API_KEY}",
        )
        assert config.api_key_source == "system"

    def test_accepts_user_value(self):
        config = ModelConfig(
            model_name="gpt-4",
            model_url="https://api.openai.com/v1/chat/completions",
            api_key="",
            api_key_source="user",
        )
        assert config.api_key_source == "user"

    def test_api_key_can_be_empty_for_user_source(self):
        config = ModelConfig(
            model_name="gpt-4",
            model_url="https://api.openai.com/v1/chat/completions",
            api_key="",
            api_key_source="user",
        )
        assert config.api_key == ""
