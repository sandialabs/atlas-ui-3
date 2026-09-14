"""The ``atlas_launch`` built-in tool (issue #925).

These pin the parts of "a conversation can launch sub-conversations" that are
security or resource boundaries rather than plumbing: the child cannot reach a
workspace, model or tool the caller could not, it is bounded in depth and in
how many siblings it may have, its events carry its own run identity, and
stopping the parent stops it.
"""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.launcher import (
    LaunchRefused,
    execute_launch_tool,
    launch_sub_conversation,
    launch_tool_enabled,
    resolve_workspace,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus, reset_run_registry
from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.atlas_server import (
    ATLAS_TOOL_SCHEMAS,
    LAUNCH_TOOL_NAME,
    atlas_tool_schemas,
    is_atlas_tool,
)

WORKSPACE = {
    "id": "ws-1",
    "name": "Research",
    "config": {
        "selected_tools": ["math_add", "filesystem_read"],
        "selected_data_sources": ["docs:handbook"],
        "rag_enabled": True,
    },
}


class _Workspaces:
    """Stand-in for WorkspaceRepository, scoped by user like the real one."""

    def __init__(self, rows=None, owner="user@example.com"):
        self._rows = rows if rows is not None else [WORKSPACE]
        self._owner = owner

    def get_workspace(self, workspace_id, user_email):
        if user_email != self._owner:
            return None
        return next((r for r in self._rows if r["id"] == workspace_id), None)

    def list_workspaces(self, user_email):
        return list(self._rows) if user_email == self._owner else []


class _ChatService:
    """Records the call the launcher makes instead of talking to an LLM."""

    def __init__(self, connection, authorized=("math_add",)):
        self.connection = connection
        self.calls = []
        self.tool_authorization = SimpleNamespace(
            filter_authorized_tools=self._filter
        )
        self._authorized = list(authorized)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def _filter(self, tools, user_email):
        return [t for t in tools if t in self._authorized]

    async def handle_chat_message(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.connection.send_json({"type": "token_stream", "token": "hi"})
        await self.release.wait()


def _settings(**overrides):
    base = dict(
        feature_atlas_launch_enabled=True,
        feature_chat_history_enabled=True,
        feature_agent_mode_available=True,
        atlas_launch_max_depth=2,
        atlas_launch_max_children_per_run=2,
        max_concurrent_runs_per_user=5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Factory:
    def __init__(self, chat_service_factory, settings=None, workspaces=None, models=None):
        self._chat_service_factory = chat_service_factory
        self._settings = settings or _settings()
        self.workspace_repository = workspaces if workspaces is not None else _Workspaces()
        self._models = {"gpt-4o": SimpleNamespace(groups=None)} if models is None else models
        self.services = []

    def get_config_manager(self):
        return SimpleNamespace(
            app_settings=self._settings,
            llm_config=SimpleNamespace(models=self._models),
        )

    def create_chat_service(self, connection=None):
        service = self._chat_service_factory(connection)
        self.services.append(service)
        return service


@pytest.fixture(autouse=True)
def _clean_run_state():
    reset_run_registry()
    clear_current_run()
    yield
    clear_current_run()
    reset_run_registry()


async def _collect(frames):
    async def _callback(frame):
        frames.append(frame)

    return _callback


# ---------------------------------------------------------------------------
# Schema surface
# ---------------------------------------------------------------------------


def test_launch_is_a_built_in_atlas_tool():
    assert is_atlas_tool(LAUNCH_TOOL_NAME)
    schema = ATLAS_TOOL_SCHEMAS[LAUNCH_TOOL_NAME]["function"]
    assert sorted(schema["parameters"]["required"]) == ["model", "prompt", "workspace"]
    assert sorted(schema["parameters"]["properties"]) == ["model", "prompt", "workspace"]


def test_launch_is_omitted_from_the_schema_when_disabled():
    names = [
        s["function"]["name"]
        for s in atlas_tool_schemas([LAUNCH_TOOL_NAME], launch_enabled=False)
    ]
    assert names == []


@pytest.mark.parametrize(
    "override",
    [
        {"feature_atlas_launch_enabled": False},
        {"feature_chat_history_enabled": False},
        {"feature_agent_mode_available": False},
    ],
)
def test_launch_requires_its_feature_flag_and_its_ground(override):
    assert launch_tool_enabled(_settings(**override)) is False
    assert launch_tool_enabled(_settings()) is True


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_workspace_belonging_to_another_user_is_not_found():
    repository = _Workspaces(owner="owner@example.com")
    with pytest.raises(LaunchRefused) as excinfo:
        resolve_workspace(repository, "ws-1", "intruder@example.com")
    # The refusal must not leak the other user's workspace names.
    assert "Research" not in str(excinfo.value)


def test_a_workspace_resolves_by_name_case_insensitively():
    assert resolve_workspace(_Workspaces(), "research", "user@example.com")["id"] == "ws-1"


@pytest.mark.asyncio
async def test_a_model_the_user_cannot_use_is_refused_like_an_unknown_one():
    factory = _Factory(lambda c: _ChatService(c), models={"gpt-4o": SimpleNamespace(groups=None)})
    messages = []
    for model in ("gpt-4o-secret", "not-configured"):
        with pytest.raises(LaunchRefused) as excinfo:
            await launch_sub_conversation(
                {"workspace": "Research", "model": model, "prompt": "go"},
                {"user_email": "user@example.com"},
                factory=factory,
            )
        messages.append(str(excinfo.value).replace(model, "<model>"))
    assert messages[0] == messages[1]


@pytest.mark.asyncio
async def test_the_child_only_gets_tools_the_caller_is_still_authorized_for():
    factory = _Factory(lambda c: _ChatService(c, authorized=("math_add",)))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    assert handle["tools"] == ["math_add"]

    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    assert service.calls[0]["selected_tools"] == ["math_add"]
    assert service.calls[0]["user_email"] == "user@example.com"
    assert service.calls[0]["agent_mode"] is True
    service.release.set()


@pytest.mark.asyncio
async def test_a_workspace_with_no_authorized_tools_is_refused():
    factory = _Factory(lambda c: _ChatService(c, authorized=()))
    with pytest.raises(LaunchRefused, match="no tools"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
            {"user_email": "user@example.com"},
            factory=factory,
        )


@pytest.mark.asyncio
async def test_an_unauthenticated_call_cannot_launch():
    factory = _Factory(lambda c: _ChatService(c))
    with pytest.raises(LaunchRefused, match="authenticated"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
            {},
            factory=factory,
        )


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_is_capped():
    factory = _Factory(lambda c: _ChatService(c), settings=_settings(atlas_launch_max_depth=1))
    registry = _install_registry()
    parent = registry.start(conversation_id="conv-parent", user_email="user@example.com")
    registry.set_status(parent.run_id, RunStatus.RUNNING)
    set_current_run(parent.run_id, parent.conversation_id)

    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    assert handle["depth"] == 1
    child = registry.get(handle["run_id"])

    # A launch from inside the child would be depth 2, past the cap of 1.
    set_current_run(child.run_id, child.conversation_id)
    with pytest.raises(LaunchRefused, match="nest more than 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "deeper"},
            {"user_email": "user@example.com"},
            factory=factory,
        )
    for service in factory.services:
        service.release.set()


@pytest.mark.asyncio
async def test_children_per_run_are_capped():
    factory = _Factory(
        lambda c: _ChatService(c), settings=_settings(atlas_launch_max_children_per_run=1)
    )
    registry = _install_registry()
    parent = registry.start(conversation_id="conv-parent", user_email="user@example.com")
    set_current_run(parent.run_id, parent.conversation_id)

    await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "first"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    with pytest.raises(LaunchRefused, match="limit is 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "second"},
            {"user_email": "user@example.com"},
            factory=factory,
        )
    for service in factory.services:
        service.release.set()


def test_stopping_a_parent_stops_the_runs_it_launched():
    registry = RunRegistry()
    parent = registry.start(conversation_id="conv-parent", user_email="user@example.com")
    child = registry.start(
        conversation_id="conv-child",
        user_email="user@example.com",
        parent_run_id=parent.run_id,
        depth=1,
    )
    grandchild = registry.start(
        conversation_id="conv-grandchild",
        user_email="user@example.com",
        parent_run_id=child.run_id,
        depth=2,
    )

    registry.cancel(parent.run_id, "user@example.com")

    assert registry.get(child.run_id).status is RunStatus.CANCELLED
    assert registry.get(grandchild.run_id).status is RunStatus.CANCELLED


def test_children_of_ignores_other_parents_and_terminal_runs():
    registry = RunRegistry()
    parent = registry.start(conversation_id="c1", user_email="u@example.com")
    other = registry.start(conversation_id="c2", user_email="u@example.com")
    child = registry.start(
        conversation_id="c3", user_email="u@example.com", parent_run_id=parent.run_id, depth=1
    )

    assert [r.run_id for r in registry.children_of(parent.run_id)] == [child.run_id]
    assert registry.children_of(other.run_id) == []

    registry.set_status(child.run_id, RunStatus.COMPLETED)
    assert registry.children_of(parent.run_id) == []
    assert len(registry.children_of(parent.run_id, include_terminal=True)) == 1


# ---------------------------------------------------------------------------
# Behaviour of the launched run
# ---------------------------------------------------------------------------


def _install_registry() -> RunRegistry:
    """Point the module-level singleton at a fresh registry."""
    from atlas.application.chat.runs import registry as registry_module

    registry_module._registry = RunRegistry()
    return registry_module._registry


@pytest.mark.asyncio
async def test_the_handle_comes_back_before_the_child_finishes():
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com"},
        factory=factory,
    )

    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    # Still working: the call returned a handle, not an answer.
    assert not service.release.is_set()
    assert handle["status"] == RunStatus.QUEUED.value
    assert handle["conversation_id"] and handle["run_id"]
    assert handle["data_sources"] == ["docs:handbook"]
    service.release.set()


@pytest.mark.asyncio
async def test_child_events_carry_the_childs_own_identity():
    frames = []
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "update_callback": await _collect(frames)},
        factory=factory,
    )

    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    service.release.set()
    await asyncio.sleep(0)

    assert frames
    assert frames[0]["run_id"] == handle["run_id"]
    assert frames[0]["conversation_id"] == handle["conversation_id"]


@pytest.mark.asyncio
async def test_a_finished_child_reaches_a_terminal_status():
    registry = _install_registry()
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    service.release.set()
    await asyncio.wait_for(registry.get(handle["run_id"]).task, timeout=1)

    assert registry.get(handle["run_id"]).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_rag_disabled_workspace_sends_no_data_sources():
    rows = [
        {
            **WORKSPACE,
            "config": {**WORKSPACE["config"], "rag_enabled": False},
        }
    ]
    factory = _Factory(lambda c: _ChatService(c), workspaces=_Workspaces(rows))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    assert handle["data_sources"] == []
    for service in factory.services:
        service.release.set()


@pytest.mark.asyncio
async def test_a_refusal_comes_back_as_a_failed_tool_result_not_an_exception(monkeypatch):
    async def _refuse(*args, **kwargs):
        raise LaunchRefused("No workspace named 'Nope'.")

    monkeypatch.setattr(
        "atlas.application.chat.runs.launcher.launch_sub_conversation", _refuse
    )
    result = await execute_launch_tool(
        ToolCall(id="call-1", name=LAUNCH_TOOL_NAME, arguments={}), {}
    )

    assert result.success is False
    assert "No workspace named" in result.content


@pytest.mark.asyncio
async def test_missing_arguments_are_refused_by_name():
    factory = _Factory(lambda c: _ChatService(c))
    with pytest.raises(LaunchRefused, match="'prompt' is required"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "   "},
            {"user_email": "user@example.com"},
            factory=factory,
        )


@pytest.mark.asyncio
async def test_the_model_is_not_offered_launch_when_the_deployment_disables_it(monkeypatch):
    """The schema path gates the tool, not just execution.

    Agent mode reaches the loop without ACL filtering, so a tool left in the
    schema costs a step before execution can refuse it -- and, worse, tells the
    model a capability exists that the deployment has switched off.
    """
    from atlas.modules.mcp_tools import mcp_discovery
    from atlas.modules.mcp_tools.client import MCPToolManager

    monkeypatch.setattr(
        mcp_discovery,
        "_atlas_tool_flags",
        lambda: (True, True, False),
    )
    manager = MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")

    schema = manager.get_tools_for_servers(["atlas"])
    assert LAUNCH_TOOL_NAME not in [s["function"]["name"] for s in schema["tools"]]
    assert manager.get_tools_schema([LAUNCH_TOOL_NAME]) == []

    monkeypatch.setattr(mcp_discovery, "_atlas_tool_flags", lambda: (True, True, True))
    assert [s["function"]["name"] for s in manager.get_tools_schema([LAUNCH_TOOL_NAME])] == [
        LAUNCH_TOOL_NAME
    ]


@pytest.mark.asyncio
async def test_an_untracked_turn_is_still_bounded_in_fan_out():
    """No parent record means no children to count; fall back to the user's."""
    factory = _Factory(
        lambda c: _ChatService(c), settings=_settings(atlas_launch_max_children_per_run=1)
    )
    _install_registry()

    first = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "first"},
        {"user_email": "user@example.com"},
        factory=factory,
    )
    assert first["parent_run_id"] is None

    with pytest.raises(LaunchRefused, match="limit is 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "second"},
            {"user_email": "user@example.com"},
            factory=factory,
        )
    for service in factory.services:
        service.release.set()
