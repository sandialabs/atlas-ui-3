"""Tests for ConversationHistory.truncate_at_user_index (issue #142).

The rewind / edit-a-previous-prompt flow addresses messages by their user-message
ordinal so the frontend (which renders extra system/tool rows) and the backend
history stay in agreement. These tests pin that addressing down.
"""

from atlas.domain.messages.models import (
    ConversationHistory,
    Message,
    MessageRole,
)


def _history(*roles_and_contents):
    """Build a ConversationHistory from (role, content) pairs."""
    history = ConversationHistory()
    for role, content in roles_and_contents:
        history.add_message(Message(role=role, content=content))
    return history


def _roles(history):
    return [m.role for m in history.messages]


def test_truncate_at_first_user_clears_everything():
    history = _history(
        (MessageRole.USER, "u0"),
        (MessageRole.ASSISTANT, "a0"),
        (MessageRole.USER, "u1"),
        (MessageRole.ASSISTANT, "a1"),
    )

    removed = history.truncate_at_user_index(0)

    assert history.messages == []
    assert [m.content for m in removed] == ["u0", "a0", "u1", "a1"]


def test_truncate_at_middle_user_keeps_prior_turns():
    history = _history(
        (MessageRole.USER, "u0"),
        (MessageRole.ASSISTANT, "a0"),
        (MessageRole.USER, "u1"),
        (MessageRole.ASSISTANT, "a1"),
        (MessageRole.USER, "u2"),
    )

    removed = history.truncate_at_user_index(1)

    assert [m.content for m in history.messages] == ["u0", "a0"]
    assert _roles(history) == [MessageRole.USER, MessageRole.ASSISTANT]
    assert [m.content for m in removed] == ["u1", "a1", "u2"]


def test_truncate_ignores_intervening_tool_and_system_rows():
    """Ordinal counts user messages only, regardless of tool/system rows."""
    history = _history(
        (MessageRole.USER, "u0"),
        (MessageRole.ASSISTANT, "a0"),
        (MessageRole.TOOL, "t0"),
        (MessageRole.SYSTEM, "s0"),
        (MessageRole.USER, "u1"),
        (MessageRole.ASSISTANT, "a1"),
    )

    removed = history.truncate_at_user_index(1)

    assert [m.content for m in history.messages] == ["u0", "a0", "t0", "s0"]
    assert [m.content for m in removed] == ["u1", "a1"]


def test_truncate_out_of_range_is_noop():
    history = _history(
        (MessageRole.USER, "u0"),
        (MessageRole.ASSISTANT, "a0"),
    )

    removed = history.truncate_at_user_index(5)

    assert removed == []
    assert [m.content for m in history.messages] == ["u0", "a0"]


def test_truncate_negative_index_is_noop():
    history = _history((MessageRole.USER, "u0"))

    removed = history.truncate_at_user_index(-1)

    assert removed == []
    assert len(history.messages) == 1
