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

        # Cleanup to avoid leaking into other tests
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

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

    def test_litellm_caller_handles_literal_api_key(self, monkeypatch):
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

        # Cleanup to avoid leaking into other tests
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

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

    def test_custom_endpoint_with_env_var_api_key(self, monkeypatch):
        """Custom endpoint should pass api_key in kwargs when using env var."""
        monkeypatch.setenv("CUSTOM_LLM_KEY", "sk-custom-12345")

        # Create LLM config for custom endpoint with env var in api_key
        llm_config = LLMConfig(
            models={
                "custom-model": ModelConfig(
                    model_name="custom-model-name",
                    model_url="https://custom-llm.example.com/v1",
                    api_key="${CUSTOM_LLM_KEY}"
                )
            }
        )

        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get model kwargs
        model_kwargs = caller._get_model_kwargs("custom-model")

        # Verify that api_key is in kwargs (critical for custom endpoints)
        assert "api_key" in model_kwargs
        assert model_kwargs["api_key"] == "sk-custom-12345"

        # Verify that api_base is set for custom endpoint
        assert "api_base" in model_kwargs
        assert model_kwargs["api_base"] == "https://custom-llm.example.com/v1"

        # Verify fallback env var is set for OpenAI-compatible endpoints
        import os
        assert os.environ.get("OPENAI_API_KEY") == "sk-custom-12345"

        # Cleanup to avoid leaking into other tests
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_custom_endpoint_with_literal_api_key(self):
        """Custom endpoint should pass api_key in kwargs when using literal value."""
        # Create LLM config for custom endpoint with literal api_key
        llm_config = LLMConfig(
            models={
                "custom-model": ModelConfig(
                    model_name="custom-model-name",
                    model_url="https://custom-llm.example.com/v1",
                    api_key="sk-literal-custom-key"
                )
            }
        )

        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get model kwargs
        model_kwargs = caller._get_model_kwargs("custom-model")

        # Verify that api_key is in kwargs (critical for custom endpoints)
        assert "api_key" in model_kwargs
        assert model_kwargs["api_key"] == "sk-literal-custom-key"

        # Verify that api_base is set for custom endpoint
        assert "api_base" in model_kwargs
        assert model_kwargs["api_base"] == "https://custom-llm.example.com/v1"

    def test_openai_env_not_overwritten_if_same_value(self, monkeypatch):
        """OPENAI_API_KEY is left as-is when value matches."""
        # Pre-set env to a specific value
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-same")

        llm_config = LLMConfig(
            models={
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-openai-same",
                )
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)
        model_kwargs = caller._get_model_kwargs("openai-model")

        import os
        # Still should have correct key in kwargs
        assert model_kwargs["api_key"] == "sk-openai-same"
        # Env var should remain the same
        assert os.environ.get("OPENAI_API_KEY") == "sk-openai-same"

    def test_openai_env_overwritten_with_warning(self, monkeypatch, caplog):
        """OPENAI_API_KEY overwrite should occur with a warning when value differs."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-original")

        llm_config = LLMConfig(
            models={
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-openai-new",
                )
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        with caplog.at_level("WARNING"):
            model_kwargs = caller._get_model_kwargs("openai-model")

        import os
        # kwargs should use the new key
        assert model_kwargs["api_key"] == "sk-openai-new"
        # Env var should be overwritten to the new value
        assert os.environ.get("OPENAI_API_KEY") == "sk-openai-new"
        # A warning about overwriting should be logged
        assert any("Overwriting existing environment variable OPENAI_API_KEY" in rec.getMessage() for rec in caplog.records)

    def test_openai_and_custom_models_resolved_in_succession(self, monkeypatch):
        """Sequence of OpenAI then custom endpoint should keep last key in env while kwargs stay correct."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-preexisting")

        llm_config = LLMConfig(
            models={
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-openai-1",
                ),
                "custom-model": ModelConfig(
                    model_name="custom-model-name",
                    model_url="https://custom-llm.example.com/v1",
                    api_key="sk-custom-2",
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # First resolve OpenAI model
        openai_kwargs = caller._get_model_kwargs("openai-model")
        # Then resolve custom model
        custom_kwargs = caller._get_model_kwargs("custom-model")

        import os
        # kwargs should always reflect model-specific keys
        assert openai_kwargs["api_key"] == "sk-openai-1"
        assert custom_kwargs["api_key"] == "sk-custom-2"
        # Env var ends up with the last key used (custom model)
        assert os.environ.get("OPENAI_API_KEY") == "sk-custom-2"

    def test_custom_endpoint_with_extra_headers(self, monkeypatch):
        """Custom endpoint should handle extra_headers correctly."""
        monkeypatch.setenv("CUSTOM_API_KEY", "sk-custom-auth")
        monkeypatch.setenv("CUSTOM_TENANT", "tenant-123")

        # Create LLM config for custom endpoint with extra headers
        llm_config = LLMConfig(
            models={
                "custom-model": ModelConfig(
                    model_name="custom-model-name",
                    model_url="https://custom-llm.example.com/v1",
                    api_key="${CUSTOM_API_KEY}",
                    extra_headers={
                        "X-Tenant-ID": "${CUSTOM_TENANT}",
                        "X-Custom-Header": "custom-value"
                    }
                )
            }
        )

        # Create LiteLLMCaller
        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get model kwargs
        model_kwargs = caller._get_model_kwargs("custom-model")

        # Verify api_key is passed
        assert "api_key" in model_kwargs
        assert model_kwargs["api_key"] == "sk-custom-auth"

        # Verify extra_headers are resolved and passed
        assert "extra_headers" in model_kwargs
        assert model_kwargs["extra_headers"]["X-Tenant-ID"] == "tenant-123"
        assert model_kwargs["extra_headers"]["X-Custom-Header"] == "custom-value"

        # Verify api_base is set
        assert "api_base" in model_kwargs

    def test_known_providers_still_get_api_key_in_kwargs(self, monkeypatch):
        """Verify that known providers also get api_key in kwargs (backward compatibility)."""
        # Test OpenAI
        llm_config = LLMConfig(
            models={
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-openai-test"
                )
            }
        )
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        model_kwargs = caller._get_model_kwargs("openai-model")

        # OpenAI should get api_key in kwargs
        assert "api_key" in model_kwargs
        assert model_kwargs["api_key"] == "sk-openai-test"

        # cleanup any env var potentially set by implementation
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Test OpenRouter
        llm_config = LLMConfig(
            models={
                "openrouter-model": ModelConfig(
                    model_name="meta-llama/llama-3-70b",
                    model_url="https://openrouter.ai/api/v1",
                    api_key="sk-or-test"
                )
            }
        )
        caller = LiteLLMCaller(llm_config, debug_mode=True)
        model_kwargs = caller._get_model_kwargs("openrouter-model")

        # OpenRouter should get api_key in kwargs
        assert "api_key" in model_kwargs
        assert model_kwargs["api_key"] == "sk-or-test"

        # cleanup any env var potentially set by implementation
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
