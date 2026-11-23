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

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.application.chat.runs.launcher import launch_sub_conversation
from atlas.application.chat.runs.registry import (
    BUSY_MESSAGE,
    LAUNCHED_RUN_BUSY_MESSAGE,
    RunRegistry,
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
    service = RunControlService(lambda: registry, lambda: get_approval_manager())

    answer = await service._steer(child.run_id, user_actor(USER), {"content": "hello"})

    assert answer["ok"] is False and answer["code"] == "conversation_busy"
    assert answer["message"] == LAUNCHED_RUN_BUSY_MESSAGE
    assert "next step" not in answer["message"]


@pytest.mark.asyncio
async def test_steer_to_user_run_keeps_old_text():
    registry = RunRegistry()
    run = registry.start(conversation_id="u", user_email=USER)  # not draining yet
    service = RunControlService(lambda: registry, lambda: get_approval_manager())

    answer = await service._steer(run.run_id, user_actor(USER), {"content": "hello"})

    assert answer["code"] == "conversation_busy"
    assert answer["message"] == BUSY_MESSAGE
