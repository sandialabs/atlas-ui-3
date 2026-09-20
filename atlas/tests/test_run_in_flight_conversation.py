"""An unsaved conversation with a run in flight is readable and reserved.

Follow-up to issue #884. A tracked run persists nothing until its turn ends,
so between "New Chat + send" and the turn's end the conversation exists only
in the run's session. These tests pin the two consequences:

* opening it resolves to the run session's live transcript (route and
  restore share ``in_flight_conversation``), and
* another user cannot claim the id in that window.
"""

import pytest

from atlas.application.chat.runs import RunRegistry
from atlas.application.chat.runs.in_flight import in_flight_conversation
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

OWNER = "owner@example.com"
OTHER = "other@example.com"


async def _running_conversation(registry, repo, *, title="What is 2+2?"):
    record = registry.start(conversation_id="conv-1", user_email=OWNER, title=title)
    session = Session(id=record.session_id, user_email=OWNER)
    session.context["agent_mode"] = True
    session.history.messages.append(Message(role=MessageRole.USER, content=title))
    tool_row = Message(role=MessageRole.SYSTEM, content="**Tool Call: calc**")
    tool_row.metadata["message_type"] = "tool_call"
    session.history.messages.append(tool_row)
    await repo.create(session)
    return record


@pytest.mark.asyncio
async def test_in_flight_conversation_reads_the_run_session():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    record = await _running_conversation(registry, repo)

    conv = await in_flight_conversation(repo, registry, "conv-1", OWNER)

    assert conv is not None
    assert conv["id"] == "conv-1"
    assert conv["in_flight"] is True
    assert conv["run_id"] == record.run_id
    assert conv["title"] == "What is 2+2?"
    assert conv["metadata"]["agent_mode"] is True
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["user", "system"]
    assert conv["messages"][1]["message_type"] == "tool_call"
    assert conv["messages"][0]["message_type"] == "chat"
    assert conv["message_count"] == 2


@pytest.mark.asyncio
async def test_in_flight_conversation_is_owner_scoped():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    await _running_conversation(registry, repo)

    assert await in_flight_conversation(repo, registry, "conv-1", OTHER) is None
    assert await in_flight_conversation(repo, registry, "conv-1", None) is None
    assert await in_flight_conversation(repo, registry, None, OWNER) is None


@pytest.mark.asyncio
async def test_in_flight_conversation_is_none_once_the_run_ends():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    record = await _running_conversation(registry, repo)
    registry.cancel(record.run_id, OWNER)

    assert await in_flight_conversation(repo, registry, "conv-1", OWNER) is None


@pytest.mark.asyncio
async def test_in_flight_conversation_survives_a_missing_session():
    registry = RunRegistry()
    repo = InMemorySessionRepository()
    registry.start(conversation_id="conv-1", user_email=OWNER)

    assert await in_flight_conversation(repo, registry, "conv-1", OWNER) is None


def test_active_conversation_owner_is_visible_across_users():
    registry = RunRegistry()
    record = registry.start(conversation_id="conv-1", user_email=OWNER)

    assert registry.active_for_conversation_any_owner("conv-1") == OWNER
    assert registry.active_for_conversation_any_owner("conv-2") is None
    assert registry.active_for_conversation_any_owner(None) is None

    registry.cancel(record.run_id, OWNER)
    assert registry.active_for_conversation_any_owner("conv-1") is None


def test_run_record_carries_a_truncated_title():
    registry = RunRegistry()
    record = registry.start(conversation_id="conv-1", user_email=OWNER, title="x" * 500)

    assert len(record.title) == 200
    assert record.to_public_dict()["title"] == record.title

    untitled = registry.start(conversation_id="conv-2", user_email=OWNER, title="")
    assert untitled.title is None
    assert untitled.to_public_dict()["title"] is None
