"""What rebinding a resumed session to a new conversation leaves behind (#1217)."""

from unittest.mock import MagicMock
from uuid import uuid4

from atlas.application.chat.service import ChatService
from atlas.domain import conversation_classification as conv_class
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import SAVE_FLOOR_CONTEXT_KEY, Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

USER = "alice@example.com"


def _service():
    return ChatService(
        llm=MagicMock(), tool_manager=MagicMock(), connection=MagicMock(),
        config_manager=MagicMock(), session_repository=InMemorySessionRepository(),
    )


def _session_of_an_old_conversation():
    session = Session(id=uuid4(), user_email=USER)
    session.context.update({
        "conversation_id": "conv-old",
        "_restored": True,
        "parent_conversation_id": "conv-parent",
        "workspace_id": "ws-1",
        SAVE_FLOOR_CONTEXT_KEY: {"floor": 3, "locked": True},
        conv_class.SESSION_BINDING_KEY: conv_class.make_binding(conv_class.STATE_LEGACY),
    })
    for text in ("question", "answer"):
        session.history.add_message(Message(role=MessageRole.USER, content=text))
    return session


def test_rebind_returns_a_new_id_and_binds_it():
    service, session = _service(), _session_of_an_old_conversation()
    new_id = service.rebind_to_new_conversation(session)
    assert new_id != "conv-old"
    assert session.context["conversation_id"] == new_id


def test_rebind_drops_what_the_old_conversation_owned():
    service, session = _service(), _session_of_an_old_conversation()
    service.rebind_to_new_conversation(session)
    assert session.history.messages == []
    for key in ("_restored", "parent_conversation_id", SAVE_FLOOR_CONTEXT_KEY,
                conv_class.SESSION_BINDING_KEY):
        assert key not in session.context


def test_rebind_forgets_the_services_floor_state():
    service, session = _service(), _session_of_an_old_conversation()
    service._incognito_save_floor[session.id] = 3
    service._save_floor_locked.add(session.id)
    service.rebind_to_new_conversation(session)
    assert session.id not in service._incognito_save_floor
    assert session.id not in service._save_floor_locked


def test_rebind_keeps_the_sessions_other_context():
    service, session = _service(), _session_of_an_old_conversation()
    service.rebind_to_new_conversation(session)
    # Attached files and the like live in the context and stay.
    assert session.context["workspace_id"] == "ws-1"
