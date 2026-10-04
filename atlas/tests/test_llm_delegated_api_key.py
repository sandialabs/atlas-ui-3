"""Delegated API keys for LLM models (api_key_source: "delegated").

A delegated model is called with a token Atlas obtained for the logged-in user
by the configured delegation provider, never with the user's own inbound token
and never with some other key: every failure to obtain one must fail the call.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from atlas.core.oidc.delegation import DelegatedToken, DelegationError
from atlas.core.oidc.session import get_session_store
from atlas.domain.errors import LLMAuthenticationError
from atlas.modules.config.models import LiteLLMGatewayDelegation, LLMConfig, ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.llm.litellm_gateway_client import mint_delegated_llm_token

GATEWAY_URL = "https://llm-gateway.example.gov/v1"
DELEGATION = {"audience": "llm-api"}
USER = "user@example.gov"


def _model(name, **overrides):
    fields = {
        "model_name": name,
        "model_url": GATEWAY_URL,
        "api_key_source": "delegated",
        "delegation": DELEGATION,
    }
    fields.update(overrides)
    return ModelConfig(**fields)


def _caller(*models):
    return LiteLLMCaller(llm_config=LLMConfig(models={m.model_name: m for m in models}))


@pytest.fixture
def user_session():
    store = get_session_store()
    store.clear()
    store.create(user_id=USER, access_token="USER-INBOUND-TOKEN")
    yield store
    store.clear()


class _StubManager:
    def __init__(self, token=None, error=None):
        self.token = token
        self.error = error
        self.requests = []

    async def get_token(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.token


def _patch_manager(manager):
    return patch(
        "atlas.core.oidc.delegation.get_delegation_manager_async",
        AsyncMock(return_value=manager),
    )


def _minted(access_token="DELEGATED"):
    return _StubManager(DelegatedToken(access_token=access_token, expires_at=time.time() + 300))


class TestModelConfigDelegation:
    def test_audience_is_enough(self):
        assert _model("gw").delegation.audience == "llm-api"

    def test_scope_alone_is_enough_for_entra_obo(self):
        model = _model("gw", delegation={"scope": "api://llm-gateway/llm.invoke"})
        assert model.delegation.scope == "api://llm-gateway/llm.invoke"

    def test_resource_is_enough(self):
        assert _model("gw", delegation={"resource": GATEWAY_URL}).delegation.resource == GATEWAY_URL

    @pytest.mark.parametrize("delegation", [None, {}])
    def test_delegated_model_without_a_target_is_rejected(self, delegation):
        with pytest.raises(ValidationError, match="requires delegation.scope"):
            _model("gw", delegation=delegation)

    def test_system_model_needs_no_delegation(self):
        assert ModelConfig(model_name="sys", model_url=GATEWAY_URL, api_key="k").delegation is None


class TestMintDelegatedLlmToken:
    async def _mint(self, user=USER):
        return await mint_delegated_llm_token(
            user, LiteLLMGatewayDelegation(audience="llm-api", scope="llm.invoke"),
            endpoint="Model 'gw'", actor="llm:gw",
        )

    @pytest.mark.asyncio
    async def test_exchanges_the_session_token(self, user_session):
        manager = _minted()
        with _patch_manager(manager):
            token = await self._mint()

        assert token == "DELEGATED"
        request = manager.requests[0]
        assert (request.subject_token, request.audience, request.scope, request.actor) == (
            "USER-INBOUND-TOKEN", "llm-api", "llm.invoke", "llm:gw",
        )

    @pytest.mark.asyncio
    async def test_requires_a_user(self, user_session):
        with pytest.raises(LLMAuthenticationError, match="needs a signed-in user"):
            await self._mint(user=None)

    @pytest.mark.asyncio
    async def test_requires_delegation_to_be_configured(self, user_session):
        with _patch_manager(None), pytest.raises(LLMAuthenticationError, match="not configured"):
            await self._mint()

    @pytest.mark.asyncio
    async def test_requires_an_oidc_session(self):
        get_session_store().clear()
        manager = _minted()
        with _patch_manager(manager), pytest.raises(LLMAuthenticationError, match="Please sign in again"):
            await self._mint()
        assert manager.requests == []

    @pytest.mark.asyncio
    async def test_a_failed_exchange_is_an_error_not_a_fallback(self, user_session):
        manager = _StubManager(error=DelegationError("Delegation endpoint returned 400 (invalid_request)"))
        with _patch_manager(manager), pytest.raises(LLMAuthenticationError, match="Could not obtain a token"):
            await self._mint()


class TestCallTarget:
    @pytest.mark.asyncio
    async def test_a_delegated_model_is_called_with_the_minted_token(self, user_session):
        caller = _caller(_model("gw"))
        manager = _minted()
        with _patch_manager(manager):
            _, kwargs = await caller._resolve_call_target("gw", None, USER)

        assert kwargs["api_key"] == "DELEGATED"
        assert kwargs["api_base"] == GATEWAY_URL
        assert manager.requests[0].actor == "llm:gw"

    @pytest.mark.asyncio
    async def test_no_token_means_no_call(self, user_session):
        caller = _caller(_model("gw"))
        with _patch_manager(None), pytest.raises(LLMAuthenticationError):
            await caller._resolve_call_target("gw", None, USER)

    @pytest.mark.asyncio
    async def test_a_system_model_is_not_delegated(self, user_session):
        caller = _caller(ModelConfig(model_name="sys", model_url=GATEWAY_URL, api_key="sk-system"))
        manager = _minted()
        with _patch_manager(manager):
            _, kwargs = await caller._resolve_call_target("sys", None, USER)

        assert kwargs["api_key"] == "sk-system"
        assert manager.requests == []

    def test_the_sync_builder_refuses_a_delegated_model_without_a_token(self):
        caller = _caller(_model("gw"))
        with pytest.raises(ValueError, match="no delegated token was obtained"):
            caller._get_model_kwargs("gw", user_email=USER)
