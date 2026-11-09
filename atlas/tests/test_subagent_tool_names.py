"""The subagent tools' names, and the deprecated names they replaced (#1141).

``atlas_launch`` read as "launch ATLAS" rather than "delegate a task to a
subagent", so the launch family was renamed. These pin both halves of that
change: the new names are what the model and the tools panel are offered, and
every old name keeps working -- in a tool selection, at execution, in the
feature gates and in an operator's hook matcher -- because agents, saved
prompts and API clients already carry them.
"""

import logging
from types import SimpleNamespace

import pytest

from atlas.application.chat.policies.tool_authorization import ToolAuthorizationService
from atlas.domain.messages.models import ToolCall
from atlas.hooks.models import HookConfig
from atlas.modules.mcp_tools import mcp_execution
from atlas.modules.mcp_tools.atlas_server import (
    ATLAS_SERVER_NAME,
    ATLAS_TOOL_NAMES,
    ATLAS_TOOL_SCHEMAS,
    DEPRECATED_TOOL_ALIASES,
    DISCOVER_LAUNCH_OPTIONS_TOOL_NAME,
    FOLLOW_UP_TOOL_NAME,
    GET_RUNS_TOOL_NAME,
    LAUNCH_GATED_TOOL_NAMES,
    LAUNCH_TOOL_NAME,
    RESULT_TOOL_NAME,
    atlas_tool_schemas,
    is_atlas_tool,
    is_builtin_tool,
    is_deprecated_tool_alias,
    matcher_candidates,
    normalize_tool_name,
    normalize_tool_names,
)
from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.routes.config_routes import _atlas_tools_info

RENAMES = {
    "atlas_discover_launch_options": "atlas_discover_subagent_options",
    "atlas_launch": "atlas_launch_subagent",
    "atlas_get_runs": "atlas_get_subagent_runs",
    "atlas_result": "atlas_get_subagent_result",
    "atlas_follow_up": "atlas_follow_up_subagent",
}


def _settings(**overrides):
    values = {
        "feature_atlas_launch_enabled": True,
        "feature_chat_history_enabled": True,
        "feature_agent_mode_available": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _manager() -> MCPToolManager:
    return MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")


def test_the_subagent_tools_carry_their_new_names():
    assert DISCOVER_LAUNCH_OPTIONS_TOOL_NAME == "atlas_discover_subagent_options"
    assert LAUNCH_TOOL_NAME == "atlas_launch_subagent"
    assert GET_RUNS_TOOL_NAME == "atlas_get_subagent_runs"
    assert RESULT_TOOL_NAME == "atlas_get_subagent_result"
    assert FOLLOW_UP_TOOL_NAME == "atlas_follow_up_subagent"
    assert DEPRECATED_TOOL_ALIASES == RENAMES


def test_every_subagent_tool_keeps_the_atlas_server_prefix():
    """The tools panel and selections address a tool as ``<server>_<tool>``."""
    for name in RENAMES.values():
        assert name.startswith(f"{ATLAS_SERVER_NAME}_")
        assert name in LAUNCH_GATED_TOOL_NAMES


def test_the_launch_schema_is_unchanged_apart_from_its_name():
    """Same parameters, same required set: only the name and wording moved."""
    function = ATLAS_TOOL_SCHEMAS[LAUNCH_TOOL_NAME]["function"]

    assert function["name"] == "atlas_launch_subagent"
    assert list(function["parameters"]["properties"]) == ["workspace", "model", "prompt"]
    assert function["parameters"]["required"] == ["workspace", "model", "prompt"]
    assert "subagent" in function["description"]
    assert "delegate" in function["description"]


def test_only_the_new_names_are_advertised():
    """A deprecated name resolves; it is never offered to a model or listed."""
    for old in RENAMES:
        assert old not in ATLAS_TOOL_SCHEMAS
        assert old not in ATLAS_TOOL_NAMES

    info = _atlas_tools_info(sleep_enabled=True, search_enabled=True, launch_enabled=True)
    listed = {f"{ATLAS_SERVER_NAME}_{tool}" for tool in info["tools"]}
    assert set(RENAMES.values()) <= listed
    assert not set(RENAMES) & listed


def test_no_tool_description_still_points_a_model_at_a_deprecated_name():
    """A description naming ``atlas_result`` would teach the model the old name."""
    import re

    for schema in ATLAS_TOOL_SCHEMAS.values():
        text = repr(schema)
        for old in RENAMES:
            assert not re.search(rf"\b{old}\b", text), (old, schema["function"]["name"])


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_a_deprecated_name_resolves_to_its_replacement(old, new):
    assert normalize_tool_name(old) == new
    assert is_deprecated_tool_alias(old)
    assert not is_deprecated_tool_alias(new)
    assert is_atlas_tool(old)
    assert is_builtin_tool(old)
    assert _manager().get_server_for_tool(old) == ATLAS_SERVER_NAME


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_a_selection_carrying_a_deprecated_name_gets_the_new_schema(old, new):
    """A saved workspace or stored selection still turns the tool on."""
    (schema,) = atlas_tool_schemas([old], launch_enabled=True)

    assert schema["function"]["name"] == new
    assert atlas_tool_schemas([old], launch_enabled=False) == []


def test_old_and_new_names_in_one_selection_offer_the_tool_once():
    assert normalize_tool_names(["atlas_launch", LAUNCH_TOOL_NAME]) == [LAUNCH_TOOL_NAME]
    schemas = atlas_tool_schemas(["atlas_launch", LAUNCH_TOOL_NAME], launch_enabled=True)
    assert [s["function"]["name"] for s in schemas] == [LAUNCH_TOOL_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_tool_authorization_gates_a_deprecated_name_like_the_new_one(enabled):
    service = ToolAuthorizationService(
        tool_manager=_manager(),
        config_manager=SimpleNamespace(
            app_settings=_settings(feature_atlas_launch_enabled=enabled)
        ),
    )

    allowed = await service.filter_authorized_tools(list(RENAMES), "user@example.com")

    assert allowed == (list(RENAMES) if enabled else [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, target",
    [
        ("atlas_launch", "execute_launch_tool"),
        ("atlas_launch_subagent", "execute_launch_tool"),
        ("atlas_discover_launch_options", "execute_launch_discovery_tool"),
        ("atlas_discover_subagent_options", "execute_launch_discovery_tool"),
    ],
)
async def test_launch_and_discovery_execute_under_either_name(name, target, monkeypatch):
    """An agent or saved prompt that still says ``atlas_launch`` keeps working."""
    from atlas.application.chat.runs import launcher

    calls = []

    async def fake(tool_call, context):
        calls.append(tool_call.name)
        return SimpleNamespace(success=True, content="ok")

    monkeypatch.setattr(launcher, target, fake)
    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(config_manager=SimpleNamespace(app_settings=_settings())),
    )

    result = await _manager().execute_tool(
        ToolCall(id="call-1", name=name, arguments={}), {"user_email": "user@example.com"}
    )

    assert result.success is True
    assert calls == [name]


@pytest.mark.asyncio
async def test_follow_up_executes_under_either_name(monkeypatch):
    from atlas.application.chat.runs import follow_up

    calls = []

    async def fake(tool_call, context):
        calls.append(tool_call.name)
        return SimpleNamespace(success=True, content="ok")

    monkeypatch.setattr(follow_up, "execute_follow_up_tool", fake)
    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(config_manager=SimpleNamespace(app_settings=_settings())),
    )

    for name in ("atlas_follow_up", FOLLOW_UP_TOOL_NAME):
        await _manager().execute_tool(
            ToolCall(id="call-1", name=name, arguments={}), {"user_email": "user@example.com"}
        )

    assert calls == ["atlas_follow_up", FOLLOW_UP_TOOL_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, expected",
    [
        ("atlas_get_runs", GET_RUNS_TOOL_NAME),
        (GET_RUNS_TOOL_NAME, GET_RUNS_TOOL_NAME),
        ("atlas_result", RESULT_TOOL_NAME),
        (RESULT_TOOL_NAME, RESULT_TOOL_NAME),
    ],
)
async def test_observation_tools_execute_under_either_name(name, expected, monkeypatch):
    """The dispatcher hands the observation helper the *resolved* name."""
    from atlas.application.chat.runs import launcher

    seen = []

    async def fake(tool_call, context, tool_name):
        seen.append(tool_name)
        return SimpleNamespace(success=True, content="ok")

    monkeypatch.setattr(launcher, "execute_observation_tool", fake)
    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(config_manager=SimpleNamespace(app_settings=_settings())),
    )

    await _manager().execute_tool(
        ToolCall(id="call-1", name=name, arguments={}), {"user_email": "user@example.com"}
    )

    assert seen == [expected]


@pytest.mark.asyncio
async def test_a_deprecated_name_is_still_refused_when_the_feature_is_off(monkeypatch):
    """The alias must not be a way around the deployment's switch."""
    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(
            config_manager=SimpleNamespace(
                app_settings=_settings(feature_atlas_launch_enabled=False)
            )
        ),
    )

    for old in RENAMES:
        result = await _manager().execute_tool(
            ToolCall(id="call-1", name=old, arguments={}), {"user_email": "user@example.com"}
        )
        assert result.success is False, old
        assert "disabled" in result.content


@pytest.mark.asyncio
async def test_a_call_under_a_deprecated_name_is_logged(monkeypatch, caplog):
    """Operators need to see who still depends on a name before it is retired."""
    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(
            config_manager=SimpleNamespace(
                app_settings=_settings(feature_atlas_launch_enabled=False)
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger=mcp_execution.logger.name):
        await _manager().execute_tool(
            ToolCall(id="call-1", name="atlas_launch", arguments={}),
            {"user_email": "user@example.com"},
        )
        deprecated = [r for r in caplog.records if "Deprecated tool name" in r.getMessage()]
        assert len(deprecated) == 1
        assert "atlas_launch_subagent" in deprecated[0].getMessage()

        caplog.clear()
        await _manager().execute_tool(
            ToolCall(id="call-2", name=LAUNCH_TOOL_NAME, arguments={}),
            {"user_email": "user@example.com"},
        )
        assert not [r for r in caplog.records if "Deprecated tool name" in r.getMessage()]


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_a_hook_written_against_the_old_name_fires_for_the_new_one(old, new):
    """Renaming a tool must never silently retire an operator's deny policy."""
    hook = HookConfig(name="deny", event="PreToolUse", command=["true"], matcher=f"^{old}$")

    assert hook.matches(new)
    assert hook.matches(old)


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_a_hook_written_against_the_new_name_fires_for_a_deprecated_call(old, new):
    """The alias still executes, so it must not slip past a policy on the new name."""
    hook = HookConfig(name="deny", event="PreToolUse", command=["true"], matcher=f"^{new}$")

    assert hook.matches(old)
    assert hook.matches(new)
    assert not hook.matches("pptx_generator_create")


def test_matcher_candidates_cover_every_spelling_without_duplicates():
    assert matcher_candidates("atlas_launch") == ("atlas_launch", "atlas_launch_subagent")
    assert matcher_candidates("atlas_launch_subagent") == ("atlas_launch_subagent", "atlas_launch")
    assert matcher_candidates("pptx_generator_create") == ("pptx_generator_create",)
    assert matcher_candidates(None) == ()


def _approvals(*required):
    """A config manager whose mcp.json mandates approval for ``required``."""
    from atlas.modules.config.models import ToolApprovalConfig, ToolApprovalsConfig

    return SimpleNamespace(
        app_settings=SimpleNamespace(force_tool_approval_globally=False),
        tool_approvals_config=ToolApprovalsConfig(
            tools={name: ToolApprovalConfig(require_approval=True) for name in required}
        ),
    )


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_an_approval_rule_on_the_old_name_still_binds_the_renamed_tool(old, new):
    """``atlas.require_approval: ["launch"]`` must not lapse with the rename."""
    from atlas.application.chat.utilities.tool_executor import requires_approval

    config = _approvals(old)

    assert requires_approval(new, config) == (True, True, True)
    assert requires_approval(old, config) == (True, True, True)


@pytest.mark.parametrize("old, new", sorted(RENAMES.items()))
def test_an_approval_rule_on_the_new_name_binds_a_deprecated_call(old, new):
    """The alias still executes, so it must not dodge a mandatory approval."""
    from atlas.application.chat.utilities.tool_executor import requires_approval

    config = _approvals(new)

    assert requires_approval(old, config) == (True, True, True)
    assert requires_approval(new, config) == (True, True, True)


def test_an_approval_rule_does_not_spill_onto_other_tools():
    from atlas.application.chat.utilities.tool_executor import requires_approval

    config = _approvals("atlas_launch")

    assert requires_approval("atlas_get_subagent_runs", config) == (True, True, False)
    assert requires_approval("pptx_generator_create", config) == (True, True, False)
