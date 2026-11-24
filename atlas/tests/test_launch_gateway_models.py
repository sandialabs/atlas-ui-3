"""Launch discovery offers LiteLLM gateway team models (issue #1227).

``atlas_discover_subagent_options`` must list the models a user can chat with
-- the same set the main chat model picker builds from the gateway's team and
model lists -- and no more: group-restricted gateways, other users' teams,
models outside the admin allowlist, and models not approved for the turn's
compliance level stay out. Gateways are exercised against the in-process mock
LiteLLM proxy in ``mocks/litellm-mock``.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from atlas.application.chat.runs.launcher import (
    LaunchRefused,
    _require_discovered_option,
    discover_launch_options,
    execute_launch_discovery_tool,
    list_launch_options,
    resolve_model,
)
from atlas.domain.messages.models import ToolCall
from atlas.modules.config.models import LLMConfig
from atlas.modules.llm import litellm_gateway_client
from atlas.modules.llm.litellm_gateway_client import LiteLLMGatewayClient, reset_gateway_clients

_MOCK_MAIN = Path(__file__).resolve().parents[2] / "mocks" / "litellm-mock" / "main.py"
_spec = importlib.util.spec_from_file_location("litellm_mock_main_for_launch_tests", _MOCK_MAIN)
litellm_mock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(litellm_mock)

ALPHA = "team-alpha-7f3a"
BETA = "team-beta-19c2"
GAMMA = "team-gamma-5d81"
ALICE = "alice@example.com"
BOB = "bob@example.com"


def _gateway(**overrides):
    config = {
        "base_url": "http://litellm.mock",
        "api_key": litellm_mock.MASTER_KEY,
        "model_defaults": {"max_tokens": 256},
    }
    config.update(overrides)
    return config


def _llm_config(static=True, **gateway_overrides) -> LLMConfig:
    models = {}
    if static:
        models["static-model"] = {
            "model_name": "gpt-4o",
            "model_url": "https://api.openai.com/v1",
            "api_key": "sk-static",
            "allowed_data_classifications": ["Public"],
        }
    return LLMConfig(models=models, litellm_gateways={"enterprise": _gateway(**gateway_overrides)})


class _Workspaces:
    def list_workspaces(self, user_email):
        return [{"id": "ws-1", "name": "Research"}]


class _Factory:
    def __init__(self, llm_config, compliance=False):
        self.workspace_repository = _Workspaces()
        self._config_manager = SimpleNamespace(
            llm_config=llm_config,
            app_settings=SimpleNamespace(
                feature_atlas_launch_enabled=True,
                feature_chat_history_enabled=True,
                feature_agent_mode_available=True,
                feature_compliance_levels_enabled=compliance,
            ),
        )

    def get_config_manager(self):
        return self._config_manager


def _mock_client(name, config, transport=None):
    return LiteLLMGatewayClient(name, config, transport=transport or httpx.ASGITransport(app=litellm_mock.app))


@pytest.fixture(autouse=True)
def _mock_gateway():
    reset_gateway_clients()
    with patch.object(litellm_gateway_client, "get_gateway_client", _mock_client):
        yield
    reset_gateway_clients()


def _key(team, model):
    return f"enterprise::{team}::{model}"


def _names(options):
    return [item["name"] for item in options["models"]]


@pytest.mark.asyncio
async def test_discovery_lists_the_users_gateway_team_models_with_static_models():
    options = await list_launch_options(_Factory(_llm_config()), ALICE)

    assert _names(options) == sorted(
        [_key(ALPHA, "claude-sonnet"), _key(ALPHA, "gpt-4o-mini"), "static-model"]
    )


@pytest.mark.asyncio
async def test_discovery_never_offers_another_users_teams():
    options = await list_launch_options(_Factory(_llm_config()), BOB)

    names = _names(options)
    assert _key(BETA, "llama-3.3-70b") in names
    assert _key(GAMMA, "gpt-4o-mini") in names
    assert not any(ALPHA in name for name in names)


@pytest.mark.asyncio
async def test_gateway_only_deployment_is_launchable():
    """The #1134 refusal is gone: a gateway-only deployment discovers its
    team models and the discovered key passes the launch-time checks."""
    factory = _Factory(_llm_config(static=False))
    context = {"user_email": ALICE, "factory": factory, "launch_discovery": {}}

    result = await execute_launch_discovery_tool(ToolCall(id="d1", name="atlas_discover_subagent_options", arguments={}), context)

    assert result.success, result.content
    key = _key(ALPHA, "gpt-4o-mini")
    assert key in _names(json.loads(result.content))
    _require_discovered_option(context["launch_discovery"], "Research", key)
    assert await resolve_model(factory.get_config_manager(), key, ALICE) == key


@pytest.mark.asyncio
async def test_admin_allowlist_narrows_gateway_models():
    options = await list_launch_options(_Factory(_llm_config(models={"claude-sonnet": {}})), ALICE)

    assert _names(options) == [_key(ALPHA, "claude-sonnet"), "static-model"]


@pytest.mark.asyncio
async def test_group_restricted_gateway_is_skipped_for_outsiders():
    factory = _Factory(_llm_config(groups=["engineering"]))

    async def _not_member(email, group):
        return False

    with patch("atlas.application.chat.runs.launcher.is_user_in_group", _not_member):
        options = await list_launch_options(factory, ALICE)

    assert _names(options) == ["static-model"]


@pytest.mark.asyncio
async def test_unreachable_gateway_does_not_hide_other_models():
    def _failing_client(name, config):
        def _refuse(request):
            return httpx.Response(503, json={"error": "down"})

        return _mock_client(name, config, transport=httpx.MockTransport(_refuse))

    with patch.object(litellm_gateway_client, "get_gateway_client", _failing_client):
        options = await list_launch_options(_Factory(_llm_config()), ALICE)

    assert _names(options) == ["static-model"]


@pytest.mark.asyncio
async def test_discovery_applies_the_turns_compliance_level():
    """At a level, only models approved for it are offered -- gateway models
    by their per-model classification, as the picker filters them."""
    llm_config = _llm_config(
        allowed_data_classifications=["Internal"],
        models={"gpt-4o-mini": {}, "claude-sonnet": {"allowed_data_classifications": ["Public"]}},
    )
    factory = _Factory(llm_config, compliance=True)

    public = await discover_launch_options({"user_email": ALICE, "compliance_level": "Public"}, factory=factory)
    internal = await discover_launch_options({"user_email": ALICE, "compliance_level": "Internal"}, factory=factory)

    assert _names(public) == [_key(ALPHA, "claude-sonnet"), "static-model"]
    assert _names(internal) == [_key(ALPHA, "gpt-4o-mini")]


@pytest.mark.asyncio
async def test_undefined_compliance_level_fails_closed():
    factory = _Factory(_llm_config(allowed_data_classifications=["Internal"]), compliance=True)
    context = {"user_email": ALICE, "compliance_level": "NoSuchLevel"}

    with pytest.raises(LaunchRefused, match="no valid LLM models"):
        await discover_launch_options(context, factory=factory)


@pytest.mark.asyncio
async def test_compliance_level_is_ignored_when_levels_are_disabled():
    factory = _Factory(_llm_config(allowed_data_classifications=["Internal"]), compliance=False)

    options = await discover_launch_options({"user_email": ALICE, "compliance_level": "Public"}, factory=factory)

    assert _key(ALPHA, "gpt-4o-mini") in _names(options)
