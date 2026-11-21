"""llmconfig.yml model picker order and default model / default LiteLLM team.

Covers the new llmconfig.yml keys end to end on the backend:

- ``model_picker_order`` (``models_first`` | ``gateways_first``),
- top-level ``default_model`` (a static model or a gateway model key),
- per-gateway ``default_team`` / ``default_model``,

through config validation, the per-user resolver, the gateway summaries,
``/api/config`` + ``/api/config/shell``, ``GET /api/llm/default-model``, and
the headless ``AtlasClient`` / ``atlas-chat --list-models`` fallbacks.

Team discovery runs against the mock LiteLLM proxy (``mocks/litellm-mock``)
in-process. In the mock, ``test@test.com`` is in Alpha (gpt-4o-mini,
claude-sonnet) and Beta (llama-3.3-70b); ``bob@example.com`` is in Beta and
Gamma; ``alice@example.com`` is in Alpha only.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas.core.log_sanitizer import get_current_user
from atlas.modules.config.models import LLMConfig
from atlas.modules.llm import litellm_gateway_client
from atlas.modules.llm.default_model_resolver import (
    SOURCE_CONFIGURED,
    SOURCE_FIRST_MODEL,
    resolve_default_model,
)
from atlas.modules.llm.litellm_gateway_client import LiteLLMGatewayClient, reset_gateway_clients

_MOCK_MAIN = Path(__file__).resolve().parents[2] / "mocks" / "litellm-mock" / "main.py"
_spec = importlib.util.spec_from_file_location("litellm_mock_main_for_default_tests", _MOCK_MAIN)
litellm_mock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(litellm_mock)

ALPHA = "team-alpha-7f3a"
BETA = "team-beta-19c2"
GAMMA = "team-gamma-5d81"
STATIC = {"model_name": "gpt-4o", "model_url": "https://api.openai.com/v1", "api_key": "sk-static"}


def _gateway(**overrides):
    config = {"base_url": "http://litellm.mock", "api_key": litellm_mock.MASTER_KEY}
    config.update(overrides)
    return config


def _config(models=None, gateways=None, **top) -> LLMConfig:
    return LLMConfig(
        models={"static-a": STATIC, "static-b": STATIC} if models is None else models,
        litellm_gateways={"enterprise": _gateway()} if gateways is None else gateways,
        **top,
    )


def _install_mock_clients(llm_config: LLMConfig) -> None:
    """Route every gateway's discovery to the in-process mock proxy."""
    for name, gateway in llm_config.litellm_gateways.items():
        client = LiteLLMGatewayClient(name, gateway, transport=httpx.ASGITransport(app=litellm_mock.app))
        litellm_gateway_client._clients[name] = (gateway, client)


async def _everyone_in_groups(user, group):
    return True


@pytest.fixture(autouse=True)
def _fresh_gateway_clients():
    reset_gateway_clients()
    yield
    reset_gateway_clients()


# -- llmconfig.yml validation -------------------------------------------------


class TestConfigFields:
    def test_defaults_keep_previous_behavior(self):
        config = _config()
        assert config.model_picker_order == "models_first"
        assert config.default_model is None
        assert config.litellm_gateways["enterprise"].default_team is None
        assert config.configured_default_models() == []

    def test_gateways_first_is_accepted(self):
        assert _config(model_picker_order="gateways_first").model_picker_order == "gateways_first"

    def test_unknown_picker_order_is_rejected(self):
        with pytest.raises(ValidationError):
            _config(model_picker_order="alphabetical")

    def test_default_model_may_name_a_static_model(self):
        assert _config(default_model="static-b").default_model == "static-b"

    def test_default_model_may_name_a_gateway_key(self):
        key = f"enterprise::{ALPHA}::gpt-4o-mini"
        assert _config(default_model=key).default_model == key

    @pytest.mark.parametrize("name", ["typo-model", "nope::team::m", "enterprise::only-two", "  "])
    def test_default_model_must_name_something_configured(self, name):
        with pytest.raises(ValidationError):
            _config(default_model=name)

    def test_default_model_gateway_key_respects_the_allowlist(self):
        gateways = {"enterprise": _gateway(models=["gpt-4o-mini"])}
        _config(gateways=gateways, default_model=f"enterprise::{ALPHA}::gpt-4o-mini")
        with pytest.raises(ValidationError, match="neither a configured model"):
            _config(gateways=gateways, default_model=f"enterprise::{ALPHA}::claude-sonnet")

    def test_gateway_default_team_and_model(self):
        gateway = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        assert gateway.litellm_gateways["enterprise"].default_model_key("enterprise") == (
            f"enterprise::{ALPHA}::gpt-4o-mini"
        )

    def test_gateway_default_team_alone_preselects_without_a_model(self):
        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA)})
        assert config.litellm_gateways["enterprise"].default_model_key("enterprise") is None
        assert config.configured_default_models() == []

    def test_gateway_default_model_requires_a_team(self):
        with pytest.raises(ValidationError, match="default_model requires default_team"):
            _config(gateways={"enterprise": _gateway(default_model="gpt-4o-mini")})

    @pytest.mark.parametrize("team", ["", "a::b", ":lead"])
    def test_gateway_default_team_must_survive_a_model_key(self, team):
        with pytest.raises(ValidationError, match="default_team"):
            _config(gateways={"enterprise": _gateway(default_team=team)})

    def test_gateway_default_model_must_be_allowlisted(self):
        with pytest.raises(ValidationError, match="allowlist"):
            _config(gateways={"enterprise": _gateway(
                default_team=ALPHA, default_model="claude-sonnet", models=["gpt-4o-mini"],
            )})

    def test_surrounding_whitespace_is_stripped(self):
        config = _config(
            gateways={"enterprise": _gateway(default_team=f"  {ALPHA} ", default_model=" gpt-4o-mini\n")},
            default_model="  static-b ",
        )
        gateway = config.litellm_gateways["enterprise"]
        assert (gateway.default_team, gateway.default_model) == (ALPHA, "gpt-4o-mini")
        assert config.default_model == "static-b"
        assert config.configured_default_models() == ["static-b", f"enterprise::{ALPHA}::gpt-4o-mini"]

    @pytest.mark.parametrize("field", ["default_team", "default_model"])
    def test_blank_gateway_defaults_are_refused(self, field):
        values = {"default_team": ALPHA, "default_model": "gpt-4o-mini", field: "   "}
        with pytest.raises(ValidationError):
            _config(gateways={"enterprise": _gateway(**values)})

    def test_candidates_in_preference_order_without_duplicates(self):
        config = _config(
            gateways={
                "enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini"),
                "second": _gateway(default_team=BETA, default_model="llama-3.3-70b"),
            },
            default_model=f"enterprise::{ALPHA}::gpt-4o-mini",
        )
        assert config.configured_default_models() == [
            f"enterprise::{ALPHA}::gpt-4o-mini",
            f"second::{BETA}::llama-3.3-70b",
        ]

    def test_loads_from_yaml(self, tmp_path):
        from atlas.modules.config import ConfigManager

        path = tmp_path / "llmconfig.yml"
        path.write_text(
            "model_picker_order: gateways_first\n"
            f"default_model: enterprise::{ALPHA}::gpt-4o-mini\n"
            "models:\n"
            "  static-a: {model_name: gpt-4o, model_url: 'https://x', api_key: k}\n"
            "litellm_gateways:\n"
            "  enterprise:\n"
            "    base_url: http://litellm.mock\n"
            "    api_key: k\n"
            f"    default_team: {ALPHA}\n"
            "    default_model: gpt-4o-mini\n"
        )
        manager = ConfigManager()
        manager._search_paths = lambda _: [path]
        config = manager.llm_config
        assert config.model_picker_order == "gateways_first"
        assert config.default_model == f"enterprise::{ALPHA}::gpt-4o-mini"
        assert config.litellm_gateways["enterprise"].default_team == ALPHA


# -- per-user resolution -------------------------------------------------------


def _resolve(config, user, auth_check=_everyone_in_groups):
    _install_mock_clients(config)
    return asyncio.run(resolve_default_model(config, user, auth_check))


class TestResolveDefaultModel:
    def test_no_defaults_is_the_first_static_model(self):
        resolved = _resolve(_config(), "test@test.com")
        assert (resolved.model, resolved.source) == ("static-a", SOURCE_FIRST_MODEL)

    def test_static_default_model(self):
        resolved = _resolve(_config(default_model="static-b"), "test@test.com")
        assert (resolved.model, resolved.source) == ("static-b", SOURCE_CONFIGURED)

    def test_gateway_default_for_a_team_member(self):
        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="claude-sonnet")})
        resolved = _resolve(config, "alice@example.com")
        assert (resolved.model, resolved.source) == (f"enterprise::{ALPHA}::claude-sonnet", SOURCE_CONFIGURED)

    def test_gateway_default_beats_the_first_static_model(self):
        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        assert _resolve(config, "test@test.com").model == f"enterprise::{ALPHA}::gpt-4o-mini"

    def test_non_member_falls_back_to_the_first_static_model(self):
        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        resolved = _resolve(config, "bob@example.com")
        assert (resolved.model, resolved.source) == ("static-a", SOURCE_FIRST_MODEL)

    def test_model_the_team_does_not_offer_falls_back(self):
        # Beta only offers llama-3.3-70b.
        config = _config(gateways={"enterprise": _gateway(default_team=BETA, default_model="gpt-4o-mini")})
        assert _resolve(config, "test@test.com").model == "static-a"

    def test_falls_through_to_the_next_gateway_default(self):
        config = _config(gateways={
            "enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini"),
            "second": _gateway(default_team=GAMMA, default_model="gpt-4o-mini"),
        })
        assert _resolve(config, "bob@example.com").model == f"second::{GAMMA}::gpt-4o-mini"

    def test_top_level_default_beats_gateway_defaults(self):
        config = _config(
            gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")},
            default_model="static-b",
        )
        assert _resolve(config, "test@test.com").model == "static-b"

    def test_top_level_gateway_key_for_a_non_member_falls_back(self):
        config = _config(default_model=f"enterprise::{ALPHA}::gpt-4o-mini")
        assert _resolve(config, "bob@example.com").model == "static-a"

    def test_gateway_only_non_member_gets_nothing(self):
        config = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        resolved = _resolve(config, "bob@example.com")
        assert (resolved.model, resolved.source) == (None, None)

    def test_gateway_only_member_gets_the_default(self):
        config = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        assert _resolve(config, "test@test.com").model == f"enterprise::{ALPHA}::gpt-4o-mini"

    def test_unreachable_gateway_falls_back(self):
        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})

        def refuse(request):
            return httpx.Response(503, json={"error": {"message": "down"}})

        gateway = config.litellm_gateways["enterprise"]
        litellm_gateway_client._clients["enterprise"] = (
            gateway, LiteLLMGatewayClient("enterprise", gateway, transport=httpx.MockTransport(refuse)),
        )
        resolved = asyncio.run(resolve_default_model(config, "test@test.com", _everyone_in_groups))
        assert resolved.model == "static-a"

    def test_a_team_without_the_model_logs_a_warning(self, caplog):
        config = _config(gateways={"enterprise": _gateway(default_team=BETA, default_model="gpt-4o-mini")})
        with caplog.at_level("WARNING", logger="atlas.modules.llm.default_model_resolver"):
            assert _resolve(config, "test@test.com").model == "static-a"
        assert any(
            "does not offer model gpt-4o-mini" in record.getMessage() and record.levelname == "WARNING"
            for record in caplog.records
        )

    def test_nothing_usable_is_logged(self, caplog):
        config = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        with caplog.at_level("INFO", logger="atlas.modules.llm.default_model_resolver"):
            assert _resolve(config, "bob@example.com").model is None
        assert any("No configured or static default model is usable" in r.getMessage() for r in caplog.records)

    def test_gateway_defaults_are_probed_concurrently(self):
        import time as _time

        # The first gateway's default team is not the user's, so a sequential
        # probe would wait for it before trying the second.
        config = _config(gateways={
            "enterprise": _gateway(default_team=GAMMA, default_model="gpt-4o-mini"),
            "second": _gateway(default_team=ALPHA, default_model="gpt-4o-mini"),
        })
        delay = 0.4

        async def slow(request):
            await asyncio.sleep(delay)
            if request.url.path.endswith("team/list"):
                return httpx.Response(200, json=[{"team_id": ALPHA, "team_alias": "Alpha"}])
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

        for name, gateway in config.litellm_gateways.items():
            litellm_gateway_client._clients[name] = (
                gateway, LiteLLMGatewayClient(name, gateway, transport=httpx.MockTransport(slow)),
            )
        started = _time.monotonic()
        resolved = asyncio.run(resolve_default_model(config, "test@test.com", _everyone_in_groups))
        elapsed = _time.monotonic() - started
        assert resolved.model == f"second::{ALPHA}::gpt-4o-mini"
        # Sequential: 1 call (refused) + 2 calls = 3 * delay; concurrent: 2 * delay.
        assert elapsed < 2.6 * delay, elapsed

    def test_a_usable_static_default_asks_no_gateway(self):
        config = _config(
            gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")},
            default_model="static-b",
        )
        calls = []

        def record(request):
            calls.append(request.url.path)
            return httpx.Response(500)

        gateway = config.litellm_gateways["enterprise"]
        litellm_gateway_client._clients["enterprise"] = (
            gateway, LiteLLMGatewayClient("enterprise", gateway, transport=httpx.MockTransport(record)),
        )
        resolved = asyncio.run(resolve_default_model(config, "test@test.com", _everyone_in_groups))
        assert resolved.model == "static-b"
        assert calls == []

    def test_group_restricted_defaults_are_skipped(self):
        models = {"static-a": {**STATIC, "groups": ["admins"]}, "static-b": STATIC}
        config = _config(
            models=models,
            gateways={"enterprise": _gateway(groups=["admins"], default_team=ALPHA, default_model="gpt-4o-mini")},
            default_model="static-a",
        )

        async def admins_only(user, group):
            return user == "root@example.com"

        assert _resolve(config, "test@test.com", admins_only).model == "static-b"
        assert _resolve(config, "root@example.com", admins_only).model == "static-a"


# -- gateway summaries, /api/llm/default-model ---------------------------------------


class TestGatewaySummaries:
    @pytest.mark.asyncio
    async def test_summary_reports_the_default_team_and_model(self):
        from atlas.routes.litellm_gateway_routes import build_gateway_summaries

        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        settings = SimpleNamespace(feature_compliance_levels_enabled=False)
        with patch("atlas.core.model_access.is_user_in_group", AsyncMock(return_value=True)):
            (summary,) = await build_gateway_summaries(config, "test@test.com", settings)
        assert summary["default_team_id"] == ALPHA
        assert summary["default_model_id"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_summary_omits_unset_defaults(self):
        from atlas.routes.litellm_gateway_routes import build_gateway_summaries

        settings = SimpleNamespace(feature_compliance_levels_enabled=False)
        (summary,) = await build_gateway_summaries(_config(), "test@test.com", settings)
        assert "default_team_id" not in summary
        assert "default_model_id" not in summary

    @pytest.mark.asyncio
    async def test_picker_config_hides_a_default_the_user_may_not_use(self):
        from atlas.routes.litellm_gateway_routes import model_picker_config

        models = {"static-a": STATIC, "secret": {**STATIC, "groups": ["admins"]}}
        with patch("atlas.core.model_access.is_user_in_group", AsyncMock(return_value=False)):
            static = await model_picker_config(_config(models=models, default_model="secret"), "u@x")
            gateway = await model_picker_config(
                _config(
                    gateways={"enterprise": _gateway(groups=["admins"])},
                    default_model=f"enterprise::{ALPHA}::gpt-4o-mini",
                ),
                "u@x",
            )
        assert static == {"model_picker_order": "models_first", "default_model": None}
        assert gateway["default_model"] is None

    @pytest.mark.asyncio
    async def test_picker_config_tolerates_a_bare_config_double(self):
        from atlas.routes.litellm_gateway_routes import model_picker_config

        assert await model_picker_config(SimpleNamespace(models={}), "u@x") == {
            "model_picker_order": "models_first", "default_model": None,
        }


@pytest.fixture
def default_model_client():
    from atlas.routes import litellm_gateway_routes

    state = {"user": "test@test.com", "config": _config()}
    app = FastAPI()
    app.include_router(litellm_gateway_routes.default_model_router)
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def config_manager():
        _install_mock_clients(state["config"])
        return SimpleNamespace(llm_config=state["config"])

    with patch.object(litellm_gateway_routes.app_factory, "get_config_manager", side_effect=config_manager):
        yield TestClient(app), state


class TestDefaultModelRoute:
    def test_member_starts_on_the_gateway_default(self, default_model_client):
        client, state = default_model_client
        state["config"] = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        response = client.get("/api/llm/default-model")
        assert response.status_code == 200
        assert response.json() == {
            "model": f"enterprise::{ALPHA}::gpt-4o-mini", "source": "configured", "team_label": "Project Alpha",
        }

    def test_non_member_falls_back(self, default_model_client):
        client, state = default_model_client
        state["user"] = "bob@example.com"
        state["config"] = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        assert client.get("/api/llm/default-model").json() == {
            "model": "static-a", "source": "first_model", "team_label": None,
        }

    def test_nothing_usable(self, default_model_client):
        client, state = default_model_client
        state["user"] = "bob@example.com"
        state["config"] = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        assert client.get("/api/llm/default-model").json() == {"model": None, "source": None, "team_label": None}


# -- /api/config and /api/config/shell -------------------------------------------


@pytest.fixture
def live_config(monkeypatch):
    """Swap the live app's llm_config for one test (both config endpoints)."""
    from atlas.infrastructure.app_factory import app_factory

    loader = app_factory.get_config_manager()

    def use(config: LLMConfig):
        monkeypatch.setattr(loader, "_llm_config", config)
        return config

    return use


class TestConfigEndpoints:
    @pytest.mark.parametrize("path", ["/api/config", "/api/config/shell"])
    def test_reports_order_default_and_gateway_defaults(self, live_config, path):
        from main import app

        live_config(_config(
            gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")},
            model_picker_order="gateways_first",
            default_model=f"enterprise::{ALPHA}::gpt-4o-mini",
        ))
        response = TestClient(app).get(path, headers={"X-User-Email": "test@test.com"})
        assert response.status_code == 200
        body = response.json()
        assert body["model_picker_order"] == "gateways_first"
        assert body["default_model"] == f"enterprise::{ALPHA}::gpt-4o-mini"
        (gateway,) = body["llm_gateways"]
        assert gateway["default_team_id"] == ALPHA
        assert gateway["default_model_id"] == "gpt-4o-mini"
        # Static models keep their file order.
        assert [m["name"] for m in body["models"]] == ["static-a", "static-b"]

    @pytest.mark.parametrize("path", ["/api/config", "/api/config/shell"])
    def test_defaults_when_unset(self, live_config, path):
        from main import app

        live_config(_config())
        body = TestClient(app).get(path, headers={"X-User-Email": "test@test.com"}).json()
        assert body["model_picker_order"] == "models_first"
        assert body["default_model"] is None


# -- headless chat and the CLI ---------------------------------------------------


class TestHeadlessDefaults:
    def test_atlas_client_uses_the_gateway_default_for_a_member(self):
        from atlas.atlas_client import _default_model_name

        config = _config(gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        _install_mock_clients(config)
        assert asyncio.run(_default_model_name(config, "test@test.com")) == f"enterprise::{ALPHA}::gpt-4o-mini"
        assert asyncio.run(_default_model_name(config, "bob@example.com")) == "static-a"

    def test_atlas_client_uses_a_static_default_model(self):
        from atlas.atlas_client import _default_model_name

        assert asyncio.run(_default_model_name(_config(default_model="static-b"), "test@test.com")) == "static-b"

    def test_atlas_client_gateway_only_non_member_needs_an_explicit_model(self):
        from atlas.atlas_client import _default_model_name

        config = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        _install_mock_clients(config)
        with pytest.raises(ValueError, match="explicit model"):
            asyncio.run(_default_model_name(config, "bob@example.com"))

    def test_chat_resolves_the_default_for_its_user(self, monkeypatch):
        """chat() resolves the default for the identity it runs as."""
        from atlas.atlas_client import AtlasClient

        config = _config(models={}, gateways={"enterprise": _gateway(default_team=ALPHA, default_model="gpt-4o-mini")})
        _install_mock_clients(config)
        client = AtlasClient()

        async def _initialize():
            client._initialized = True
            client._factory = SimpleNamespace(
                get_config_manager=lambda: SimpleNamespace(
                    llm_config=config, app_settings=SimpleNamespace(test_user="bob@example.com"),
                )
            )

        monkeypatch.setattr(client, "initialize", _initialize)
        # bob is not in Alpha and there is no static model to fall back on.
        with pytest.raises(ValueError, match="gateway model"):
            asyncio.run(client.chat("hello"))

    def test_list_models_json_reports_the_defaults(self, monkeypatch, tmp_path, capsys):
        from atlas import atlas_chat_cli
        from atlas.modules.config import ConfigManager

        path = tmp_path / "llmconfig.yml"
        path.write_text(
            "model_picker_order: gateways_first\n"
            "default_model: static-a\n"
            "models:\n"
            "  static-a: {model_name: gpt-4o, model_url: 'https://x', api_key: k}\n"
            "litellm_gateways:\n"
            "  enterprise:\n"
            "    base_url: http://litellm.mock\n"
            "    api_key: k\n"
            f"    default_team: {ALPHA}\n"
            "    default_model: gpt-4o-mini\n"
        )

        def factory():
            manager = ConfigManager()
            manager._search_paths = lambda _: [path]
            return manager

        monkeypatch.setattr("atlas.modules.config.ConfigManager", factory)
        assert asyncio.run(atlas_chat_cli.list_models(json_output=True)) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["model_picker_order"] == "gateways_first"
        assert payload["default_model"] == "static-a"
        assert payload["litellm_gateways"][0]["default_team"] == ALPHA
        assert payload["litellm_gateways"][0]["default_model"] == "gpt-4o-mini"

        assert asyncio.run(atlas_chat_cli.list_models(json_output=False)) == 0
        text = capsys.readouterr().out
        assert f"default: enterprise::{ALPHA}::gpt-4o-mini" in text
        assert "Default model: static-a" in text
