"""Tests for ChatOrchestrator rewind/edit-and-resubmit (issue #142).

When ``rewind_to_user_index`` is supplied, the orchestrator must truncate the
session history at the targeted prior prompt before appending the new (edited)
content, producing a single linear thread (overwrite-in-place).
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

_ORCH_LOGGER = "atlas.application.chat.orchestrator"

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


def _make_orchestrator():
    llm = MagicMock()
    event_pub = MagicMock()
    repo = InMemorySessionRepository()

    plain_runner = MagicMock()
    plain_runner.run_streaming = AsyncMock(return_value={"mode": "plain"})
    rag_runner = MagicMock()
    rag_runner.run_streaming = AsyncMock(return_value={"mode": "rag"})
    tools_runner = MagicMock()
    tools_runner.run_streaming = AsyncMock(return_value={"mode": "tools"})
    agent_runner = MagicMock()
    agent_runner.run = AsyncMock(return_value={"mode": "agent"})

    orch = ChatOrchestrator(
        llm=llm,
        event_publisher=event_pub,
        session_repository=repo,
        plain_mode=plain_runner,
        rag_mode=rag_runner,
        tools_mode=tools_runner,
        agent_mode=agent_runner,
    )
    return orch, repo


async def _seed_two_turn_session(repo):
    sid = uuid.uuid4()
    session = Session(id=sid, user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="first"))
    session.history.add_message(Message(role=MessageRole.ASSISTANT, content="first reply"))
    session.history.add_message(Message(role=MessageRole.USER, content="second"))
    session.history.add_message(Message(role=MessageRole.ASSISTANT, content="second reply"))
    await repo.create(session)
    return sid, session


@pytest.mark.asyncio
async def test_rewind_truncates_and_replaces_prompt():
    orch, repo = _make_orchestrator()
    sid, session = await _seed_two_turn_session(repo)

    await orch.execute(
        session_id=sid,
        content="second edited",
        model="test-model",
        rewind_to_user_index=1,
    )

    contents = [(m.role, m.content) for m in session.history.messages]
    assert contents == [
        (MessageRole.USER, "first"),
        (MessageRole.ASSISTANT, "first reply"),
        (MessageRole.USER, "second edited"),
    ]


@pytest.mark.asyncio
async def test_rewind_to_first_prompt_drops_all_prior():
    orch, repo = _make_orchestrator()
    sid, session = await _seed_two_turn_session(repo)

    await orch.execute(
        session_id=sid,
        content="brand new start",
        model="test-model",
        rewind_to_user_index=0,
    )

    contents = [(m.role, m.content) for m in session.history.messages]
    assert contents == [(MessageRole.USER, "brand new start")]


@pytest.mark.asyncio
async def test_no_rewind_appends_normally():
    orch, repo = _make_orchestrator()
    sid, session = await _seed_two_turn_session(repo)

    await orch.execute(
        session_id=sid,
        content="third",
        model="test-model",
    )

    # All four prior messages remain; the new prompt is appended.
    assert len(session.history.messages) == 5
    assert session.history.messages[-1].content == "third"
    assert session.history.messages[-1].role == MessageRole.USER


@pytest.mark.asyncio
async def test_out_of_range_rewind_appends_without_truncation():
    orch, repo = _make_orchestrator()
    sid, session = await _seed_two_turn_session(repo)

    await orch.execute(
        session_id=sid,
        content="appended anyway",
        model="test-model",
        rewind_to_user_index=99,
    )

    assert len(session.history.messages) == 5
    assert session.history.messages[-1].content == "appended anyway"


async def _seed_restored_session_with_tool_rows(repo):
    """Simulate a conversation restored from saved history.

    A real persisted thread interleaves tool/system rows the frontend renders
    but does not count toward the user ordinal. This pins that a rewind to the
    Nth *user* prompt still targets the right turn after restore/resume, matching
    the frontend's user-message-ordinal counting (utils/userMessageOrdinal).
    """
    sid = uuid.uuid4()
    session = Session(id=sid, user_email="test@example.com")
    h = session.history
    h.add_message(Message(role=MessageRole.SYSTEM, content="system preamble"))
    h.add_message(Message(role=MessageRole.USER, content="u0"))          # ordinal 0
    h.add_message(Message(role=MessageRole.ASSISTANT, content="a0"))
    h.add_message(Message(role=MessageRole.TOOL, content="t0"))
    h.add_message(Message(role=MessageRole.USER, content="u1"))          # ordinal 1
    h.add_message(Message(role=MessageRole.ASSISTANT, content="a1"))
    h.add_message(Message(role=MessageRole.USER, content="u2"))          # ordinal 2
    h.add_message(Message(role=MessageRole.ASSISTANT, content="a2"))
    await repo.create(session)
    return sid, session


@pytest.mark.asyncio
async def test_rewind_after_restore_targets_right_user_ordinal():
    orch, repo = _make_orchestrator()
    sid, session = await _seed_restored_session_with_tool_rows(repo)

    # Rewind to user ordinal 1 ("u1") -- everything from u1 on is dropped and the
    # edited prompt replaces it; the system/tool rows before u1 are preserved.
    await orch.execute(
        session_id=sid,
        content="u1 edited",
        model="test-model",
        rewind_to_user_index=1,
    )

    contents = [(m.role, m.content) for m in session.history.messages]
    assert contents == [
        (MessageRole.SYSTEM, "system preamble"),
        (MessageRole.USER, "u0"),
        (MessageRole.ASSISTANT, "a0"),
        (MessageRole.TOOL, "t0"),
        (MessageRole.USER, "u1 edited"),
    ]


@pytest.mark.parametrize("bad_index", ["1", 1.0, True, [1], {"n": 1}, object()])
@pytest.mark.asyncio
async def test_non_integer_rewind_index_is_ignored_not_fatal(bad_index):
    """A malformed wire value must not crash the turn or match the wrong prompt.

    The index is read straight off the WebSocket frame; a crafted/buggy client
    could send a string, float, bool, list, dict, etc. None is a valid ordinal,
    so the rewind is ignored and the prompt is appended normally (no truncation,
    no ``TypeError`` from ``user_index < 0``, no ``True``-as-1 mismatch).
    """
    orch, repo = _make_orchestrator()
    sid, session = await _seed_two_turn_session(repo)

    await orch.execute(
        session_id=sid,
        content="resilient append",
        model="test-model",
        rewind_to_user_index=bad_index,
    )

    # Nothing truncated: all four prior messages remain and the prompt is appended.
    assert len(session.history.messages) == 5
    assert session.history.messages[-1].content == "resilient append"
    assert session.history.messages[-1].role == MessageRole.USER


# --- Observability: the warning lines are the only operator signal that the
# FE/BE ordinal contract drifted, so pin their level and the routine no-warn path.


@pytest.mark.asyncio
async def test_routine_rewind_does_not_warn(caplog):
    orch, repo = _make_orchestrator()
    sid, _ = await _seed_two_turn_session(repo)

    with caplog.at_level(logging.WARNING, logger=_ORCH_LOGGER):
        await orch.execute(
            session_id=sid,
            content="second edited",
            model="test-model",
            rewind_to_user_index=1,
        )

    warnings = [r for r in caplog.records if r.name == _ORCH_LOGGER and r.levelno >= logging.WARNING]
    assert warnings == [], f"routine rewind should not warn, got: {[r.message for r in warnings]}"


@pytest.mark.asyncio
async def test_out_of_range_rewind_warns_desync(caplog):
    orch, repo = _make_orchestrator()
    sid, _ = await _seed_two_turn_session(repo)

    with caplog.at_level(logging.WARNING, logger=_ORCH_LOGGER):
        await orch.execute(
            session_id=sid,
            content="appended anyway",
            model="test-model",
            rewind_to_user_index=99,
        )

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("removed nothing" in m and "desync" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_non_integer_rewind_warns(caplog):
    orch, repo = _make_orchestrator()
    sid, _ = await _seed_two_turn_session(repo)

    with caplog.at_level(logging.WARNING, logger=_ORCH_LOGGER):
        await orch.execute(
            session_id=sid,
            content="resilient append",
            model="test-model",
            rewind_to_user_index="not-an-int",
        )

    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("non-integer index" in m for m in msgs), msgs
