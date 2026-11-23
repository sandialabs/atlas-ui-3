"""A fresh conversation id minted after a resume starts with empty history (#1217).

The backstop no longer resets the session, so what the resumed session still
carries must not reach the model or be saved under the new conversation.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from main import _RESUMED_CONVERSATION_KEY, _resume_connection_session

from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"
OLD = "conv-old"


class _Store:
    """Conversation store holding OLD; records every save."""

    def __init__(self):
        self.saved = []

    def get_conversation_owner(self, conversation_id):
        return USER if conversation_id == OLD else None

    def get_conversation(self, conversation_id, user_email):
        return None

    def save_conversation(self, **kwargs):
        self.saved.append(kwargs)
        return {"id": kwargs["conversation_id"]}


async def _setup():
    repo = InMemorySessionRepository()
    store = _Store()
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
        conversation_repository=store,
    )
    service.connection_id = str(uuid4())
    previous = Session(id=uuid4(), user_email=USER, active=False)
    previous.context["conversation_id"] = OLD
    for role, text in ((MessageRole.USER, "old question"), (MessageRole.ASSISTANT, "old answer")):
        previous.history.add_message(Message(role=role, content=text))
    await repo.create(previous)
    return repo, store, service, previous


async def _run_turn(service, session_id, conversation_id):
    seen = {}

    async def fake_execute(**kwargs):
        session = await service.session_repository.get(session_id)
        seen["history"] = [m.content for m in session.history.messages]
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="new answer"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=fake_execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content="new question",
            model="m",
            user_email=USER,
            conversation_id=conversation_id,
        )
    return seen


@pytest.mark.asyncio
async def test_fresh_id_turn_sends_no_old_messages_to_the_model():
    repo, store, service, previous = await _setup()
    resumed = await _resume_connection_session(service, str(previous.id), USER)
    assert resumed is not None and resumed.context.get(_RESUMED_CONVERSATION_KEY)

    seen = await _run_turn(service, previous.id, str(uuid4()))

    assert "old question" not in seen["history"]
    assert "old answer" not in seen["history"]


@pytest.mark.asyncio
async def test_fresh_id_turn_saves_only_its_own_messages():
    repo, store, service, previous = await _setup()
    await _resume_connection_session(service, str(previous.id), USER)
    fresh = str(uuid4())

    await _run_turn(service, previous.id, fresh)

    assert store.saved, "the new conversation should have been saved"
    saved = store.saved[-1]
    assert saved["conversation_id"] == fresh
    texts = [m["content"] if isinstance(m, dict) else m.content for m in saved["messages"]]
    assert "old question" not in texts and "old answer" not in texts
    assert all(call["conversation_id"] != OLD for call in store.saved)
