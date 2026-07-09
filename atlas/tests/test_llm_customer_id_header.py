"""Unit tests for the LiteLLM customer-id header feature.

Tests that LiteLLMCaller sends the logged-in user as the
``x-litellm-customer-id`` header when a model is configured with
``pass_user_as_customer_id: true``.

Added: 2026-07-09
"""

import importlib
import sys
from unittest.mock import MagicMock

from atlas.modules.config.config_manager import ModelConfig


def _get_real_litellm_caller():
    """Return the real LiteLLMCaller class.

    Some test files (e.g. test_capability_tokens_and_injection.py) replace the
    litellm_caller module's class with a fake at import time. Force a reimport
    of the real module when that happens. Mirrors the helper in
    test_llm_env_expansion.py.
    """
    module_name = "atlas.modules.llm.litellm_caller"
    if module_name in sys.modules:
        old_module = sys.modules.pop(module_name)
        caller_class = getattr(old_module, "LiteLLMCaller", None)
        if caller_class is not None and hasattr(caller_class, "_get_model_kwargs"):
            sys.modules[module_name] = old_module
            return caller_class
    return importlib.import_module(module_name).LiteLLMCaller


LiteLLMCaller = _get_real_litellm_caller()


def _make_caller(models_dict):
    """Build a LiteLLMCaller with a mocked LLMConfig."""
    mock_config = MagicMock()
    mock_models = {}
    for name, overrides in models_dict.items():
        defaults = {
            "model_name": name,
            "model_url": "https://litellm.example.com/v1",
            "api_key": "sk-test",
            "api_key_source": "system",
        }
        defaults.update(overrides)
        mock_models[name] = ModelConfig(**defaults)
    mock_config.models = mock_models
    return LiteLLMCaller(llm_config=mock_config)


class TestCustomerIdHeader:
    """Test x-litellm-customer-id header injection in _get_model_kwargs."""

    def test_header_added_when_enabled_and_user_present(self):
        """The user email is sent as x-litellm-customer-id when enabled."""
        caller = _make_caller({
            "litellm-model": {"pass_user_as_customer_id": True},
        })
        kwargs = caller._get_model_kwargs("litellm-model", user_email="alice@example.com")
        assert kwargs["extra_headers"]["x-litellm-customer-id"] == "alice@example.com"

    def test_header_not_added_when_disabled(self):
        """No customer-id header when the model does not opt in (default)."""
        caller = _make_caller({
            "plain-model": {},
        })
        kwargs = caller._get_model_kwargs("plain-model", user_email="alice@example.com")
        assert "extra_headers" not in kwargs

    def test_header_skipped_when_no_user_email(self):
        """No customer-id header (and no error) when user_email is absent."""
        caller = _make_caller({
            "litellm-model": {"pass_user_as_customer_id": True},
        })
        kwargs = caller._get_model_kwargs("litellm-model", user_email=None)
        assert "extra_headers" not in kwargs

    def test_header_merges_with_existing_extra_headers(self):
        """The customer-id header is added alongside configured extra_headers."""
        caller = _make_caller({
            "litellm-model": {
                "pass_user_as_customer_id": True,
                "extra_headers": {"X-Title": "atlas"},
            },
        })
        kwargs = caller._get_model_kwargs("litellm-model", user_email="bob@example.com")
        assert kwargs["extra_headers"]["X-Title"] == "atlas"
        assert kwargs["extra_headers"]["x-litellm-customer-id"] == "bob@example.com"

    def test_config_field_defaults_false(self):
        """ModelConfig.pass_user_as_customer_id defaults to False."""
        cfg = ModelConfig(model_name="m", model_url="https://x/v1", api_key="k")
        assert cfg.pass_user_as_customer_id is False
