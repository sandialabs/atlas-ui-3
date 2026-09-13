"""Tests for ChatOrchestrator mode routing (GH #335).

Verifies that the orchestrator routes to the correct mode based on
the presence/absence of selected_data_sources, selected_tools, and
agent_mode flags.  In particular, an empty list for selected_data_sources
must NOT trigger RAG mode.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.settings import configured_agent_max_steps


def _make_orchestrator(
    plain_mock=None,
    rag_mock=None,
    tools_mock=None,
    agent_mock=None,
):
    """Build a ChatOrchestrator with mocked mode runners."""
    llm = MagicMock()
    event_pub = MagicMock()
    event_pub.publish_warning = AsyncMock()
    repo = InMemorySessionRepository()

    plain = plain_mock or AsyncMock(return_value={"mode": "plain"})
    rag = rag_mock or AsyncMock(return_value={"mode": "rag"})
    tools = tools_mock or AsyncMock(return_value={"mode": "tools"})
    agent = agent_mock or AsyncMock(return_value={"mode": "agent"})

    # Create runner-like objects with run_streaming mocks
    plain_runner = MagicMock()
    plain_runner.run_streaming = plain
    rag_runner = MagicMock()
    rag_runner.run_streaming = rag
    tools_runner = MagicMock()
    tools_runner.run_streaming = tools
    agent_runner = MagicMock()
    agent_runner.run = agent

    orch = ChatOrchestrator(
        llm=llm,
        event_publisher=event_pub,
        session_repository=repo,
        plain_mode=plain_runner,
        rag_mode=rag_runner,
        tools_mode=tools_runner,
        agent_mode=agent_runner,
    )
    return orch, repo, {
        "plain": plain, "rag": rag, "tools": tools, "agent": agent,
        "warning": event_pub.publish_warning,
    }


async def _seed_session(repo):
    """Create and store a test session, return its id."""
    sid = uuid.uuid4()
    session = Session(id=sid, user_email="test@example.com")
    await repo.create(session)
    return sid


@pytest.mark.asyncio
async def test_empty_data_sources_routes_to_plain():
    """Empty selected_data_sources must route to plain mode, not RAG."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    await orch.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        selected_data_sources=[],
    )

    mocks["plain"].assert_awaited_once()
    mocks["rag"].assert_not_awaited()


@pytest.mark.asyncio
async def test_none_data_sources_routes_to_plain():
    """None selected_data_sources must route to plain mode."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    await orch.execute(
        session_id=sid,
        content="Hello",
        model="test-model",
        selected_data_sources=None,
    )

    mocks["plain"].assert_awaited_once()
    mocks["rag"].assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_data_sources_routes_to_rag():
    """Non-empty selected_data_sources should route to RAG mode."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    await orch.execute(
        session_id=sid,
        content="search query",
        model="test-model",
        selected_data_sources=["server:source1"],
    )

    mocks["rag"].assert_awaited_once()
    mocks["plain"].assert_not_awaited()


@pytest.mark.asyncio
async def test_tools_with_no_data_sources_routes_to_tools():
    """selected_tools without data sources routes to tools mode."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    # Patch tool authorization to pass tools through
    orch.tool_authorization = MagicMock()
    orch.tool_authorization.filter_authorized_tools = AsyncMock(
        return_value=["server_tool1"]
    )

    await orch.execute(
        session_id=sid,
        content="use a tool",
        model="test-model",
        selected_tools=["server_tool1"],
        selected_data_sources=[],
    )

    mocks["tools"].assert_awaited_once()
    mocks["rag"].assert_not_awaited()
    mocks["plain"].assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_mode_with_tools_routes_to_agent():
    """Agent mode with at least one tool routes to the agent runner."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    # Availability must be proven by settings now that the kill switch fails
    # closed without them (#849 review).
    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(feature_agent_mode_available=True, agent_max_steps=10)
    )
    orch.tool_authorization = MagicMock()
    orch.tool_authorization.filter_authorized_tools = AsyncMock(
        return_value=["server_tool1"]
    )

    await orch.execute(
        session_id=sid,
        content="do a task",
        model="test-model",
        selected_tools=["server_tool1"],
        agent_mode=True,
    )

    mocks["agent"].assert_awaited_once()
    mocks["warning"].assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_mode_with_no_tools_falls_back_to_plain_with_warning():
    """Agent mode with no tools must not route to the agent loop -- the loop
    has nothing to call and tool-seeking prompts can trigger a provider
    rejection that surfaces as an empty/failed response. The orchestrator
    instead warns the user and runs a normal chat turn.
    """
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    await orch.execute(
        session_id=sid,
        content="hello",
        model="test-model",
        selected_tools=[],
        agent_mode=True,
    )

    mocks["agent"].assert_not_awaited()
    mocks["plain"].assert_awaited_once()
    mocks["rag"].assert_not_awaited()
    mocks["warning"].assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_mode_with_rag_sources_and_no_tools_falls_back_to_plain_with_warning():
    """RAG source selection alone should not auto-enable agent tools."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=[],
        selected_data_sources=["atlas_rag:technical-docs"],
        agent_mode=True,
    )

    mocks["agent"].assert_not_awaited()
    mocks["rag"].assert_awaited_once()
    mocks["plain"].assert_not_awaited()
    mocks["warning"].assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_mode_with_selected_atlas_rag_tool_routes_to_agent():
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    # Availability must be proven by settings now that the kill switch fails
    # closed without them (#849 review). The RAG flags let the reachability
    # check see that the selected atlas_rag_query can read the sources.
    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(
            feature_agent_mode_available=True,
            agent_max_steps=10,
            feature_rag_enabled=True,
            feature_atlas_rag_tools_enabled=True,
        )
    )

    await orch.execute(
        session_id=sid,
        content="find the policy",
        model="test-model",
        selected_tools=["atlas_rag_query"],
        selected_data_sources=["atlas_rag:technical-docs"],
        agent_mode=True,
    )

    mocks["agent"].assert_awaited_once()
    mocks["plain"].assert_not_awaited()
    mocks["warning"].assert_not_awaited()

    called_kwargs = mocks["agent"].await_args.kwargs
    assert called_kwargs["selected_tools"] == ["atlas_rag_query"]


@pytest.mark.asyncio
async def test_agent_mode_kill_switch_downgrades_to_plain():
    """``FEATURE_AGENT_MODE_AVAILABLE=false`` must be enforced server-side.

    The client-side gate trusts a config cache that can be stale, so a browser
    may still send ``agent_mode: true`` on its first turn after an admin
    disables the feature; the orchestrator refuses it regardless (#849
    review).
    """
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    orch.tool_authorization = MagicMock()
    orch.tool_authorization.filter_authorized_tools = AsyncMock(
        return_value=["server_tool1"]
    )
    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(feature_agent_mode_available=False, agent_max_steps=10)
    )

    await orch.execute(
        session_id=sid,
        content="do a task",
        model="test-model",
        selected_tools=["server_tool1"],
        agent_mode=True,
    )

    mocks["agent"].assert_not_awaited()
    mocks["tools"].assert_awaited_once()
    # The downgrade must tell the user why the turn ran without agent mode,
    # not merely fire some warning (#849 review).
    mocks["warning"].assert_awaited_once()
    warning_text = mocks["warning"].await_args.kwargs["message"]
    assert "**Agent mode is disabled.**" in warning_text
    assert "agent mode turned off" in warning_text


@pytest.mark.asyncio
async def test_agent_mode_fails_closed_without_app_settings():
    """A missing ``app_settings`` cannot prove the kill switch is on.

    The kill switch is a security control, so an orchestrator built without
    settings refuses agent mode instead of assuming it is allowed (#849
    review). Tests that want agent mode supply explicit settings.
    """
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    orch.tool_authorization = MagicMock()
    orch.tool_authorization.filter_authorized_tools = AsyncMock(
        return_value=["server_tool1"]
    )
    orch.config_manager = SimpleNamespace()  # no app_settings

    assert orch._agent_mode_available() is False

    await orch.execute(
        session_id=sid,
        content="do a task",
        model="test-model",
        selected_tools=["server_tool1"],
        agent_mode=True,
    )

    mocks["agent"].assert_not_awaited()
    mocks["tools"].assert_awaited_once()
    mocks["warning"].assert_awaited_once()


def test_agent_mode_availability_matrix():
    """Direct coverage of the kill-switch predicate's edges (#849 review)."""
    orch, _, _ = _make_orchestrator()

    orch.config_manager = None
    assert orch._agent_mode_available() is False

    orch.config_manager = SimpleNamespace()  # no app_settings
    assert orch._agent_mode_available() is False

    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(feature_agent_mode_available=True)
    )
    assert orch._agent_mode_available() is True

    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(feature_agent_mode_available=False)
    )
    assert orch._agent_mode_available() is False


@pytest.mark.asyncio
async def test_agent_mode_kill_switch_off_is_noop_when_enabled():
    """Default deployments (no kill switch) keep routing agent turns."""
    orch, repo, mocks = _make_orchestrator()
    sid = await _seed_session(repo)

    orch.tool_authorization = MagicMock()
    orch.tool_authorization.filter_authorized_tools = AsyncMock(
        return_value=["server_tool1"]
    )
    orch.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(feature_agent_mode_available=True, agent_max_steps=10)
    )

    await orch.execute(
        session_id=sid,
        content="do a task",
        model="test-model",
        selected_tools=["server_tool1"],
        agent_mode=True,
    )

    mocks["agent"].assert_awaited_once()
    mocks["warning"].assert_not_awaited()


class TestConfiguredAgentMaxSteps:
    """Direct coverage of the shared ``AGENT_MAX_STEPS`` coercion helper.

    The config endpoints expose whatever this returns, so every input the
    orchestrator's own clamp handles must coerce to the same ceiling here
    (#849 review): the non-numeric case that motivated the extraction was
    previously unasserted anywhere.
    """

    def test_missing_settings_object_falls_back_to_default(self):
        assert configured_agent_max_steps(None) == 10

    def test_missing_attribute_falls_back_to_default(self):
        assert configured_agent_max_steps(SimpleNamespace()) == 10

    def test_none_value_falls_back_to_default(self):
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps=None)) == 10

    def test_zero_falls_back_to_default(self):
        # ``or 10`` treats a falsy 0 the same as a missing value.
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps=0)) == 10

    def test_negative_is_floored_to_one(self):
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps=-5)) == 1

    def test_non_numeric_string_falls_back_to_default(self):
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps="abc")) == 10

    def test_numeric_string_is_coerced(self):
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps="30")) == 30

    def test_valid_value_passes_through(self):
        assert configured_agent_max_steps(SimpleNamespace(agent_max_steps=30)) == 30
