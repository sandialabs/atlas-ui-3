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
    discover_launch_options,
    execute_launch_discovery_tool,
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

_DISCOVERY = {
    "workspaces": [{"id": "ws-1", "name": "Research"}],
    "models": [{"name": "gpt-4o", "provider": "unknown", "model": "gpt-4o"}],
}

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
        # Mirrors the real ChatService shape the launcher pre-approves through.
        self.agent_mode = SimpleNamespace(
            agent_loop_factory=SimpleNamespace(skip_approval=False)
        )
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


def test_launch_discovery_is_a_built_in_tool():
    from atlas.modules.mcp_tools.atlas_server import DISCOVER_LAUNCH_OPTIONS_TOOL_NAME

    assert is_atlas_tool(DISCOVER_LAUNCH_OPTIONS_TOOL_NAME)
    schema = ATLAS_TOOL_SCHEMAS[DISCOVER_LAUNCH_OPTIONS_TOOL_NAME]["function"]
    assert schema["parameters"]["properties"] == {}


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
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_discovery_returns_authorized_workspaces_and_models():
    factory = _Factory(lambda c: _ChatService(c))
    context = {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)}

    options = await discover_launch_options(context, factory=factory)

    assert options["workspaces"] == [{"id": "ws-1", "name": "Research"}]
    assert options["models"] == [{"name": "gpt-4o"}]
    assert context["launch_discovery"] == options


@pytest.mark.asyncio
async def test_launch_is_blocked_when_discovery_has_no_choices():
    factory = _Factory(lambda c: _ChatService(c), workspaces=_Workspaces(rows=[]), models={})

    with pytest.raises(LaunchRefused, match="no valid workspaces or LLM models"):
        await discover_launch_options({"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)}, factory=factory)


@pytest.mark.asyncio
async def test_discovery_execution_primes_the_same_context_for_launch():
    factory = _Factory(lambda c: _ChatService(c))
    context = {"user_email": "user@example.com", "factory": factory, "launch_discovery": {}}
    result = await execute_launch_discovery_tool(
        ToolCall(id="discover-1", name="atlas_discover_launch_options", arguments={}),
        context,
    )

    assert result.success
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        context,
        factory=factory,
    )
    factory.services[0].release.set()
    assert handle["workspace"] == "Research"


@pytest.mark.asyncio
async def test_launch_requires_discovery_when_tool_context_supplies_state():
    factory = _Factory(lambda c: _ChatService(c))
    with pytest.raises(LaunchRefused, match="discover_launch_options"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
            {"user_email": "user@example.com", "launch_discovery": {}},
            factory=factory,
        )


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
                {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
                factory=factory,
            )
        messages.append(str(excinfo.value).replace(model, "<model>"))
    assert messages[0] == messages[1]


@pytest.mark.asyncio
async def test_the_child_only_gets_tools_the_caller_is_still_authorized_for():
    factory = _Factory(lambda c: _ChatService(c, authorized=("math_add",)))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    assert handle["depth"] == 1
    child = registry.get(handle["run_id"])

    # A launch from inside the child would be depth 2, past the cap of 1.
    set_current_run(child.run_id, child.conversation_id)
    with pytest.raises(LaunchRefused, match="nest more than 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "deeper"},
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    with pytest.raises(LaunchRefused, match="limit is 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "second"},
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )

    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    # Still working: the call returned a handle, not an answer.
    assert not service.release.is_set()
    assert handle["status"] == RunStatus.RUNNING.value
    assert handle["conversation_id"] and handle["run_id"]
    assert handle["data_sources"] == ["docs:handbook"]
    service.release.set()


@pytest.mark.asyncio
async def test_child_events_carry_the_childs_own_identity():
    frames = []
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY), "update_callback": await _collect(frames)},
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
async def test_a_child_approval_is_replayable_from_its_run_record():
    registry = _install_registry()
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    await service.connection.send_json(
        {"type": "tool_approval_request", "tool_call_id": "call-1"}
    )

    record = registry.get(handle["run_id"])
    assert record.status is RunStatus.WAITING_FOR_INPUT
    assert registry.pending_requests_for_conversation(
        handle["conversation_id"], "user@example.com"
    ) == [{
        "type": "tool_approval_request",
        "tool_call_id": "call-1",
        "run_id": handle["run_id"],
        "conversation_id": handle["conversation_id"],
    }]
    service.release.set()


@pytest.mark.asyncio
async def test_a_finished_child_reaches_a_terminal_status():
    registry = _install_registry()
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
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
        "atlas_discover_launch_options",
        LAUNCH_TOOL_NAME,
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
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    assert first["parent_run_id"] is None

    with pytest.raises(LaunchRefused, match="limit is 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "second"},
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
            factory=factory,
        )
    for service in factory.services:
        service.release.set()


@pytest.mark.asyncio
async def test_the_parents_compliance_level_travels_to_the_child():
    """A sub-conversation is never less restricted than the turn that asked for it."""
    factory = _Factory(lambda c: _ChatService(c))
    await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY), "compliance_level": "restricted"},
        factory=factory,
    )
    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)
    assert service.calls[0]["compliance_level"] == "restricted"
    service.release.set()


@pytest.mark.asyncio
async def test_a_launch_from_an_incognito_turn_is_refused():
    """The child persists a transcript; an incognito turn asked for none."""
    factory = _Factory(lambda c: _ChatService(c))
    with pytest.raises(LaunchRefused, match="incognito"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
            {"user_email": "user@example.com", "incognito": True},
            factory=factory,
        )


@pytest.mark.asyncio
async def test_concurrent_launches_in_one_step_cannot_both_pass_a_cap_of_one():
    """Admission is check-and-insert with no await between the two."""
    factory = _Factory(
        lambda c: _ChatService(c), settings=_settings(atlas_launch_max_children_per_run=1)
    )
    _install_registry()

    results = await asyncio.gather(
        *[
            launch_sub_conversation(
                {"workspace": "Research", "model": "gpt-4o", "prompt": f"task {i}"},
                {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
                factory=factory,
            )
            for i in range(4)
        ],
        return_exceptions=True,
    )
    admitted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, LaunchRefused)]

    assert len(admitted) == 1
    assert len(refused) == 3
    for service in factory.services:
        service.release.set()


@pytest.mark.asyncio
async def test_stopping_a_finished_parent_still_cancels_a_running_child():
    """The common case: the parent turn ends while the child keeps working."""
    registry = _install_registry()
    factory = _Factory(lambda c: _ChatService(c))
    parent = registry.start(conversation_id="conv-parent", user_email="user@example.com")
    set_current_run(parent.run_id, parent.conversation_id)

    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)

    # The parent's turn finishes; the handle-returning launch means the child
    # is still working.
    registry.set_status(parent.run_id, RunStatus.COMPLETED)
    child = registry.get(handle["run_id"])
    assert not child.is_terminal
    # Held before the cancel: reaching a terminal status clears the record's
    # task reference.
    child_task = child.task

    assert registry.cancel(parent.run_id, "user@example.com") is True
    assert registry.get(handle["run_id"]).status is RunStatus.CANCELLED
    # The child's real task was cancelled, not just its record.
    with pytest.raises(asyncio.CancelledError):
        await child_task


def test_a_finished_parent_is_not_reaped_while_a_child_is_still_running():
    registry = RunRegistry()
    parent = registry.start(conversation_id="c1", user_email="u@example.com")
    child = registry.start(
        conversation_id="c2", user_email="u@example.com", parent_run_id=parent.run_id, depth=1
    )
    registry.set_status(parent.run_id, RunStatus.COMPLETED)

    import time as _time

    from atlas.application.chat.runs.registry import TERMINAL_RETENTION_SECONDS

    assert registry.reap_terminal(now=_time.time() + TERMINAL_RETENTION_SECONDS + 60) == 0
    assert registry.get(parent.run_id) is not None

    registry.set_status(child.run_id, RunStatus.COMPLETED)
    assert registry.reap_terminal(now=_time.time() + TERMINAL_RETENTION_SECONDS + 60) == 2


@pytest.mark.asyncio
async def test_the_wall_clock_backstop_stops_the_childrens_runs_too():
    registry = RunRegistry()
    parent = registry.start(conversation_id="c1", user_email="u@example.com")
    child = registry.start(
        conversation_id="c2", user_email="u@example.com", parent_run_id=parent.run_id, depth=1
    )

    async def _forever():
        await asyncio.Event().wait()

    child_task = asyncio.ensure_future(_forever())
    registry.attach_task(child.run_id, child_task)
    parent.created_at -= 10_000

    stopped = registry.enforce_wall_clock(60)

    assert stopped == [parent.run_id]
    assert registry.get(child.run_id).status is RunStatus.FAILED
    with pytest.raises(asyncio.CancelledError):
        await child_task


@pytest.mark.asyncio
async def test_wall_clock_cascade_reaches_live_grandchildren_through_terminal_children():
    registry = RunRegistry()
    parent = registry.start(conversation_id="c1", user_email="u@example.com")
    child = registry.start(
        conversation_id="c2", user_email="u@example.com", parent_run_id=parent.run_id, depth=1
    )
    grandchild = registry.start(
        conversation_id="c3", user_email="u@example.com", parent_run_id=child.run_id, depth=2
    )
    registry.set_status(child.run_id, RunStatus.COMPLETED)

    async def _forever():
        await asyncio.Event().wait()

    grandchild_task = asyncio.ensure_future(_forever())
    registry.attach_task(grandchild.run_id, grandchild_task)
    parent.created_at -= 10_000

    assert registry.enforce_wall_clock(60) == [parent.run_id]
    assert registry.get(grandchild.run_id).status is RunStatus.FAILED
    with pytest.raises(asyncio.CancelledError):
        await grandchild_task


@pytest.mark.asyncio
async def test_a_child_error_response_marks_the_run_failed():
    registry = _install_registry()

    class _FailingService(_ChatService):
        async def handle_chat_message(self, **kwargs):
            return {"type": "error", "message": "child failed"}

    factory = _Factory(lambda c: _FailingService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    await asyncio.wait_for(registry.get(handle["run_id"]).task, timeout=1)

    record = registry.get(handle["run_id"])
    assert record.status is RunStatus.FAILED
    assert record.error == "child failed"


@pytest.mark.asyncio
async def test_a_childs_tool_rows_are_not_recorded_into_the_parents_history():
    """The child sends past the parent's recorder, and the recorder drops strays."""
    from atlas.application.chat.utilities.tool_history import ToolCallRecorder

    delivered = []

    async def _transport(frame):
        delivered.append(frame)

    set_current_run("parent-run", "conv-parent")
    recorder = ToolCallRecorder(_transport)
    clear_current_run()

    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY), "update_callback": recorder},
        factory=factory,
    )
    service = factory.services[0]
    await asyncio.wait_for(service.started.wait(), timeout=1)

    # A tool row emitted by the child, as the executor would send it.
    await service.connection.send_json(
        {
            "type": "tool_start",
            "tool_call_id": "child-call-1",
            "tool_name": "math_add",
            "arguments": {"a": 1},
        }
    )
    service.release.set()

    assert recorder.messages() == []
    assert [f["run_id"] for f in delivered] == [handle["run_id"], handle["run_id"]]


def test_the_recorder_drops_events_tagged_with_another_run():
    from atlas.application.chat.utilities.tool_history import ToolCallRecorder

    set_current_run("parent-run", "conv-parent")
    recorder = ToolCallRecorder(None)
    clear_current_run()

    recorder._record(
        {
            "type": "tool_start",
            "tool_call_id": "x",
            "tool_name": "math_add",
            "run_id": "some-other-run",
        }
    )
    assert recorder.messages() == []

    recorder._record(
        {
            "type": "tool_start",
            "tool_call_id": "y",
            "tool_name": "math_add",
            "run_id": "parent-run",
        }
    )
    assert len(recorder.messages()) == 1


def test_agent_mode_carries_the_turns_incognito_flag_to_its_tools():
    """The refusal above is only reachable if agent mode forwards the flag.

    Agent mode builds its own tool session_context rather than spreading the
    session's, so a flag added for tools has to be added here too -- this is
    the wiring that makes the incognito refusal real in the app rather than
    only in a unit test.
    """
    import inspect

    from atlas.application.chat.agent import agentic_loop
    from atlas.application.chat.agent.protocols import AgentContext
    from atlas.application.chat.modes import agent as agent_mode

    assert "incognito" in AgentContext.__dataclass_fields__
    assert '"incognito": context.incognito' in inspect.getsource(agentic_loop)
    assert 'incognito=bool(session.context.get("incognito"' in inspect.getsource(agent_mode)


@pytest.mark.asyncio
async def test_a_run_id_the_registry_never_saw_is_treated_as_untracked():
    """Otherwise children hang off a phantom parent and no cap ever bites."""
    factory = _Factory(
        lambda c: _ChatService(c), settings=_settings(atlas_launch_max_children_per_run=1)
    )
    _install_registry()
    set_current_run("a-run-nobody-registered", "conv-ghost")

    first = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "first"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )
    assert first["parent_run_id"] is None

    with pytest.raises(LaunchRefused, match="limit is 1"):
        await launch_sub_conversation(
            {"workspace": "Research", "model": "gpt-4o", "prompt": "second"},
            {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
            factory=factory,
        )
    for service in factory.services:
        service.release.set()


def test_a_terminal_child_does_not_hide_a_running_grandchild():
    registry = RunRegistry()
    parent = registry.start(conversation_id="c1", user_email="u@example.com")
    child = registry.start(
        conversation_id="c2", user_email="u@example.com", parent_run_id=parent.run_id, depth=1
    )
    grandchild = registry.start(
        conversation_id="c3", user_email="u@example.com", parent_run_id=child.run_id, depth=2
    )
    registry.set_status(child.run_id, RunStatus.COMPLETED)

    # The finished middle run must not be reaped out from under its live child.
    import time as _time

    from atlas.application.chat.runs.registry import TERMINAL_RETENTION_SECONDS

    assert registry.reap_terminal(now=_time.time() + TERMINAL_RETENTION_SECONDS + 60) == 0

    registry.cancel(parent.run_id, "u@example.com")
    assert registry.get(grandchild.run_id).status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_the_three_launch_gates_agree_on_the_same_flag():
    """Schema, tool authorization and execution must switch together."""
    from atlas.application.chat.policies.tool_authorization import ToolAuthorizationService
    from atlas.modules.mcp_tools.client import MCPToolManager

    manager = MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")

    for enabled in (True, False):
        settings = _settings(feature_atlas_launch_enabled=enabled)
        config_manager = SimpleNamespace(app_settings=settings)
        service = ToolAuthorizationService(
            tool_manager=manager, config_manager=config_manager
        )

        assert launch_tool_enabled(settings) is enabled
        # Schema gate.
        offered = [
            s["function"]["name"]
            for s in atlas_tool_schemas([LAUNCH_TOOL_NAME], launch_enabled=enabled)
        ]
        assert offered == (["atlas_discover_launch_options", LAUNCH_TOOL_NAME] if enabled else [])
        # Authorization gate.
        allowed = await service.filter_authorized_tools(
            [LAUNCH_TOOL_NAME], "user@example.com"
        )
        assert allowed == (["atlas_discover_launch_options", LAUNCH_TOOL_NAME] if enabled else [])


@pytest.mark.asyncio
async def test_execution_refuses_the_tool_when_the_deployment_disables_it(monkeypatch):
    """A saved conversation can still name a tool the deployment switched off."""
    from atlas.modules.mcp_tools import mcp_execution
    from atlas.modules.mcp_tools.client import MCPToolManager

    monkeypatch.setattr(
        mcp_execution,
        "_client",
        lambda: SimpleNamespace(
            config_manager=SimpleNamespace(
                app_settings=_settings(feature_atlas_launch_enabled=False)
            )
        ),
    )
    manager = MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")

    result = await manager.execute_tool(
        ToolCall(
            id="call-1",
            name=LAUNCH_TOOL_NAME,
            arguments={"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        ),
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
    )

    assert result.success is False
    assert "disabled" in result.content


@pytest.mark.asyncio
async def test_the_launch_call_is_the_approval_for_the_childs_own_tools(monkeypatch):
    """Nobody is watching the child's conversation to answer an approval."""
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, False),
    )
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )

    assert handle["tools_needing_approval"] == []
    assert factory.services[0].agent_mode.agent_loop_factory.skip_approval is True
    for service in factory.services:
        service.release.set()


@pytest.mark.asyncio
async def test_an_admin_mandated_tool_still_prompts_inside_the_child(monkeypatch):
    """Launching cannot wave away an approval the admin made mandatory."""
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, True),
    )
    factory = _Factory(lambda c: _ChatService(c))
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": "user@example.com", "launch_discovery": dict(_DISCOVERY)},
        factory=factory,
    )

    assert handle["tools_needing_approval"] == ["math_add"]
    assert factory.services[0].agent_mode.agent_loop_factory.skip_approval is False
    for service in factory.services:
        service.release.set()
