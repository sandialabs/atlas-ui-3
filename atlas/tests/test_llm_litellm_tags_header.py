"""Unit tests for the LiteLLM request-tags header feature.

Tests that LiteLLMCaller sends ``x-litellm-tags`` (a comma-separated string,
per LiteLLM's request-tags convention) when a model is configured with
``litellm_tags``.

Added: 2026-10-14
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from atlas.modules.config.config_manager import ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller


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


class TestLiteLLMTagsHeader:
    """Test x-litellm-tags header injection in _get_model_kwargs."""

    def test_header_added_from_list(self):
        """A configured tag list is joined into the comma-separated header."""
        caller = _make_caller({
            "litellm-model": {"litellm_tags": ["atlas"]},
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["x-litellm-tags"] == "atlas"

    def test_multiple_tags_joined_with_commas(self):
        """Several tags become one comma-separated header value."""
        caller = _make_caller({
            "litellm-model": {"litellm_tags": ["atlas", "prod"]},
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["x-litellm-tags"] == "atlas,prod"

    def test_comma_separated_string_is_split(self):
        """A single string may itself carry a comma-separated list."""
        caller = _make_caller({
            "litellm-model": {"litellm_tags": "atlas, prod"},
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["x-litellm-tags"] == "atlas,prod"

    def test_header_not_added_when_unset(self):
        """No tags header when the model does not opt in (default)."""
        caller = _make_caller({"plain-model": {}})
        kwargs = caller._get_model_kwargs("plain-model")
        assert "extra_headers" not in kwargs

    def test_header_merges_with_existing_extra_headers(self):
        """The tags header is added alongside configured extra_headers."""
        caller = _make_caller({
            "litellm-model": {
                "litellm_tags": ["atlas"],
                "extra_headers": {"X-Title": "atlas"},
            },
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["X-Title"] == "atlas"
        assert kwargs["extra_headers"]["x-litellm-tags"] == "atlas"

    def test_explicit_extra_header_wins(self):
        """An explicitly-configured tags header is not overwritten."""
        caller = _make_caller({
            "litellm-model": {
                "litellm_tags": ["atlas"],
                "extra_headers": {"x-litellm-tags": "pinned"},
            },
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["x-litellm-tags"] == "pinned"

    def test_explicit_extra_header_wins_case_insensitive(self):
        """The explicit header wins even when its casing differs."""
        caller = _make_caller({
            "litellm-model": {
                "litellm_tags": ["atlas"],
                "extra_headers": {"X-LiteLLM-Tags": "pinned"},
            },
        })
        kwargs = caller._get_model_kwargs("litellm-model")
        assert kwargs["extra_headers"]["X-LiteLLM-Tags"] == "pinned"
        assert "x-litellm-tags" not in kwargs["extra_headers"]

    def test_tags_combine_with_customer_id_header(self):
        """Tags and the customer-id header can be sent together."""
        caller = _make_caller({
            "litellm-model": {
                "litellm_tags": ["atlas"],
                "pass_user_as_customer_id": True,
            },
        })
        kwargs = caller._get_model_kwargs("litellm-model", user_email="alice@example.com")
        assert kwargs["extra_headers"]["x-litellm-tags"] == "atlas"
        assert kwargs["extra_headers"]["x-litellm-customer-id"] == "alice@example.com"


class TestLiteLLMTagsConfig:
    """Test normalization and validation of the litellm_tags field."""

    def test_defaults_to_none(self):
        cfg = ModelConfig(model_name="m", model_url="https://x/v1", api_key="k")
        assert cfg.litellm_tags is None

    def test_string_is_normalized_to_list(self):
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k", litellm_tags="atlas,prod"
        )
        assert cfg.litellm_tags == ["atlas", "prod"]

    def test_whitespace_and_empty_entries_dropped(self):
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k",
            litellm_tags=[" atlas ", "", "prod"],
        )
        assert cfg.litellm_tags == ["atlas", "prod"]

    def test_list_entry_with_comma_is_split(self):
        """A list entry holding commas matches a comma-separated string value."""
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k",
            litellm_tags=["atlas,prod"],
        )
        assert cfg.litellm_tags == ["atlas", "prod"]

    def test_control_characters_rejected(self):
        """A tag with a control character is rejected at config load."""
        for bad in ["atlas\nprod", "atlas\rprod", "atlas\x00prod", "atlas\tprod"]:
            with pytest.raises(ValidationError):
                ModelConfig(
                    model_name="m", model_url="https://x/v1", api_key="k",
                    litellm_tags=[bad],
                )

    def test_non_ascii_rejected(self):
        """A non-ASCII tag would fail to encode into an HTTP header."""
        for bad in ["café", "atlas\u0085prod"]:
            with pytest.raises(ValidationError):
                ModelConfig(
                    model_name="m", model_url="https://x/v1", api_key="k",
                    litellm_tags=[bad],
                )

    def test_del_character_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(
                model_name="m", model_url="https://x/v1", api_key="k",
                litellm_tags=["atlas\x7fprod"],
            )

    def test_env_var_is_literal(self):
        """Tags are literal: ${ENV_VAR} is not expanded."""
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k",
            litellm_tags=["${ATLAS_TAG}"],
        )
        assert cfg.litellm_tags == ["${ATLAS_TAG}"]

    def test_empty_value_is_none(self):
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k", litellm_tags=""
        )
        assert cfg.litellm_tags is None

    def test_numeric_entry_coerced(self):
        """An unquoted YAML number is stringified instead of blocking startup."""
        cfg = ModelConfig(
            model_name="m", model_url="https://x/v1", api_key="k",
            litellm_tags=["atlas", 2026],
        )
        assert cfg.litellm_tags == ["atlas", "2026"]

    def test_bool_entry_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(
                model_name="m", model_url="https://x/v1", api_key="k",
                litellm_tags=["atlas", True],
            )

    def test_bad_type_rejected(self):
        with pytest.raises(ValidationError):
            ModelConfig(
                model_name="m", model_url="https://x/v1", api_key="k", litellm_tags=5
            )

    def test_conflict_with_explicit_header_warns_at_load(self, caplog):
        """The litellm_tags / explicit-header conflict is reported once, at load."""
        import logging

        with caplog.at_level(logging.WARNING, logger="atlas.modules.config.models"):
            ModelConfig(
                model_name="litellm-model", model_url="https://x/v1", api_key="k",
                litellm_tags=["atlas"],
                extra_headers={"x-litellm-tags": "pinned"},
            )
        assert "the explicit header wins" in caplog.text
        assert "litellm-model" in caplog.text
