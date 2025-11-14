"""Integration tests for LLM environment variable expansion."""

import pytest
from backend.modules.config.config_manager import LLMConfig, ModelConfig
from backend.modules.llm.litellm_caller import LiteLLMCaller


class TestLLMEnvExpansionIntegration:
    """Integration tests for LLM caller with environment variable expansion."""

    def test_litellm_caller_resolves_api_key_env_var(self, monkeypatch):
        """LiteLLMCaller should resolve environment variables in api_key."""
        monkeypatch.setenv("TEST_OPENAI_KEY", "sk-test-12345")
        
        # Create LLM config with env var in api_key
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="${TEST_OPENAI_KEY}"
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should resolve the env var
        _ = caller._get_model_kwargs("test-model")
        
        # Verify that the environment variable was set (LiteLLMCaller sets env vars for provider detection)
        import os
        assert os.environ.get("OPENAI_API_KEY") == "sk-test-12345"

    def test_litellm_caller_raises_on_missing_api_key_env_var(self):
        """LiteLLMCaller should raise ValueError when api_key env var is missing."""
        # Create LLM config with missing env var in api_key
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="${MISSING_OPENAI_KEY}"
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should raise ValueError
        with pytest.raises(ValueError, match="Environment variable 'MISSING_OPENAI_KEY' is not set"):
            caller._get_model_kwargs("test-model")

    def test_litellm_caller_handles_literal_api_key(self):
        """LiteLLMCaller should handle literal api_key values."""
        # Create LLM config with literal api_key
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-literal-key-12345"
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should work without errors
        _ = caller._get_model_kwargs("test-model")
        
        # Verify that the environment variable was set
        import os
        assert os.environ.get("OPENAI_API_KEY") == "sk-literal-key-12345"

    def test_litellm_caller_resolves_extra_headers_env_vars(self, monkeypatch):
        """LiteLLMCaller should resolve environment variables in extra_headers."""
        monkeypatch.setenv("TEST_REFERER", "https://myapp.com")
        monkeypatch.setenv("TEST_APP_NAME", "MyTestApp")
        
        # Create LLM config with env vars in extra_headers
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="llama-3-70b",
                    model_url="https://openrouter.ai/api/v1",
                    api_key="sk-test",
                    extra_headers={
                        "HTTP-Referer": "${TEST_REFERER}",
                        "X-Title": "${TEST_APP_NAME}"
                    }
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should resolve the env vars
        model_kwargs = caller._get_model_kwargs("test-model")
        
        # Verify that extra_headers were resolved
        assert "extra_headers" in model_kwargs
        assert model_kwargs["extra_headers"]["HTTP-Referer"] == "https://myapp.com"
        assert model_kwargs["extra_headers"]["X-Title"] == "MyTestApp"

    def test_litellm_caller_raises_on_missing_extra_headers_env_var(self):
        """LiteLLMCaller should raise ValueError when extra_headers env var is missing."""
        # Create LLM config with missing env var in extra_headers
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="llama-3-70b",
                    model_url="https://openrouter.ai/api/v1",
                    api_key="sk-test",
                    extra_headers={
                        "HTTP-Referer": "${MISSING_REFERER}"
                    }
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should raise ValueError
        with pytest.raises(ValueError, match="Environment variable 'MISSING_REFERER' is not set"):
            caller._get_model_kwargs("test-model")

    def test_litellm_caller_handles_literal_extra_headers(self):
        """LiteLLMCaller should handle literal extra_headers values."""
        # Create LLM config with literal extra_headers
        llm_config = LLMConfig(
            models={
                "test-model": ModelConfig(
                    model_name="llama-3-70b",
                    model_url="https://openrouter.ai/api/v1",
                    api_key="sk-test",
                    extra_headers={
                        "HTTP-Referer": "https://literal-app.com",
                        "X-Title": "LiteralApp"
                    }
                )
            }
        )
        
        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get model kwargs - this should work without errors
        model_kwargs = caller._get_model_kwargs("test-model")
        
        # Verify that extra_headers were passed through
        assert "extra_headers" in model_kwargs
        assert model_kwargs["extra_headers"]["HTTP-Referer"] == "https://literal-app.com"
        assert model_kwargs["extra_headers"]["X-Title"] == "LiteralApp"
