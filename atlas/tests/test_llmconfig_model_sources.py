"""Regression tests for issue #1134: an Enterprise LiteLLM gateway may be the
sole model source in ``llmconfig.yml``.

Covers the four configuration shapes the issue names -- gateway-only,
static-only, mixed, and no valid source -- plus the line between
configuration schema errors (which must block startup) and runtime gateway
failures such as an unreachable or rejecting proxy (which must not).
"""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from atlas.atlas_client import _default_model_name
from atlas.domain.errors import (
    ConfigurationError,
    LLMAuthenticationError,
    LLMServiceError,
)
from atlas.modules.config.config_manager import ConfigManager
from atlas.modules.config.models import LLMConfig, lookup_model_config
from atlas.modules.llm.litellm_gateway_client import LiteLLMGatewayClient

GATEWAY = """
litellm_gateways:
  enterprise:
    base_url: https://litellm.example.gov
    api_key: "${LITELLM_KEY}"
    display_name: Enterprise LiteLLM
    description: Team-scoped models
"""

GATEWAY_ONLY_YAML = GATEWAY
EMPTY_MODELS_YAML = "models: {}\n" + GATEWAY
NULL_MODELS_YAML = "models:\n" + GATEWAY
STATIC_ONLY_YAML = """
models:
  static-model:
    model_name: openai/gpt-4o
    model_url: https://api.openai.com/v1
    api_key: "${OPENAI_API_KEY}"
"""
MIXED_YAML = STATIC_ONLY_YAML + GATEWAY

# A literal key, so the runtime tests below reach the HTTP layer instead of
# stopping at the unset-${ENV_VAR} credential check.
RUNTIME_GATEWAY_YAML = GATEWAY.replace('"${LITELLM_KEY}"', "sk-test")


def _cm_with_llmconfig(tmp_path, yaml_text):
    """A ConfigManager whose user config dir holds just the given llmconfig.yml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "llmconfig.yml").write_text(yaml_text, encoding="utf-8")
    cm = ConfigManager(atlas_root=tmp_path / "atlas")
    cm._app_settings = SimpleNamespace(
        app_config_dir=str(config_dir),
        llm_config_file="llmconfig.yml",
        mcp_config_file="mcp.json",
        rag_sources_config_file="rag-sources.json",
        file_extractors_config_file="file-extractors.json",
        hooks_config_file="hooks.json",
        model_fields_set={"app_config_dir"},
    )
    return cm


def _unreachable_transport() -> httpx.MockTransport:
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def _rejecting_transport(status: int) -> httpx.MockTransport:
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "no"}})

    return httpx.MockTransport(handler)


class TestGatewayOnlyConfig:
    """A LiteLLM gateway with no static models loads and validates."""

    @pytest.mark.parametrize(
        "yaml_text",
        [GATEWAY_ONLY_YAML, EMPTY_MODELS_YAML, NULL_MODELS_YAML],
        ids=["omitted", "explicit-empty", "null-key"],
    )
    def test_loads_with_zero_static_models(self, tmp_path, yaml_text):
        cm = _cm_with_llmconfig(tmp_path, yaml_text)
        config = cm.llm_config
        assert len(config.models) == 0
        assert list(config.litellm_gateways) == ["enterprise"]

    @pytest.mark.parametrize(
        "yaml_text",
        [GATEWAY_ONLY_YAML, EMPTY_MODELS_YAML, NULL_MODELS_YAML],
        ids=["omitted", "explicit-empty", "null-key"],
    )
    def test_passes_validate_config(self, tmp_path, yaml_text):
        cm = _cm_with_llmconfig(tmp_path, yaml_text)
        assert cm.validate_config()["llm_config"] is True

    def test_gateway_model_resolves_for_the_chat_path(self, tmp_path):
        """The first step of a chat request -- resolving the model key -- works
        with no static models present."""
        cm = _cm_with_llmconfig(tmp_path, GATEWAY_ONLY_YAML)
        model = lookup_model_config(cm.llm_config, "enterprise::team-alpha-7f3a::gpt-4o")
        assert model is not None
        assert model.model_name == "gpt-4o"
        assert model.model_url == "https://litellm.example.gov"

    def test_gateway_summaries_build_without_static_models(self, tmp_path):
        """``/api/config``'s gateway summaries need no static model."""
        from atlas.routes.litellm_gateway_routes import build_gateway_summaries

        cm = _cm_with_llmconfig(tmp_path, GATEWAY_ONLY_YAML)

        async def allowed(resource, user):
            return True

        async def run():
            with patch("atlas.routes.litellm_gateway_routes.is_model_allowed", allowed):
                return await build_gateway_summaries(
                    cm.llm_config, "u@example.com",
                    SimpleNamespace(feature_compliance_levels_enabled=False),
                )

        summaries = asyncio.run(run())
        assert [s["name"] for s in summaries] == ["enterprise"]
        assert summaries[0]["display_name"] == "Enterprise LiteLLM"


class TestOtherConfigShapes:
    """Static-only and mixed configs keep working; an empty one does not."""

    def test_static_only_is_valid(self, tmp_path):
        cm = _cm_with_llmconfig(tmp_path, STATIC_ONLY_YAML)
        config = cm.llm_config
        assert list(config.models) == ["static-model"]
        assert len(config.litellm_gateways) == 0
        assert cm.validate_config()["llm_config"] is True

    def test_mixed_is_valid(self, tmp_path):
        cm = _cm_with_llmconfig(tmp_path, MIXED_YAML)
        config = cm.llm_config
        assert list(config.models) == ["static-model"]
        assert list(config.litellm_gateways) == ["enterprise"]
        assert cm.validate_config()["llm_config"] is True

    def test_no_model_source_fails_validation(self, tmp_path, caplog):
        cm = _cm_with_llmconfig(tmp_path, "models: {}\n")
        with caplog.at_level("ERROR", logger="atlas.modules.config.config_loader"):
            status = cm.validate_config()
        assert status["llm_config"] is False
        assert any("no model source" in r.message for r in caplog.records)

    def test_missing_llmconfig_file_fails_validation(self, tmp_path):
        cm = _cm_with_llmconfig(tmp_path, "")
        (tmp_path / "config" / "llmconfig.yml").unlink()
        assert cm.validate_config()["llm_config"] is False

    def test_no_model_source_message_names_both_sections(self, tmp_path, caplog):
        """The message tells the operator which sections to add."""
        cm = _cm_with_llmconfig(tmp_path, "models: {}\n")
        with caplog.at_level("ERROR", logger="atlas.modules.config.config_loader"):
            cm.validate_config()
        joined = "\n".join(r.message for r in caplog.records)
        assert "'models'" in joined and "'litellm_gateways'" in joined


class TestSchemaErrorsStayDistinct:
    """A malformed file still blocks startup -- unchanged by the gateway work."""

    def test_bad_model_field_is_a_configuration_error(self, tmp_path):
        cm = _cm_with_llmconfig(
            tmp_path,
            "models:\n  bad-model:\n    max_tokens: not-a-number\n",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            cm.llm_config  # noqa: B018 -- property raises on access
        message = str(excinfo.value)
        assert "LLM configuration validation failed" in message
        assert "max_tokens" in message

    def test_non_scalar_error_input_is_redacted(self, tmp_path):
        cm = _cm_with_llmconfig(
            tmp_path,
            "models:\n  bad-model: [a, b]\n",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            cm.llm_config  # noqa: B018 -- property raises on access
        assert "<redacted>" in str(excinfo.value)

    def test_bad_gateway_name_is_a_configuration_error(self, tmp_path):
        cm = _cm_with_llmconfig(
            tmp_path,
            'litellm_gateways:\n  "bad::name":\n    base_url: https://x\n    api_key: k\n',
        )
        with pytest.raises(ConfigurationError):
            cm.llm_config  # noqa: B018 -- property raises on access

    def test_schema_error_also_fails_validate_config(self, tmp_path):
        cm = _cm_with_llmconfig(
            tmp_path,
            "models:\n  bad-model:\n    max_tokens: not-a-number\n",
        )
        assert cm.validate_config()["llm_config"] is False


class TestGatewayRuntimeFailuresAreNotConfigErrors:
    """An unreachable or rejecting gateway is a discovery-time failure, not a
    schema problem: the config still loads and validates, and the failure
    surfaces as its own error type from the gateway client."""

    def _gateway_only_client(self, tmp_path, transport):
        cm = _cm_with_llmconfig(tmp_path, RUNTIME_GATEWAY_YAML)
        config = cm.llm_config
        gateway = config.litellm_gateways["enterprise"]
        client = LiteLLMGatewayClient("enterprise", gateway, transport=transport)
        return cm, client

    def test_unreachable_gateway_still_validates(self, tmp_path):
        cm, _ = self._gateway_only_client(tmp_path, _unreachable_transport())
        assert cm.validate_config()["llm_config"] is True

    def test_unreachable_gateway_raises_a_service_error(self, tmp_path):
        _, client = self._gateway_only_client(tmp_path, _unreachable_transport())
        with pytest.raises(LLMServiceError):
            asyncio.run(client.list_teams("test@example.com"))

    def test_unauthorized_gateway_raises_an_auth_error_not_a_config_error(self, tmp_path):
        _, client = self._gateway_only_client(tmp_path, _rejecting_transport(401))
        with pytest.raises(LLMAuthenticationError):
            asyncio.run(client.list_teams("test@example.com"))


class TestDefaultModelSelection:
    """Headless chat() needs a nameable model: the first static model, or an
    explicit argument when a gateway is the only source."""

    def test_first_static_model_is_the_default(self):
        config = LLMConfig(
            models={
                "a": {"model_name": "openai/gpt-4o", "model_url": "https://x", "api_key": "k"},
                "b": {"model_name": "openai/gpt-4o", "model_url": "https://x", "api_key": "k"},
            }
        )
        assert _default_model_name(config) == "a"

    def test_gateway_only_has_no_default(self):
        config = LLMConfig(models={}, litellm_gateways={"enterprise": {
            "base_url": "https://litellm.example.gov", "api_key": "k",
        }})
        with pytest.raises(ValueError) as excinfo:
            _default_model_name(config)
        message = str(excinfo.value)
        assert "explicit" in message
        assert "<gateway>::<team_id>::<model_id>" in message

    def test_empty_config_has_no_default(self):
        with pytest.raises(ValueError):
            _default_model_name(LLMConfig(models={}))

    def test_headless_chat_reports_the_gateway_key_form(self, monkeypatch):
        """The error reaches a chat() caller, not just the helper's unit test."""
        from atlas.atlas_client import AtlasClient

        client = AtlasClient()
        llm_config = LLMConfig(models={}, litellm_gateways={"enterprise": {
            "base_url": "https://litellm.example.gov", "api_key": "k",
        }})

        async def _initialize():
            client._initialized = True
            client._factory = SimpleNamespace(
                get_config_manager=lambda: SimpleNamespace(llm_config=llm_config)
            )

        monkeypatch.setattr(client, "initialize", _initialize)
        with pytest.raises(ValueError, match="gateway model"):
            asyncio.run(client.chat("hello"))


class TestCliListModelsGatewayBranch:
    """Both ``list-models`` CLIs report gateways in every config shape, and
    the JSON shape no longer depends on which sources exist."""

    @staticmethod
    def _config_manager_factory(config_path):
        def _factory():
            manager = ConfigManager()
            manager._search_paths = lambda _: [config_path]
            return manager

        return _factory

    @staticmethod
    def _patch_chat_cli(monkeypatch, config_path):
        config_package = importlib.import_module("atlas.modules.config")
        chat_cli_list_models = importlib.import_module("atlas.atlas_chat_cli").list_models
        monkeypatch.setattr(
            config_package, "ConfigManager", TestCliListModelsGatewayBranch._config_manager_factory(config_path)
        )
        return chat_cli_list_models

    @staticmethod
    def _patch_config_cli(monkeypatch, config_path):
        config_cli = importlib.import_module("atlas.modules.config.cli")
        monkeypatch.setattr(
            config_cli, "ConfigManager", TestCliListModelsGatewayBranch._config_manager_factory(config_path)
        )
        return config_cli

    def test_atlas_chat_cli_gateway_only_lists_the_gateway(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(GATEWAY_ONLY_YAML, encoding="utf-8")
        chat_cli_list_models = self._patch_chat_cli(monkeypatch, config_path)

        code = asyncio.run(chat_cli_list_models(json_output=False))

        assert code == 0
        lines = capsys.readouterr().out.splitlines()
        assert "LiteLLM gateways (1):" in lines
        # Exact-line compares: no URL substring matching.
        assert "  enterprise: https://litellm.example.gov  (Team-scoped models)" in lines

    def test_atlas_chat_cli_mixed_lists_both_sections(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(MIXED_YAML, encoding="utf-8")
        chat_cli_list_models = self._patch_chat_cli(monkeypatch, config_path)

        code = asyncio.run(chat_cli_list_models(json_output=False))

        assert code == 0
        lines = capsys.readouterr().out.splitlines()
        assert any("static-model" in line for line in lines)
        assert "LiteLLM gateways (1):" in lines

    @pytest.mark.parametrize("yaml_text", [GATEWAY_ONLY_YAML, STATIC_ONLY_YAML])
    def test_atlas_chat_cli_json_always_has_both_keys(self, monkeypatch, tmp_path, capsys, yaml_text):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(yaml_text, encoding="utf-8")
        chat_cli_list_models = self._patch_chat_cli(monkeypatch, config_path)

        assert asyncio.run(chat_cli_list_models(json_output=True)) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"models", "litellm_gateways"}
        assert isinstance(payload["models"], list) and isinstance(payload["litellm_gateways"], list)

    def test_atlas_chat_cli_neither_source_exits_1(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text("models: {}\n", encoding="utf-8")
        chat_cli_list_models = self._patch_chat_cli(monkeypatch, config_path)

        assert asyncio.run(chat_cli_list_models(json_output=False)) == 1
        assert "No models configured" in capsys.readouterr().err

    def test_config_cli_gateway_only_lists_the_gateway(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(GATEWAY_ONLY_YAML, encoding="utf-8")
        config_cli = self._patch_config_cli(monkeypatch, config_path)

        config_cli.list_models(None)

        lines = capsys.readouterr().out.splitlines()
        assert "LiteLLM gateways (1):" in lines
        assert "[gateway] enterprise" in lines
        assert "No models configured" not in lines

    def test_config_cli_mixed_lists_both_sections(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text(MIXED_YAML, encoding="utf-8")
        config_cli = self._patch_config_cli(monkeypatch, config_path)

        config_cli.list_models(None)

        lines = capsys.readouterr().out.splitlines()
        assert any("static-model" in line for line in lines)
        assert "LiteLLM gateways (1):" in lines

    def test_config_cli_neither_source_still_reports_empty(self, monkeypatch, tmp_path, capsys):
        config_path = tmp_path / "llmconfig.yml"
        config_path.write_text("models: {}\n", encoding="utf-8")
        config_cli = self._patch_config_cli(monkeypatch, config_path)

        config_cli.list_models(None)

        assert "No models configured" in capsys.readouterr().out
