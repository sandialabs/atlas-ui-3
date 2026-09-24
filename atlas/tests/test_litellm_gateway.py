"""Team-scoped enterprise LiteLLM gateways.

Exercised against the mock LiteLLM proxy in ``mocks/litellm-mock``: in-process
through an ASGI transport for discovery and auth, and over a real socket for
the chat calls LiteLLM's SDK makes, so the team header is observed where the
proxy receives it.
"""

import base64
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.core.log_sanitizer import get_current_user
from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.domain.errors import AuthorizationError, LLMAuthenticationError
from atlas.modules.config.litellm_gateway_models import (
    build_gateway_model_key,
    parse_gateway_model_key,
)
from atlas.modules.config.models import LLMConfig
from atlas.modules.llm import litellm_gateway_client
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.litellm_gateway_client import LiteLLMGatewayClient, reset_gateway_clients

_MOCK_MAIN = Path(__file__).resolve().parents[2] / "mocks" / "litellm-mock" / "main.py"
_spec = importlib.util.spec_from_file_location("litellm_mock_main_for_tests", _MOCK_MAIN)
litellm_mock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(litellm_mock)

MASTER_KEY = litellm_mock.MASTER_KEY
ALPHA = "team-alpha-7f3a"
BETA = "team-beta-19c2"
GAMMA = "team-gamma-5d81"


def _gateway(**overrides):
    config = {
        "base_url": "http://litellm.mock",
        "display_name": "Enterprise LiteLLM",
        "api_key": MASTER_KEY,
        "model_defaults": {"max_tokens": 256, "supports_tools": False},
    }
    config.update(overrides)
    return config


def _llm_config(**gateway_overrides) -> LLMConfig:
    return LLMConfig(
        models={
            "static-model": {
                "model_name": "gpt-4o",
                "model_url": "https://api.openai.com/v1",
                "api_key": "sk-static",
            }
        },
        litellm_gateways={"enterprise": _gateway(**gateway_overrides)},
    )


def _mock_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=litellm_mock.app)


def _client(llm_config: LLMConfig) -> LiteLLMGatewayClient:
    return LiteLLMGatewayClient(
        "enterprise", llm_config.litellm_gateways["enterprise"], transport=_mock_transport()
    )


def _unsigned_jwt(claims) -> str:
    def encode(part):
        return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(claims)}.sig"


@pytest.fixture(autouse=True)
def _fresh_gateway_clients():
    reset_gateway_clients()
    litellm_mock._request_log.clear()
    yield
    reset_gateway_clients()


# -- model keys and configuration ------------------------------------------


class TestGatewayModelKeys:
    def test_round_trip_keeps_model_ids_with_separators(self):
        key = build_gateway_model_key("enterprise", ALPHA, "bedrock/anthropic.claude:v2")
        ref = parse_gateway_model_key(key)
        assert (ref.gateway, ref.team_id, ref.model_id) == (
            "enterprise", ALPHA, "bedrock/anthropic.claude:v2",
        )

    @pytest.mark.parametrize("team_id", ["a::b", "team:", ":team", ""])
    def test_rejects_team_ids_that_would_not_round_trip(self, team_id):
        with pytest.raises(ValueError):
            build_gateway_model_key("enterprise", team_id, "gpt-4o-mini")

    @pytest.mark.parametrize("name", ["gpt-4o", "a::b", "::team::model", "gw::::model"])
    def test_ordinary_names_are_not_gateway_keys(self, name):
        assert parse_gateway_model_key(name) is None

    def test_get_model_synthesizes_gateway_model(self):
        llm_config = _llm_config(groups=["engineering"], compliance_level="Internal")
        model = llm_config.get_model(f"enterprise::{ALPHA}::gpt-4o-mini")
        assert model.model_name == "gpt-4o-mini"
        assert model.model_url == "http://litellm.mock"
        # The key is supplied per call by the gateway client, never statically.
        assert model.api_key == ""
        assert model.groups == ["engineering"]
        assert model.compliance_level == "Internal"
        assert model.max_tokens == 256
        assert model.supports_tools is False

    def test_base_url_expands_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_GATEWAY_URL", "https://litellm.example.gov")
        llm_config = _llm_config(base_url="${TEST_GATEWAY_URL}")
        assert llm_config.get_model(f"enterprise::{ALPHA}::m").model_url == "https://litellm.example.gov"
        assert _client(llm_config)._url("team/list") == "https://litellm.example.gov/team/list"

    def test_get_model_ignores_unknown_gateway(self):
        assert _llm_config().get_model(f"other::{ALPHA}::gpt-4o-mini") is None
        assert _llm_config().get_model("static-model").api_key == "sk-static"

    def test_delegated_gateway_carries_no_static_key(self):
        llm_config = _llm_config(auth_type="delegated", delegation={"scope": "api://litellm/.default"})
        assert llm_config.get_model(f"enterprise::{ALPHA}::gpt-4o-mini").api_key == ""

    def test_system_gateway_requires_a_key(self):
        # An empty key would let LiteLLM fall back to the server's OPENAI_API_KEY.
        with pytest.raises(ValueError, match="requires api_key"):
            _llm_config(api_key="")

    def test_unset_base_url_env_makes_models_unknown(self, monkeypatch):
        monkeypatch.delenv("UNSET_GATEWAY_URL_FOR_TEST", raising=False)
        llm_config = _llm_config(base_url="${UNSET_GATEWAY_URL_FOR_TEST}")
        assert llm_config.get_model(f"enterprise::{ALPHA}::m") is None

    def test_delegated_gateway_requires_a_target(self):
        with pytest.raises(ValueError, match="delegation.scope"):
            _llm_config(auth_type="delegated")

    def test_model_defaults_may_not_override_identity(self):
        with pytest.raises(ValueError, match="model_defaults may not set api_key"):
            _llm_config(model_defaults={"api_key": "sk-other"})

    def test_model_defaults_are_validated_at_load(self):
        with pytest.raises(ValueError):
            _llm_config(model_defaults={"reasoning_effort": "extreme"})

    def test_static_model_name_may_not_shadow_gateway_keys(self):
        with pytest.raises(ValueError, match="collide"):
            LLMConfig(
                models={"enterprise::x::y": {"model_name": "y", "model_url": "http://x"}},
                litellm_gateways={"enterprise": _gateway()},
            )


# -- discovery against the mock proxy ---------------------------------------


class TestGatewayDiscovery:
    @pytest.mark.asyncio
    async def test_lists_only_the_users_teams(self):
        teams = await _client(_llm_config()).list_teams("test@test.com")
        assert [(t.team_id, t.label) for t in teams] == [
            (ALPHA, "Project Alpha"),
            (BETA, "Project Beta"),
        ]

    @pytest.mark.asyncio
    async def test_lists_team_models(self):
        client = _client(_llm_config())
        assert await client.list_models("test@test.com", ALPHA) == ["gpt-4o-mini", "claude-sonnet"]
        assert await client.list_models("test@test.com", BETA) == ["llama-3.3-70b"]

    @pytest.mark.asyncio
    async def test_refuses_a_team_the_user_is_not_in(self):
        with pytest.raises(AuthorizationError):
            await _client(_llm_config()).list_models("test@test.com", GAMMA)

    @pytest.mark.asyncio
    async def test_strips_configured_suffix_from_user_id(self):
        client = _client(_llm_config(user_id_strip_suffix="@corp.example"))
        credential = await client.get_credential("alice@corp.example")
        assert credential.litellm_user_id == "alice"

    @pytest.mark.asyncio
    async def test_team_list_is_cached(self):
        calls = []
        transport = _mock_transport()
        original = transport.handle_async_request

        async def counting(request):
            calls.append(request.url.path)
            return await original(request)

        transport.handle_async_request = counting
        client = LiteLLMGatewayClient(
            "enterprise", _llm_config().litellm_gateways["enterprise"], transport=transport
        )
        await client.list_teams("test@test.com")
        await client.list_teams("test@test.com")
        assert calls == ["/team/list"]

    @pytest.mark.asyncio
    async def test_refresh_rechecks_membership_but_is_throttled(self):
        now = [1000.0]
        client = LiteLLMGatewayClient(
            "enterprise", _llm_config().litellm_gateways["enterprise"],
            transport=_mock_transport(), clock=lambda: now[0],
        )
        stale = [litellm_gateway_client.GatewayTeam(team_id=GAMMA, label="stale")]
        client._team_cache["test@test.com"] = (now[0], stale)
        # A refresh right after the cache filled is served from the cache...
        assert await client.list_teams("test@test.com", refresh=True) == stale
        # ...but once the throttle window passes it reaches LiteLLM, so a
        # revoked membership is seen.
        now[0] += litellm_gateway_client.FORCED_REFRESH_MIN_INTERVAL_SECONDS + 1
        assert await client.list_models("test@test.com", ALPHA, refresh=True)
        with pytest.raises(AuthorizationError):
            await client.list_models("test@test.com", GAMMA)

    @pytest.mark.asyncio
    async def test_rejected_service_key_is_an_authentication_error(self):
        with pytest.raises(LLMAuthenticationError):
            await _client(_llm_config(api_key="sk-wrong")).list_teams("test@test.com")

    @pytest.mark.asyncio
    async def test_missing_env_key_is_an_authentication_error(self, monkeypatch):
        monkeypatch.delenv("UNSET_LITELLM_KEY_FOR_TEST", raising=False)
        with pytest.raises(LLMAuthenticationError):
            await _client(_llm_config(api_key="${UNSET_LITELLM_KEY_FOR_TEST}")).list_teams("test@test.com")

    @pytest.mark.asyncio
    async def test_delegated_token_identifies_user_by_oid(self):
        oid = litellm_mock.USERS["bob@example.com"]
        delegated = _unsigned_jwt({"oid": oid, "aud": "litellm"})
        manager = AsyncMock()
        manager.get_token.return_value = type("Token", (), {"access_token": delegated})()
        llm_config = _llm_config(
            auth_type="delegated", delegation={"scope": "https://litellm.example/user_impersonation"}
        )
        with patch(
            "atlas.core.oidc.delegation.get_delegation_manager_async",
            AsyncMock(return_value=manager),
        ), patch(
            "atlas.core.oidc.mcp_delegation.resolve_subject_token",
            AsyncMock(return_value="user-login-token"),
        ):
            teams = await _client(llm_config).list_teams("bob@example.com")

        assert [t.team_id for t in teams] == [BETA, GAMMA]
        request = manager.get_token.await_args.args[0]
        assert request.subject_token == "user-login-token"
        assert request.scope == "https://litellm.example/user_impersonation"

    @pytest.mark.asyncio
    async def test_delegated_without_oidc_session_asks_user_to_sign_in(self):
        llm_config = _llm_config(auth_type="delegated", delegation={"scope": "s"})
        with patch(
            "atlas.core.oidc.delegation.get_delegation_manager_async",
            AsyncMock(return_value=AsyncMock()),
        ), patch(
            "atlas.core.oidc.mcp_delegation.resolve_subject_token",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(LLMAuthenticationError, match="sign in again"):
                await _client(llm_config).list_teams("bob@example.com")


# -- access control -----------------------------------------------------------


class TestGatewayAccessControl:
    @pytest.mark.asyncio
    async def test_gateway_groups_apply_to_its_models(self):
        llm_config = _llm_config(groups=["engineering"])
        key = f"enterprise::{ALPHA}::gpt-4o-mini"

        async def member_of(user, group):
            return user == "eng@example.com"

        allowed = await check_model_access(llm_config, key, "eng@example.com", auth_check_func=member_of)
        denied = await check_model_access(llm_config, key, "other@example.com", auth_check_func=member_of)
        assert allowed is ModelAccessDecision.ALLOWED
        assert denied is ModelAccessDecision.DENIED


# -- LLM calls ------------------------------------------------------------------


def _caller_with_mock_transport(llm_config: LLMConfig) -> LiteLLMCaller:
    """A caller whose gateway client talks to the in-process mock."""
    litellm_gateway_client._clients["enterprise"] = (
        llm_config.litellm_gateways["enterprise"],
        _client(llm_config),
    )
    return LiteLLMCaller(llm_config=llm_config)


class TestGatewayCallTarget:
    @pytest.mark.asyncio
    async def test_adds_team_header_and_key(self):
        caller = _caller_with_mock_transport(_llm_config(extra_headers={"X-Title": "atlas"}))
        model, kwargs = await caller._resolve_call_target(
            f"enterprise::{ALPHA}::gpt-4o-mini", None, "test@test.com"
        )
        assert model == "openai/gpt-4o-mini"
        assert kwargs["api_base"] == "http://litellm.mock"
        assert kwargs["api_key"] == MASTER_KEY
        assert kwargs["extra_headers"] == {"X-Title": "atlas", "x-litellm-team-id": ALPHA}
        assert kwargs["max_tokens"] == 256

    @pytest.mark.asyncio
    async def test_api_base_is_the_gateway_even_if_it_names_a_provider(self):
        llm_config = _llm_config(base_url="https://openrouter-proxy.internal.example")
        caller = _caller_with_mock_transport(llm_config)
        _, kwargs = await caller._resolve_call_target(
            f"enterprise::{ALPHA}::gpt-4o-mini", None, "test@test.com"
        )
        assert kwargs["api_base"] == "https://openrouter-proxy.internal.example"

    @pytest.mark.asyncio
    async def test_configured_team_header_name_is_used(self):
        caller = _caller_with_mock_transport(_llm_config(team_header="x-team"))
        _, kwargs = await caller._resolve_call_target(
            f"enterprise::{BETA}::llama-3.3-70b", None, "test@test.com"
        )
        assert kwargs["extra_headers"] == {"x-team": BETA}

    @pytest.mark.asyncio
    async def test_crafted_key_for_another_team_is_refused(self):
        caller = _caller_with_mock_transport(_llm_config())
        with pytest.raises(AuthorizationError):
            await caller._resolve_call_target(f"enterprise::{GAMMA}::gpt-4o-mini", None, "test@test.com")

    @pytest.mark.asyncio
    async def test_crafted_key_for_a_model_outside_the_team_is_refused(self):
        # Project Alpha is a team the user belongs to, but llama-3.3-70b is
        # only on Project Beta. The check must hold even if the proxy would
        # not enforce the team's allowlist for a service key.
        caller = _caller_with_mock_transport(_llm_config())
        with pytest.raises(AuthorizationError) as exc_info:
            await caller._resolve_call_target(f"enterprise::{ALPHA}::llama-3.3-70b", None, "test@test.com")
        assert exc_info.value.code == "LLM_TEAM_MODEL_DENIED"

    @pytest.mark.asyncio
    async def test_tools_path_keeps_the_team_error(self):
        from atlas.application.chat.utilities.error_handler import safe_call_llm_with_tools

        caller = _caller_with_mock_transport(_llm_config())
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
        with pytest.raises(AuthorizationError) as exc_info:
            await safe_call_llm_with_tools(
                caller, f"enterprise::{GAMMA}::gpt-4o-mini",
                [{"role": "user", "content": "hi"}], tools, user_email="test@test.com",
            )
        assert exc_info.value.code == "LLM_TEAM_ACCESS_DENIED"

    @pytest.mark.asyncio
    async def test_static_models_are_untouched(self):
        caller = _caller_with_mock_transport(_llm_config())
        model, kwargs = await caller._resolve_call_target("static-model", None, "test@test.com")
        assert model == "openai/gpt-4o"
        assert "extra_headers" not in kwargs


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_mock_url():
    """The mock proxy on a real socket, for LiteLLM's own HTTP client."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(litellm_mock.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "mock LiteLLM proxy did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


class TestGatewayChatAgainstMock:
    @pytest.mark.asyncio
    async def test_plain_call_sends_the_selected_team(self, live_mock_url):
        caller = LiteLLMCaller(llm_config=_llm_config(base_url=live_mock_url))
        content = await caller.call_plain(
            f"enterprise::{BETA}::llama-3.3-70b",
            [{"role": "user", "content": "hello team"}],
            user_email="test@test.com",
        )
        assert content.startswith("[Project Beta / llama-3.3-70b]")
        last = litellm_mock._request_log[-1]
        assert last["team_header"] == BETA
        assert last["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_streaming_call_sends_the_selected_team(self, live_mock_url):
        caller = LiteLLMCaller(llm_config=_llm_config(base_url=live_mock_url))
        chunks = [
            chunk
            async for chunk in caller.stream_plain(
                f"enterprise::{ALPHA}::claude-sonnet",
                [{"role": "user", "content": "stream please"}],
                user_email="alice@example.com",
            )
        ]
        text = "".join(c for c in chunks if isinstance(c, str))
        assert text.startswith("[Project Alpha / claude-sonnet]")
        assert litellm_mock._request_log[-1]["team_header"] == ALPHA
        assert litellm_mock._request_log[-1]["stream"] is True


# -- routes -----------------------------------------------------------------------


@pytest.fixture
def routes_client():
    from atlas.routes import litellm_gateway_routes

    llm_config = _llm_config(groups=["engineering"])
    litellm_gateway_client._clients["enterprise"] = (
        llm_config.litellm_gateways["enterprise"],
        _client(llm_config),
    )
    config_manager = type("CM", (), {"llm_config": llm_config})()
    app = FastAPI()
    app.include_router(litellm_gateway_routes.router)
    state = {"user": "test@test.com"}
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    async def member_of(user, group):
        return user != "outsider@example.com"

    with patch.object(litellm_gateway_routes.app_factory, "get_config_manager", return_value=config_manager), \
            patch("atlas.core.model_access.is_user_in_group", member_of):
        yield TestClient(app), state


class TestGatewayRoutes:
    def test_teams(self, routes_client):
        client, _ = routes_client
        response = client.get("/api/llm/gateways/enterprise/teams")
        assert response.status_code == 200
        assert response.json()["teams"] == [
            {"team_id": ALPHA, "label": "Project Alpha"},
            {"team_id": BETA, "label": "Project Beta"},
        ]

    def test_models_are_selectable_keys(self, routes_client):
        client, _ = routes_client
        response = client.get("/api/llm/gateways/enterprise/models", params={"team_id": ALPHA})
        assert response.status_code == 200
        body = response.json()
        assert body["team_label"] == "Project Alpha"
        assert [m["name"] for m in body["models"]] == [
            f"enterprise::{ALPHA}::gpt-4o-mini",
            f"enterprise::{ALPHA}::claude-sonnet",
        ]

    def test_non_member_team_is_forbidden(self, routes_client):
        client, _ = routes_client
        response = client.get("/api/llm/gateways/enterprise/models", params={"team_id": GAMMA})
        assert response.status_code == 403

    def test_team_id_with_key_separator_is_rejected(self, routes_client):
        client, _ = routes_client
        response = client.get("/api/llm/gateways/enterprise/models", params={"team_id": "a::b"})
        assert response.status_code == 400

    def test_unknown_and_restricted_gateways_look_the_same(self, routes_client):
        client, state = routes_client
        assert client.get("/api/llm/gateways/nope/teams").status_code == 404
        state["user"] = "outsider@example.com"
        assert client.get("/api/llm/gateways/enterprise/teams").status_code == 404


class TestGatewayComplianceNormalization:
    def test_gateway_level_is_canonicalized_at_load(self):
        from atlas.modules.config import config_loader

        loader = object.__new__(config_loader.ConfigManager)
        loader._llm_config = _llm_config(compliance_level="internal-alias")

        class Levels:
            def validate_compliance_level(self, level, context=""):
                return {"internal-alias": "Internal"}.get(level)

        with patch("atlas.core.compliance.get_compliance_manager", return_value=Levels()):
            loader._validate_llm_compliance_levels()
        assert loader._llm_config.litellm_gateways["enterprise"].compliance_level == "Internal"
