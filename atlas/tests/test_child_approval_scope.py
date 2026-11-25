"""Issue #1225: what pauses inside a launched sub-conversation, and why a
message sent to a running one is refused.

A: one admin-gated tool in the workspace must pause only that tool; the
child's other tools run, because the launch call already covered them.
B: a launched run has no steering channel, so the refusal must not tell the
user to wait for a step boundary that never drains a message.
"""

import asyncio
from types import SimpleNamespace

import pytest
from test_websocket_run_directory_outage import chat_service  # noqa: F401  (fixture)

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.launcher import launch_sub_conversation
from atlas.application.chat.runs.registry import (
    BUSY_MESSAGE,
    LAUNCHED_RUN_BUSY_MESSAGE,
    NO_STEERING_BUSY_MESSAGE,
    RunRegistry,
    busy_message_for,
    reset_run_registry,
)
from atlas.application.chat.runs.run_control import RunControlService, user_actor
from atlas.application.chat.utilities.tool_executor import execute_single_tool

USER = "user@example.com"
GATED = "gated_tool"
OPEN = "open_tool"

WORKSPACE = {
    "id": "ws-1",
    "name": "Research",
    "config": {"selected_tools": [OPEN, GATED], "selected_data_sources": []},
}


class _Workspaces:
    def get_workspace(self, workspace_id, user_email):
        return WORKSPACE if user_email == USER else None

    def list_workspaces(self, user_email):
        return [WORKSPACE]


class _ChatService:
    def __init__(self, connection):
        self.connection = connection
        self.tool_authorization = SimpleNamespace(filter_authorized_tools=self._filter)
        self.agent_mode = SimpleNamespace(
            agent_loop_factory=SimpleNamespace(skip_approval=False)
        )
        self.release = asyncio.Event()

    async def _filter(self, tools, user_email):
        return list(tools)

    async def handle_chat_message(self, **kwargs):
        await self.release.wait()


class _Factory:
    def __init__(self):
        self.services = []
        self._settings = SimpleNamespace(
            feature_atlas_launch_enabled=True,
            feature_chat_history_enabled=True,
            feature_agent_mode_available=True,
            atlas_launch_max_depth=2,
            atlas_launch_max_children_per_run=2,
            max_concurrent_runs_per_user=5,
        )
        self.workspace_repository = _Workspaces()

    def get_config_manager(self):
        return SimpleNamespace(
            app_settings=self._settings,
            llm_config=SimpleNamespace(models={"gpt-4o": SimpleNamespace(groups=None)}),
        )

    def create_chat_service(self, connection=None):
        service = _ChatService(connection)
        self.services.append(service)
        return service


class _ToolManager:
    async def execute_tool(self, tool_call_obj, context=None):
        return SimpleNamespace(content="ran", success=True, error=None)

    def get_tool_schema(self, tool_name):
        return {"type": "object", "properties": {}}

    def get_tools_schema(self, *args, **kwargs):
        return []


def _call(call_id, name):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments="{}"))


@pytest.fixture(autouse=True)
def _clean():
    reset_run_registry()
    clear_current_run()
    yield
    clear_current_run()
    reset_run_registry()
    manager = get_approval_manager()
    for request in list(manager.get_pending_requests().values()):
        manager.cleanup_request(request.tool_call_id, user_email=request.user_email)


@pytest.fixture
def gate(monkeypatch):
    """Only ``gated_tool`` is admin-mandated; everything else is user-level."""
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, name == GATED),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.2,
    )


async def _launch_child():
    factory = _Factory()
    handle = await launch_sub_conversation(
        {"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
        {"user_email": USER, "launch_discovery": {
            "workspaces": [{"id": "ws-1", "name": "Research"}],
            "models": [{"name": "gpt-4o"}],
        }},
        factory=factory,
    )
    return factory, handle


async def _child_tool_call(factory, handle, name):
    """Run one tool call the way the child's loop would, in the child's run."""
    set_current_run(handle["run_id"], handle["conversation_id"])
    frames = []

    async def update_callback(message):
        frames.append(message)

    result = await execute_single_tool(
        _call(f"call-{name}", name),
        {"user_email": USER, "session_id": "s1"},
        _ToolManager(),
        update_callback=update_callback,
        config_manager=SimpleNamespace(),
        skip_approval=factory.services[0].agent_mode.agent_loop_factory.skip_approval,
    )
    return result, [f for f in frames if f.get("type") == "tool_approval_request"]


@pytest.mark.asyncio
async def test_open_tool_runs_beside_gated(gate):
    factory, handle = await _launch_child()
    try:
        assert handle["tools_needing_approval"] == [GATED]
        result, requests = await _child_tool_call(factory, handle, OPEN)
        assert requests == []
        assert result.success is True and result.content == "ran"
    finally:
        factory.services[0].release.set()


@pytest.mark.asyncio
async def test_gated_tool_still_asks_in_child(gate):
    factory, handle = await _launch_child()
    try:
        result, requests = await _child_tool_call(factory, handle, GATED)
        assert len(requests) == 1
        assert requests[0]["auto_approvable"] is False
        assert result.success is False  # nobody answered; the wait timed out
    finally:
        factory.services[0].release.set()


@pytest.mark.asyncio
async def test_force_global_gates_every_tool(monkeypatch):
    """With the global switch on, every tool is admin-gated, so all of them ask."""
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, True),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.2,
    )
    factory, handle = await _launch_child()
    try:
        assert handle["tools_needing_approval"] == [OPEN, GATED]
        _, requests = await _child_tool_call(factory, handle, OPEN)
        assert len(requests) == 1
    finally:
        factory.services[0].release.set()


@pytest.mark.asyncio
async def test_cli_skip_approval_unchanged(gate):
    """No run in scope (CLI / direct call): skip_approval still skips everything."""
    frames = []

    async def update_callback(message):
        frames.append(message)

    result = await execute_single_tool(
        _call("c1", GATED),
        {"user_email": USER, "session_id": "s1"},
        _ToolManager(),
        update_callback=update_callback,
        config_manager=SimpleNamespace(),
        skip_approval=True,
    )
    assert result.success is True
    assert not [f for f in frames if f.get("type") == "tool_approval_request"]


@pytest.mark.asyncio
async def test_steer_to_child_says_it_cannot():
    """B: the reply for a running child gives advice that can come true."""
    registry = RunRegistry()
    parent = registry.start(conversation_id="p", user_email=USER)
    child = registry.start(
        conversation_id="c", user_email=USER, parent_run_id=parent.run_id, depth=1
    )
    child.launch_spec = {"model": "gpt-4o"}
    service = RunControlService(lambda: registry, get_approval_manager)

    answer = await service._steer(child.run_id, user_actor(USER), {"content": "hello"})

    assert answer["ok"] is False and answer["code"] == "conversation_busy"
    assert answer["message"] == LAUNCHED_RUN_BUSY_MESSAGE
    assert "next step" not in answer["message"]


@pytest.mark.asyncio
async def test_steer_to_user_run_keeps_old_text():
    registry = RunRegistry()
    run = registry.start(conversation_id="u", user_email=USER)  # not draining yet
    service = RunControlService(lambda: registry, get_approval_manager)

    answer = await service._steer(run.run_id, user_actor(USER), {"content": "hello"})

    assert answer["code"] == "conversation_busy"
    # No live steering channel: no promise of a step boundary.
    assert answer["message"] == NO_STEERING_BUSY_MESSAGE
    assert "next step" not in answer["message"]


def test_step_boundary_advice_needs_a_live_channel():
    from atlas.application.chat.agent.steering import SteeringChannel

    registry = RunRegistry()
    channel = SteeringChannel()
    channel.activate()
    run = registry.start(conversation_id="a", user_email=USER, steering=channel)
    assert busy_message_for(run) == BUSY_MESSAGE
    channel.deactivate()
    assert busy_message_for(run) == NO_STEERING_BUSY_MESSAGE


@pytest.mark.asyncio
async def test_child_gate_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.2,
    )
    """No config manager, or a policy lookup that raises: the tool stays gated."""
    factory, handle = await _launch_child()
    try:
        set_current_run(handle["run_id"], handle["conversation_id"])
        for config in (None, _RaisingConfig()):
            frames = []

            async def update_callback(message, frames=frames):
                frames.append(message)

            await execute_single_tool(
                _call("c-closed", OPEN),
                {"user_email": USER, "session_id": "s1"},
                _ToolManager(),
                update_callback=update_callback,
                config_manager=config,
                skip_approval=True,
            )
            assert [f for f in frames if f.get("type") == "tool_approval_request"]
    finally:
        factory.services[0].release.set()


class _RaisingConfig:
    @property
    def app_settings(self):
        raise RuntimeError("policy unreadable")

    @property
    def tool_approvals_config(self):
        raise RuntimeError("policy unreadable")


def test_launch_and_enforcement_agree_on_the_gated_set():
    from atlas.application.chat.runs.launcher import _admin_gated_tools

    assert _admin_gated_tools([OPEN, GATED], None) == [OPEN, GATED]
    assert _admin_gated_tools([OPEN], _RaisingConfig()) == [OPEN]


@pytest.mark.asyncio
async def test_launch_handle_text_names_only_gated(gate):
    from atlas.application.chat.runs.launcher import execute_launch_tool

    factory = _Factory()
    call = SimpleNamespace(
        id="l1",
        arguments={"workspace": "Research", "model": "gpt-4o", "prompt": "go"},
    )
    result = await execute_launch_tool(call, {
        "user_email": USER,
        "factory": factory,
        "launch_discovery": {
            "workspaces": [{"id": "ws-1", "name": "Research"}],
            "models": [{"name": "gpt-4o"}],
        },
    })
    try:
        assert result.success, result.content
        assert "Only these tools will pause" in result.content
        assert "other tools run without asking" in result.content
    finally:
        for service in factory.services:
            service.release.set()


def test_local_websocket_refusal_for_a_child(chat_service):  # noqa: F811
    from fastapi.testclient import TestClient
    from main import app
    from test_websocket_run_directory_outage import CONV as WS_CONV
    from test_websocket_run_directory_outage import USER as WS_USER

    from atlas.application.chat.runs import get_run_registry
    from atlas.application.chat.runs.control_bus import set_run_control_bus

    set_run_control_bus(None)
    registry = get_run_registry()
    parent = registry.start(conversation_id="parent-conv", user_email=WS_USER)
    child = registry.start(
        conversation_id=WS_CONV, user_email=WS_USER,
        parent_run_id=parent.run_id, depth=1,
    )
    child.launch_spec = {"model": "m"}
    with TestClient(app).websocket_connect("/ws", headers={"X-User-Email": WS_USER}) as ws:
        ws.send_json({"type": "chat", "content": "hi", "model": "m",
                      "conversation_id": WS_CONV})
        frame = ws.receive_json()
        while frame.get("type") != "error":
            frame = ws.receive_json()
    assert frame["error_type"] == "conversation_busy"
    assert frame["message"] == LAUNCHED_RUN_BUSY_MESSAGE



def _real_config(force_global=False):
    """A real ConfigManager with one per-tool rule; nothing is patched."""
    from atlas.modules.config.config_loader import ConfigManager
    from atlas.modules.config.models import ToolApprovalsConfig
    from atlas.modules.config.settings import AppSettings

    config = ConfigManager()
    settings = AppSettings()
    settings.force_tool_approval_globally = force_global
    config._app_settings = settings
    config._tool_approvals_config = ToolApprovalsConfig(
        tools={GATED: {"require_approval": True}}
    )
    return config


def test_real_config_gates_per_tool_and_globally():
    from atlas.application.chat.utilities.tool_executor import is_admin_gated

    config = _real_config()
    assert is_admin_gated(GATED, config) is True
    assert is_admin_gated(OPEN, config) is False

    forced = _real_config(force_global=True)
    assert is_admin_gated(OPEN, forced) is True
    assert is_admin_gated(GATED, forced) is True


@pytest.mark.asyncio
async def test_real_config_in_a_child_run(monkeypatch):
    """The unpatched policy: the gated tool asks, the open one runs."""
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0.2,
    )
    factory, handle = await _launch_child()
    set_current_run(handle["run_id"], handle["conversation_id"])
    config = _real_config()
    seen = {}
    try:
        for name in (OPEN, GATED):
            frames = []

            async def update_callback(message, frames=frames):
                frames.append(message)

            result = await execute_single_tool(
                _call(f"real-{name}", name),
                {"user_email": USER, "session_id": "s1"},
                _ToolManager(),
                update_callback=update_callback,
                config_manager=config,
                skip_approval=True,
            )
            seen[name] = (
                result.success,
                [f for f in frames if f.get("type") == "tool_approval_request"],
            )
    finally:
        factory.services[0].release.set()
    assert seen[OPEN][0] is True and seen[OPEN][1] == []
    assert len(seen[GATED][1]) == 1
