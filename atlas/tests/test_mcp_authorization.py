"""Central MCP authorization tests (tools, prompts, resources).

The security contract these tests pin down:

* an unauthorized user cannot retrieve a restricted prompt (and therefore
  cannot inject its text into model context);
* authorized MCP operations still work, including per-user auth routing;
* group, data-classification, and per-model checks are the same for every MCP
  capability;
* MCP sampling paths no longer exist, so a server cannot initiate a model call.
"""

from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest

from atlas.application.chat.preprocessors.prompt_override_service import (
    PromptOverrideService,
)
from atlas.core.compliance import ComplianceLevelManager
from atlas.domain.errors import AuthorizationError
from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.mcp_authorization import (
    McpDenialKind,
    McpOperation,
    authorize_mcp_server,
)


class _FakeClient:
    """Async-context FastMCP client stand-in with only what these paths use."""

    def __init__(self) -> None:
        self.get_prompt = AsyncMock(return_value="restricted prompt text")
        self.list_resources = AsyncMock(return_value=[{"uri": "resource://one"}])
        self.read_resource = AsyncMock(return_value=[{"text": "resource body"}])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None


def _manager(
    servers_config: Dict[str, Dict[str, Any]],
    client: Optional[_FakeClient] = None,
) -> MCPToolManager:
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = servers_config
    manager.available_tools = {}
    manager.clients = {"srv": client} if client is not None else {}
    manager._elicitation_routing = {}
    manager._user_clients = {}
    return manager


async def _deny_all(user_email: str, group: str) -> bool:
    return False


async def _group_member(user_email: str, group: str) -> bool:
    return group == "allowed"


# --- central policy ---------------------------------------------------------


@pytest.mark.asyncio
async def test_central_policy_allows_open_server():
    decision = await authorize_mcp_server(
        "open",
        operation=McpOperation.TOOL,
        servers_config={"open": {"enabled": True, "groups": []}},
        user_email="alice@example.com",
    )
    assert decision.allowed is True
    assert decision.user_email == "alice@example.com"


@pytest.mark.asyncio
async def test_central_policy_denies_missing_disabled_and_unknown_servers():
    for config, reason in (
        ({}, McpDenialKind.SERVER),
        ({"enabled": False}, McpDenialKind.SERVER),
    ):
        decision = await authorize_mcp_server(
            "srv",
            operation=McpOperation.TOOL,
            servers_config={"srv": config} if config else {},
            user_email="alice@example.com",
        )
        assert decision.allowed is False
        assert decision.denial is reason


@pytest.mark.asyncio
async def test_central_policy_group_acl_denies_and_allows():
    config = {"enabled": True, "groups": ["allowed"]}
    denied = await authorize_mcp_server(
        "srv",
        operation=McpOperation.PROMPT,
        servers_config={"srv": config},
        user_email="alice@example.com",
        group_check=_deny_all,
    )
    assert denied.allowed is False
    assert denied.denial is McpDenialKind.GROUP

    allowed = await authorize_mcp_server(
        "srv",
        operation=McpOperation.PROMPT,
        servers_config={"srv": config},
        user_email="alice@example.com",
        group_check=_group_member,
    )
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_central_policy_fails_closed_when_group_check_raises():
    async def boom(user_email: str, group: str) -> bool:
        raise RuntimeError("authorization backend down")

    decision = await authorize_mcp_server(
        "srv",
        operation=McpOperation.TOOL,
        servers_config={"srv": {"enabled": True, "groups": ["allowed"]}},
        user_email="alice@example.com",
        group_check=boom,
    )
    assert decision.allowed is False
    assert decision.denial is McpDenialKind.GROUP


@pytest.mark.asyncio
async def test_central_policy_requires_identity_for_attributed_operations():
    decision = await authorize_mcp_server(
        "srv",
        operation=McpOperation.PROMPT,
        servers_config={"srv": {"enabled": True, "groups": []}},
        user_email=None,
    )
    assert decision.allowed is False
    assert decision.denial is McpDenialKind.USER


@pytest.mark.asyncio
async def test_central_policy_classification_is_uniform():
    levels = ComplianceLevelManager(None)
    levels.levels = {}
    config = {
        "enabled": True,
        "groups": [],
        "allowed_data_classifications": ["UUR"],
    }
    with patch("atlas.core.compliance.get_compliance_manager", return_value=levels):
        denied = await authorize_mcp_server(
            "srv",
            operation=McpOperation.PROMPT,
            servers_config={"srv": config},
            user_email="alice@example.com",
            compliance_level="ITAR",
        )
        allowed = await authorize_mcp_server(
            "srv",
            operation=McpOperation.PROMPT,
            servers_config={"srv": config},
            user_email="alice@example.com",
            compliance_level="UUR",
        )
    assert denied.allowed is False
    assert denied.denial is McpDenialKind.CLASSIFICATION
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_central_policy_preserves_identity_case_for_external_group_check():
    """External authorizers receive the identity as the user presented it."""
    seen = []

    async def recording_check(user_email: str, group: str) -> bool:
        seen.append(user_email)
        return True

    decision = await authorize_mcp_server(
        "srv",
        operation=McpOperation.TOOL,
        servers_config={"srv": {"enabled": True, "groups": ["allowed"]}},
        user_email="  Alice@Example.com  ",
        group_check=recording_check,
    )
    assert decision.allowed is True
    assert seen == ["Alice@Example.com"]
    # Logged/attributed identity stays normalized.
    assert decision.user_email == "alice@example.com"


@pytest.mark.asyncio
async def test_central_policy_fails_closed_when_model_catalogue_unavailable():
    decision = await authorize_mcp_server(
        "srv",
        operation=McpOperation.PROMPT,
        servers_config={"srv": {"enabled": True, "groups": []}},
        user_email="alice@example.com",
        model_name="any-model",
        models=None,
    )
    assert decision.allowed is False
    assert decision.denial is McpDenialKind.MODEL


@pytest.mark.asyncio
async def test_central_policy_model_acl_denies_restricted_and_tolerates_unknown_models():
    models = {"restricted": SimpleNamespace(groups=["admin"])}

    async def not_admin(user_email: str, group: str) -> bool:
        return False

    with patch("atlas.core.model_access.is_user_in_group", not_admin):
        denied = await authorize_mcp_server(
            "srv",
            operation=McpOperation.PROMPT,
            servers_config={"srv": {"enabled": True, "groups": []}},
            user_email="alice@example.com",
            model_name="restricted",
            models=models,
        )
    assert denied.allowed is False
    assert denied.denial is McpDenialKind.MODEL

    # Unknown models are left to the LLM caller to reject, matching chat's own
    # model gate, so prompt retrieval is not blocked on an alias the model
    # catalogue has not been refreshed with yet.
    unknown = await authorize_mcp_server(
        "srv",
        operation=McpOperation.PROMPT,
        servers_config={"srv": {"enabled": True, "groups": []}},
        user_email="alice@example.com",
        model_name="not-configured",
        models=models,
    )
    assert unknown.allowed is True


# --- manager prompt path ----------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_retrieve_restricted_prompt():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )

    with patch("atlas.core.auth.is_user_in_group", _deny_all):
        with pytest.raises(AuthorizationError) as exc_info:
            await manager.get_prompt(
                "srv",
                "secret_prompt",
                user_email="alice@example.com",
                conversation_id="conv-1",
            )

    assert "not authorized" in exc_info.value.message
    client.get_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_user_can_retrieve_prompt():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )

    with patch("atlas.core.auth.is_user_in_group", _group_member):
        result = await manager.get_prompt(
            "srv",
            "allowed_prompt",
            user_email="alice@example.com",
            conversation_id="conv-1",
        )

    assert result == "restricted prompt text"
    client.get_prompt.assert_awaited_once_with("allowed_prompt")


@pytest.mark.asyncio
async def test_prompt_requires_user_identity_even_on_open_server():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": [], "command": ["python"]}},
        client,
    )

    with pytest.raises(AuthorizationError) as exc_info:
        await manager.get_prompt("srv", "prompt", user_email=None)

    assert "authenticated user" in exc_info.value.message
    client.get_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_denied_when_classification_not_declared():
    client = _FakeClient()
    manager = _manager(
        {
            "srv": {
                "enabled": True,
                "groups": [],
                "command": ["python"],
                "allowed_data_classifications": ["UUR"],
            }
        },
        client,
    )
    levels = ComplianceLevelManager(None)
    levels.levels = {}

    with patch("atlas.core.compliance.get_compliance_manager", return_value=levels):
        with pytest.raises(AuthorizationError) as exc_info:
            await manager.get_prompt(
                "srv",
                "prompt",
                user_email="alice@example.com",
                compliance_level="ITAR",
            )

    assert "not approved for ITAR" in exc_info.value.message
    client.get_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_denied_when_model_not_authorized():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": [], "command": ["python"]}},
        client,
    )
    llm_config = SimpleNamespace(
        models={"restricted": SimpleNamespace(groups=["admin"])}
    )

    async def not_admin(user_email: str, group: str) -> bool:
        return False

    with (
        patch.object(
            manager,
            "_authorize_mcp_operation",
            wraps=manager._authorize_mcp_operation,
        ),
        patch(
            "atlas.modules.mcp_tools.mcp_execution._client"
        ) as mock_client_module,
        patch("atlas.core.model_access.is_user_in_group", not_admin),
    ):
        mock_client_module.return_value.config_manager.llm_config = llm_config
        with pytest.raises(AuthorizationError) as exc_info:
            await manager.get_prompt(
                "srv",
                "prompt",
                user_email="alice@example.com",
                model_name="restricted",
            )

    assert "selected model" in exc_info.value.message
    client.get_prompt.assert_not_awaited()


# --- manager resource path --------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_read_resource():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )

    with patch("atlas.core.auth.is_user_in_group", _deny_all):
        with pytest.raises(AuthorizationError):
            await manager.read_resource(
                "srv", "resource://secret", user_email="alice@example.com"
            )

    client.read_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_user_can_list_and_read_resources():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )

    with patch("atlas.core.auth.is_user_in_group", _group_member):
        listed = await manager.list_resources(
            "srv", user_email="alice@example.com"
        )
        body = await manager.read_resource(
            "srv", "resource://allowed", user_email="alice@example.com"
        )

    assert listed == [{"uri": "resource://one"}]
    assert body == [{"text": "resource body"}]
    client.list_resources.assert_awaited_once()
    client.read_resource.assert_awaited_once_with("resource://allowed")


# --- prompt override integration -------------------------------------------


@pytest.mark.asyncio
async def test_prompt_override_does_not_inject_denied_prompt():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )
    service = PromptOverrideService(tool_manager=manager)
    messages = [{"role": "user", "content": "Hello"}]

    with patch("atlas.core.auth.is_user_in_group", _deny_all):
        result = await service.apply_prompt_override(
            messages,
            ["srv_restricted_prompt"],
            user_email="alice@example.com",
        )

    assert result == messages
    client.get_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_override_applies_authorized_prompt():
    client = _FakeClient()
    manager = _manager(
        {"srv": {"enabled": True, "groups": ["allowed"], "command": ["python"]}},
        client,
    )
    manager.available_prompts = {"srv": {"prompts": []}}
    service = PromptOverrideService(tool_manager=manager)
    messages = [{"role": "user", "content": "Hello"}]

    with patch("atlas.core.auth.is_user_in_group", _group_member):
        result = await service.apply_prompt_override(
            messages,
            ["srv_allowed_prompt"],
            user_email="alice@example.com",
        )

    assert result[0] == {"role": "system", "content": "restricted prompt text"}
    assert result[1] == {"role": "user", "content": "Hello"}


@pytest.mark.asyncio
async def test_prompt_override_forwards_trusted_level_and_model():
    """The turn's classification and model reach the authorization call."""
    tool_manager = AsyncMock()
    tool_manager.available_prompts = {}
    tool_manager.get_prompt.return_value = "prompt"
    service = PromptOverrideService(tool_manager=tool_manager)

    await service.apply_prompt_override(
        [{"role": "user", "content": "Hello"}],
        ["srv_prompt"],
        user_email="alice@example.com",
        compliance_level="ITAR",
        model_name="restricted-model",
    )

    tool_manager.get_prompt.assert_awaited_once_with(
        "srv",
        "prompt",
        meta={"user_email": "alice@example.com"},
        user_email="alice@example.com",
        conversation_id=None,
        compliance_level="ITAR",
        model_name="restricted-model",
    )


# --- sampling removal -------------------------------------------------------


def test_sampling_surfaces_are_gone():
    for attr in (
        "_create_sampling_handler",
        "_use_sampling_context",
    ):
        assert not hasattr(MCPToolManager, attr), attr

    from atlas.modules.mcp_tools import mcp_routing

    assert not hasattr(mcp_routing, "_SamplingRoutingContext")

    manager = _manager({})
    assert not hasattr(manager, "_sampling_routing")
