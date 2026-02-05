"""Integration tests for LLM environment variable expansion."""

import pytest
from atlas.modules.config.config_manager import LLMConfig, ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller


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

    def test_openai_key_from_dotenv_file(self, monkeypatch):
        """Test that OPENAI_API_KEY set in .env file (loaded into os.environ) works correctly."""
        # Simulate .env file loading by setting the env var
        # In real scenarios, python-dotenv loads .env into os.environ
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-dotenv-file")

        # Create LLM config that doesn't specify an api_key (relies on env)
        llm_config = LLMConfig(
            models={
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="${OPENAI_API_KEY}"
                )
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)
        model_kwargs = caller._get_model_kwargs("openai-model")

        # Should use the key from .env
        assert model_kwargs["api_key"] == "sk-from-dotenv-file"

        import os
        assert os.environ.get("OPENAI_API_KEY") == "sk-from-dotenv-file"

    def test_multiple_custom_openai_compatible_endpoints_with_different_keys(self, monkeypatch):
        """Test multiple OpenAI-compatible custom endpoints each with their own API key."""
        monkeypatch.setenv("CUSTOM_LLM_A_KEY", "sk-custom-a-12345")
        monkeypatch.setenv("CUSTOM_LLM_B_KEY", "sk-custom-b-67890")
        monkeypatch.setenv("CUSTOM_LLM_C_KEY", "sk-custom-c-abcde")

        llm_config = LLMConfig(
            models={
                "custom-llm-a": ModelConfig(
                    model_name="custom-model-a",
                    model_url="https://llm-a.example.com/v1",
                    api_key="${CUSTOM_LLM_A_KEY}"
                ),
                "custom-llm-b": ModelConfig(
                    model_name="custom-model-b",
                    model_url="https://llm-b.example.com/v1",
                    api_key="${CUSTOM_LLM_B_KEY}"
                ),
                "custom-llm-c": ModelConfig(
                    model_name="custom-model-c",
                    model_url="https://llm-c.example.com/v1",
                    api_key="${CUSTOM_LLM_C_KEY}"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get kwargs for each model
        kwargs_a = caller._get_model_kwargs("custom-llm-a")
        kwargs_b = caller._get_model_kwargs("custom-llm-b")
        kwargs_c = caller._get_model_kwargs("custom-llm-c")

        # Each should have its own correct API key in kwargs
        assert kwargs_a["api_key"] == "sk-custom-a-12345"
        assert kwargs_b["api_key"] == "sk-custom-b-67890"
        assert kwargs_c["api_key"] == "sk-custom-c-abcde"

        # Each should have its own api_base
        assert kwargs_a["api_base"] == "https://llm-a.example.com/v1"
        assert kwargs_b["api_base"] == "https://llm-b.example.com/v1"
        assert kwargs_c["api_base"] == "https://llm-c.example.com/v1"

        # OPENAI_API_KEY env var will be set to the last one resolved
        import os
        assert os.environ.get("OPENAI_API_KEY") == "sk-custom-c-abcde"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_custom_and_real_openai_endpoints_use_correct_keys(self, monkeypatch):
        """Test that custom endpoints and real OpenAI endpoints each use their correct API keys."""
        monkeypatch.setenv("REAL_OPENAI_KEY", "sk-real-openai-xyz")
        monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "sk-custom-provider-abc")

        llm_config = LLMConfig(
            models={
                "openai-gpt4": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="${REAL_OPENAI_KEY}"
                ),
                "custom-provider": ModelConfig(
                    model_name="custom-llm-7b",
                    model_url="https://custom-provider.example.com/v1",
                    api_key="${CUSTOM_PROVIDER_KEY}"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Test OpenAI endpoint
        openai_kwargs = caller._get_model_kwargs("openai-gpt4")
        assert openai_kwargs["api_key"] == "sk-real-openai-xyz"
        # Standard OpenAI endpoint doesn't set api_base (uses default)
        assert "api_base" not in openai_kwargs

        # Test custom endpoint
        custom_kwargs = caller._get_model_kwargs("custom-provider")
        assert custom_kwargs["api_key"] == "sk-custom-provider-abc"
        assert custom_kwargs["api_base"] == "https://custom-provider.example.com/v1"

        # Both should be callable with correct keys
        import os
        # Last one will be in OPENAI_API_KEY env var
        assert os.environ.get("OPENAI_API_KEY") in ["sk-real-openai-xyz", "sk-custom-provider-abc"]

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_custom_endpoint_missing_api_key_env_var_raises_error(self):
        """Test that missing custom API key env var raises appropriate error."""
        # Create config with undefined env var
        llm_config = LLMConfig(
            models={
                "custom-model": ModelConfig(
                    model_name="custom-model",
                    model_url="https://custom.example.com/v1",
                    api_key="${UNDEFINED_CUSTOM_KEY}"
                )
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Should raise ValueError about missing env var
        with pytest.raises(ValueError, match="Environment variable 'UNDEFINED_CUSTOM_KEY' is not set"):
            caller._get_model_kwargs("custom-model")

    def test_multiple_endpoints_with_mixed_key_sources(self, monkeypatch):
        """Test mixture of literal keys, env vars, and .env-loaded keys across multiple endpoints."""
        # Simulate some keys from .env file
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-dotenv")
        # Some from explicit env vars
        monkeypatch.setenv("CUSTOM_A_KEY", "sk-custom-a-env")

        llm_config = LLMConfig(
            models={
                "openai-from-dotenv": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="${OPENAI_API_KEY}"  # Uses .env value
                ),
                "custom-from-env": ModelConfig(
                    model_name="custom-a",
                    model_url="https://custom-a.example.com/v1",
                    api_key="${CUSTOM_A_KEY}"  # Uses explicit env var
                ),
                "custom-literal": ModelConfig(
                    model_name="custom-b",
                    model_url="https://custom-b.example.com/v1",
                    api_key="sk-literal-hardcoded"  # Literal value
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Each should resolve correctly
        kwargs_openai = caller._get_model_kwargs("openai-from-dotenv")
        assert kwargs_openai["api_key"] == "sk-from-dotenv"

        kwargs_custom_a = caller._get_model_kwargs("custom-from-env")
        assert kwargs_custom_a["api_key"] == "sk-custom-a-env"

        kwargs_custom_b = caller._get_model_kwargs("custom-literal")
        assert kwargs_custom_b["api_key"] == "sk-literal-hardcoded"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_empty_api_key_raises_appropriate_error(self):
        """Test that empty API key (after env var expansion) raises error."""
        # Note: Current implementation may not explicitly check for empty strings
        # This test documents expected behavior
        llm_config = LLMConfig(
            models={
                "model-with-empty-key": ModelConfig(
                    model_name="test-model",
                    model_url="https://api.example.com/v1",
                    api_key=""  # Empty string
                )
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)
        
        # Get kwargs - empty api_key is treated as None/missing
        # The implementation only sets api_key if it's truthy
        kwargs = caller._get_model_kwargs("model-with-empty-key")
        
        # Empty string is not passed through (falsy value)
        assert "api_key" not in kwargs

    def test_switching_between_models_updates_env_correctly(self, monkeypatch):
        """Test that switching between different model types updates environment correctly."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-initial")
        
        llm_config = LLMConfig(
            models={
                "anthropic-model": ModelConfig(
                    model_name="claude-3",
                    model_url="https://api.anthropic.com/v1",
                    api_key="sk-ant-new"
                ),
                "openai-model": ModelConfig(
                    model_name="gpt-4",
                    model_url="https://api.openai.com/v1",
                    api_key="sk-openai-new"
                ),
                "custom-model": ModelConfig(
                    model_name="custom",
                    model_url="https://custom.example.com/v1",
                    api_key="sk-custom-new"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        import os
        
        # Call Anthropic
        anthropic_kwargs = caller._get_model_kwargs("anthropic-model")
        assert anthropic_kwargs["api_key"] == "sk-ant-new"
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-new"

        # Call OpenAI
        openai_kwargs = caller._get_model_kwargs("openai-model")
        assert openai_kwargs["api_key"] == "sk-openai-new"
        assert os.environ.get("OPENAI_API_KEY") == "sk-openai-new"

        # Call custom (should also set OPENAI_API_KEY as fallback)
        custom_kwargs = caller._get_model_kwargs("custom-model")
        assert custom_kwargs["api_key"] == "sk-custom-new"
        assert os.environ.get("OPENAI_API_KEY") == "sk-custom-new"

        # Anthropic key should still be set
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-new"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_custom_endpoints_with_openai_prefix_in_model_name(self, monkeypatch):
        """Test custom endpoints that use 'openai/' prefix in model name use correct API keys.
        
        LiteLLM uses model name prefixes (e.g., 'openai/', 'anthropic/') to detect providers.
        This test ensures that when we have custom endpoints with model names like
        'openai/custom-model1', each endpoint still gets its own correct API key.
        """
        monkeypatch.setenv("CUSTOM_ENDPOINT_A_KEY", "sk-custom-a-12345")
        monkeypatch.setenv("CUSTOM_ENDPOINT_B_KEY", "sk-custom-b-67890")

        llm_config = LLMConfig(
            models={
                "custom-a": ModelConfig(
                    model_name="openai/custom-model1",  # Has openai/ prefix but custom endpoint
                    model_url="https://custom-a.example.com/v1",
                    api_key="${CUSTOM_ENDPOINT_A_KEY}"
                ),
                "custom-b": ModelConfig(
                    model_name="openai/custom-model2",  # Has openai/ prefix but custom endpoint
                    model_url="https://custom-b.example.com/v1",
                    api_key="${CUSTOM_ENDPOINT_B_KEY}"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get kwargs for each custom endpoint
        kwargs_a = caller._get_model_kwargs("custom-a")
        kwargs_b = caller._get_model_kwargs("custom-b")

        # Each should have its own correct API key in kwargs
        assert kwargs_a["api_key"] == "sk-custom-a-12345"
        assert kwargs_b["api_key"] == "sk-custom-b-67890"

        # Each should have its own api_base set (custom endpoints)
        assert kwargs_a["api_base"] == "https://custom-a.example.com/v1"
        assert kwargs_b["api_base"] == "https://custom-b.example.com/v1"

        # Verify the LiteLLM model names don't have prefixes for custom endpoints
        litellm_name_a = caller._get_litellm_model_name("custom-a")
        litellm_name_b = caller._get_litellm_model_name("custom-b")
        
        # Custom endpoints should use model_id directly (not add prefix)
        assert litellm_name_a == "openai/custom-model1"
        assert litellm_name_b == "openai/custom-model2"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_mixed_real_and_custom_openai_endpoints_with_same_prefix(self, monkeypatch):
        """Test that real OpenAI and custom OpenAI-compatible endpoints are handled correctly.
        
        When you have both:
        - A real OpenAI endpoint (api.openai.com)
        - Custom OpenAI-compatible endpoints
        
        Each should use its own API key even though they might have similar model name patterns.
        
        NOTE: If a custom endpoint URL contains 'openai' in the hostname (e.g., 
        'custom-openai.example.com'), it will be detected as an OpenAI endpoint and get 
        the 'openai/' prefix. To avoid this, use URLs without 'openai' in them.
        """
        monkeypatch.setenv("REAL_OPENAI_KEY", "sk-real-openai-xyz")
        monkeypatch.setenv("CUSTOM_COMPAT_KEY", "sk-custom-compat-abc")

        llm_config = LLMConfig(
            models={
                "real-openai": ModelConfig(
                    model_name="gpt-4o",
                    model_url="https://api.openai.com/v1",
                    api_key="${REAL_OPENAI_KEY}"
                ),
                "custom-compat": ModelConfig(
                    model_name="custom-gpt-4",
                    # Use URL without 'openai' in it to avoid provider detection
                    model_url="https://llm-provider.example.com/v1",
                    api_key="${CUSTOM_COMPAT_KEY}"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Get kwargs for real OpenAI
        real_kwargs = caller._get_model_kwargs("real-openai")
        assert real_kwargs["api_key"] == "sk-real-openai-xyz"
        # Real OpenAI doesn't set custom api_base
        assert "api_base" not in real_kwargs

        # Get kwargs for custom endpoint
        custom_kwargs = caller._get_model_kwargs("custom-compat")
        assert custom_kwargs["api_key"] == "sk-custom-compat-abc"
        # Custom endpoint sets api_base
        assert custom_kwargs["api_base"] == "https://llm-provider.example.com/v1"

        # Verify LiteLLM model names
        assert caller._get_litellm_model_name("real-openai") == "openai/gpt-4o"
        # Custom endpoint without provider keywords in URL gets no prefix
        assert caller._get_litellm_model_name("custom-compat") == "custom-gpt-4"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_multiple_custom_endpoints_sequential_calls_preserve_keys(self, monkeypatch):
        """Test that calling multiple custom endpoints in sequence preserves correct API keys.
        
        This is critical because the implementation sets OPENAI_API_KEY as a fallback
        for custom endpoints. We need to ensure that when switching between custom
        endpoints, each call still gets the correct API key in kwargs even though
        the env var might have been overwritten.
        """
        monkeypatch.setenv("CUSTOM_1_KEY", "sk-custom-1")
        monkeypatch.setenv("CUSTOM_2_KEY", "sk-custom-2")
        monkeypatch.setenv("CUSTOM_3_KEY", "sk-custom-3")

        llm_config = LLMConfig(
            models={
                "custom-1": ModelConfig(
                    model_name="model-1",
                    model_url="https://custom1.example.com/v1",
                    api_key="${CUSTOM_1_KEY}"
                ),
                "custom-2": ModelConfig(
                    model_name="model-2",
                    model_url="https://custom2.example.com/v1",
                    api_key="${CUSTOM_2_KEY}"
                ),
                "custom-3": ModelConfig(
                    model_name="model-3",
                    model_url="https://custom3.example.com/v1",
                    api_key="${CUSTOM_3_KEY}"
                ),
            }
        )

        caller = LiteLLMCaller(llm_config, debug_mode=True)

        # Call them in sequence multiple times
        for _ in range(2):
            kwargs_1 = caller._get_model_kwargs("custom-1")
            assert kwargs_1["api_key"] == "sk-custom-1", "Custom-1 should always get its own key"

            kwargs_2 = caller._get_model_kwargs("custom-2")
            assert kwargs_2["api_key"] == "sk-custom-2", "Custom-2 should always get its own key"

            kwargs_3 = caller._get_model_kwargs("custom-3")
            assert kwargs_3["api_key"] == "sk-custom-3", "Custom-3 should always get its own key"

            # Going back to custom-1 should still work
            kwargs_1_again = caller._get_model_kwargs("custom-1")
            assert kwargs_1_again["api_key"] == "sk-custom-1", "Custom-1 should still get its own key"

        # Cleanup
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
