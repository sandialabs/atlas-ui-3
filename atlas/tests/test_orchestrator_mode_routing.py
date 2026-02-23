"""Tests for ChatOrchestrator mode routing (GH #335).

Verifies that the orchestrator routes to the correct mode based on
the presence/absence of selected_data_sources, selected_tools, and
agent_mode flags.  In particular, an empty list for selected_data_sources
must NOT trigger RAG mode.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


def _make_orchestrator(
    plain_mock=None,
    rag_mock=None,
    tools_mock=None,
    agent_mock=None,
):
    """Build a ChatOrchestrator with mocked mode runners."""
    llm = MagicMock()
    event_pub = MagicMock()
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
    return orch, repo, {"plain": plain, "rag": rag, "tools": tools, "agent": agent}


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
