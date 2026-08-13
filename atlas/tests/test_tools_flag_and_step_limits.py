"""Server-side enforcement of the tools feature flag and agent step ceiling.

FEATURE_TOOLS_ENABLED previously only filtered what /api/config advertised, so
a crafted client could still submit selected_tools, selected_prompts or
agent_mode over the WebSocket and have them honoured -- the flag looked like a
security boundary but was purely cosmetic.

agent_max_steps had the same shape: it arrived off the WebSocket frame with no
server-side ceiling, and each step is a metered model call plus its tool calls.

Both are enforced in the orchestrator rather than the WebSocket handler, so
programmatic callers go through the same rule.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


def _orchestrator(*, tools_enabled=True, agent_max_steps=10):
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(
            feature_tools_enabled=tools_enabled,
            agent_max_steps=agent_max_steps,
        )
    )
    return orchestrator


# --- tools flag -----------------------------------------------------------

def test_tools_enabled_is_read_from_settings():
    assert _orchestrator(tools_enabled=True)._tools_are_enabled() is True
    assert _orchestrator(tools_enabled=False)._tools_are_enabled() is False


def test_missing_config_manager_does_not_disable_tools():
    """A caller constructed without config must not silently lose tools."""
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = None
    assert orchestrator._tools_are_enabled() is True


# --- agent step ceiling ---------------------------------------------------

@pytest.mark.parametrize(
    "requested,expected",
    [
        (5, 5),            # under the ceiling, honoured
        (10, 10),          # exactly the ceiling
        (50, 10),          # over the ceiling, clamped
        (10_000_000, 10),  # the cost-multiplication case
        (0, 10),           # nonsense, falls back to configured
        (-3, 10),
        (None, 10),
        ("100", 10),       # a string off a crafted frame
        (True, 10),        # bool is an int subclass; must not become 1 step
        (3.9, 10),
    ],
)
def test_agent_steps_are_clamped(requested, expected):
    assert _orchestrator()._clamp_agent_steps(requested) == expected


def test_ceiling_follows_operator_configuration():
    assert _orchestrator(agent_max_steps=3)._clamp_agent_steps(100) == 3
    assert _orchestrator(agent_max_steps=3)._clamp_agent_steps(2) == 2


def test_ceiling_defaults_when_unconfigured():
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = None
    assert orchestrator._clamp_agent_steps(999) == 10


# --- the enforcement itself, through execute() ----------------------------
# The helpers above are only useful if execute() actually applies them. These
# drive a real orchestrator so a wiring mistake cannot pass by testing the
# helper in isolation.

def _wire_orchestrator(*, tools_enabled: bool):
    event_pub = MagicMock()
    event_pub.publish_warning = AsyncMock()
    repo = InMemorySessionRepository()

    runners = {
        "plain": AsyncMock(return_value={"mode": "plain"}),
        "rag": AsyncMock(return_value={"mode": "rag"}),
        "tools": AsyncMock(return_value={"mode": "tools"}),
        "agent": AsyncMock(return_value={"mode": "agent"}),
    }
    plain_runner = MagicMock()
    plain_runner.run_streaming = runners["plain"]
    rag_runner = MagicMock()
    rag_runner.run_streaming = runners["rag"]
    tools_runner = MagicMock()
    tools_runner.run_streaming = runners["tools"]
    agent_runner = MagicMock()
    agent_runner.run = runners["agent"]

    orchestrator = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=event_pub,
        session_repository=repo,
        plain_mode=plain_runner,
        rag_mode=rag_runner,
        tools_mode=tools_runner,
        agent_mode=agent_runner,
        config_manager=SimpleNamespace(
            app_settings=SimpleNamespace(
                feature_tools_enabled=tools_enabled,
                agent_max_steps=10,
            ),
            llm_config=SimpleNamespace(models={}),
        ),
    )
    return orchestrator, repo, runners


async def _seed(repo):
    sid = uuid.uuid4()
    await repo.create(Session(id=sid, user_email="test@example.com"))
    return sid


@pytest.mark.asyncio
async def test_tool_request_is_discarded_when_the_flag_is_off():
    """A crafted frame must not reach tools mode while tools are disabled."""
    orchestrator, repo, runners = _wire_orchestrator(tools_enabled=False)
    sid = await _seed(repo)

    await orchestrator.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        selected_tools=["calculator_evaluate"],
    )

    runners["tools"].assert_not_awaited()
    runners["plain"].assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_mode_is_discarded_when_the_flag_is_off():
    orchestrator, repo, runners = _wire_orchestrator(tools_enabled=False)
    sid = await _seed(repo)

    await orchestrator.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        agent_mode=True,
        selected_tools=["calculator_evaluate"],
    )

    runners["agent"].assert_not_awaited()
    runners["tools"].assert_not_awaited()
    runners["plain"].assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_request_is_honoured_when_the_flag_is_on():
    """The guard must not break the normal case."""
    orchestrator, repo, runners = _wire_orchestrator(tools_enabled=True)
    sid = await _seed(repo)

    await orchestrator.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        selected_tools=["calculator_evaluate"],
    )

    runners["tools"].assert_awaited_once()
    runners["plain"].assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_clamps_agent_steps_before_dispatch():
    """The clamp must be applied on the value the runner actually receives."""
    orchestrator, repo, runners = _wire_orchestrator(tools_enabled=True)
    sid = await _seed(repo)

    await orchestrator.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        agent_mode=True,
        selected_tools=["calculator_evaluate"],
        agent_max_steps=10_000_000,
    )

    runners["agent"].assert_awaited_once()
    # The runner takes it as `max_steps` (orchestrator reads the clamped
    # kwargs entry when dispatching), so assert on what it actually receives
    # rather than on the name the client used.
    passed = runners["agent"].await_args.kwargs
    assert passed.get("max_steps") == 10


@pytest.mark.asyncio
async def test_execute_passes_a_reasonable_step_count_through():
    """A request under the ceiling must reach the runner unchanged."""
    orchestrator, repo, runners = _wire_orchestrator(tools_enabled=True)
    sid = await _seed(repo)

    await orchestrator.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        agent_mode=True,
        selected_tools=["calculator_evaluate"],
        agent_max_steps=3,
    )

    assert runners["agent"].await_args.kwargs.get("max_steps") == 3
