"""Conversation checkpoints in ConversationHistory.get_messages_for_llm (#877)."""

from uuid import uuid4

from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    CHECKPOINT_SCOPE_RUN,
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    ConversationHistory,
    Message,
    MessageRole,
)


def _msg(role, content, **metadata):
    return Message(role=role, content=content, metadata=metadata)


def _checkpoint(through_id, summary="SUMMARY", scope=None):
    record = {"summary": summary, "through_message_id": str(through_id)}
    if scope:
        record["scope"] = scope
    return Message(
        role=MessageRole.SYSTEM,
        content="Context compacted",
        metadata={"message_type": CONTEXT_CHECKPOINT_MESSAGE_TYPE, CONTEXT_CHECKPOINT_KEY: record},
    )


def _history(*messages):
    history = ConversationHistory()
    for m in messages:
        history.add_message(m)
    return history


def _turns(n):
    out = []
    for i in range(n):
        out.append(_msg(MessageRole.USER, f"q{i}"))
        out.append(_msg(MessageRole.ASSISTANT, f"a{i}"))
    return out


class TestCheckpointReconstruction:
    def test_no_checkpoint_is_unchanged(self):
        history = _history(*_turns(2))
        assert [m["content"] for m in history.get_messages_for_llm()] == ["q0", "a0", "q1", "a1"]

    def test_covered_messages_replaced_by_summary(self):
        turns = _turns(3)
        history = _history(*turns, )
        history.add_message(_checkpoint(turns[3].id))  # covers turns 0 and 1
        out = history.get_messages_for_llm()
        assert [m["role"] for m in out] == ["user", "assistant"]
        assert out[0]["content"].startswith("[Context checkpoint")
        assert "SUMMARY" in out[0]["content"]
        assert out[0]["content"].endswith("q2")
        # The original rows are all still there.
        assert len(history.messages) == 7

    def test_standalone_when_nothing_follows(self):
        turns = _turns(1)
        history = _history(*turns)
        history.add_message(_checkpoint(turns[1].id))
        out = history.get_messages_for_llm()
        assert len(out) == 1 and out[0]["role"] == "user"
        # The next turn's prompt takes the block, keeping alternation.
        history.add_message(_msg(MessageRole.USER, "next"))
        out = history.get_messages_for_llm()
        assert len(out) == 1 and out[0]["content"].endswith("next")

    def test_latest_checkpoint_wins(self):
        turns = _turns(3)
        history = _history(*turns[:4])
        history.add_message(_checkpoint(turns[1].id, "OLD"))
        history.add_message(turns[4])
        history.add_message(turns[5])
        history.add_message(_checkpoint(turns[3].id, "NEW"))
        content = history.get_messages_for_llm()[0]["content"]
        assert "NEW" in content and "OLD" not in content

    def test_unknown_through_id_is_ignored(self):
        history = _history(*_turns(2))
        history.add_message(_checkpoint(uuid4()))
        assert len(history.get_messages_for_llm()) == 4

    def test_run_scope_is_record_only(self):
        turns = _turns(2)
        history = _history(*turns)
        history.add_message(_checkpoint(turns[1].id, scope=CHECKPOINT_SCOPE_RUN))
        assert len(history.get_messages_for_llm()) == 4

    def test_malformed_record_is_ignored(self):
        history = _history(*_turns(1))
        history.add_message(Message(
            role=MessageRole.SYSTEM, content="x",
            metadata={"message_type": CONTEXT_CHECKPOINT_MESSAGE_TYPE, CONTEXT_CHECKPOINT_KEY: "bad"},
        ))
        assert len(history.get_messages_for_llm()) == 2

    def test_rewind_drops_checkpoint(self):
        turns = _turns(3)
        history = _history(*turns)
        history.add_message(_checkpoint(turns[3].id))
        history.truncate_at_user_index(1)  # removes q1 onward, checkpoint too
        assert [m["content"] for m in history.get_messages_for_llm()] == ["q0", "a0"]

    def test_covered_digests_not_folded(self):
        turns = _turns(2)
        turns[1].metadata[AGENT_TOOL_DIGEST_KEY] = "OLD DIGEST"
        turns[3].metadata[AGENT_TOOL_DIGEST_KEY] = "NEW DIGEST"
        history = _history(*turns)
        history.add_message(_checkpoint(turns[1].id))
        joined = "\n".join(m["content"] for m in history.get_messages_for_llm())
        assert "NEW DIGEST" in joined and "OLD DIGEST" not in joined

    def test_checkpoint_row_not_counted_as_turn(self):
        turns = _turns(1)
        history = _history(*turns)
        history.add_message(_checkpoint(turns[1].id))
        # Rewind ordinals count user rows only; the checkpoint is a system row.
        assert all(m.role != MessageRole.USER for m in history.messages[2:])


class TestCheckpointAfterReload:
    def _reloaded(self, history):
        """Round-trip through the stored shape, as restore/rehydration does."""
        from atlas.application.chat.utilities.conversation_loader import load_messages_into_history
        stored = [{**m.to_dict(), "message_type": m.metadata.get("message_type", "chat")} for m in history.messages]
        fresh = ConversationHistory()
        load_messages_into_history(fresh, stored, "c")
        return fresh

    def _compacted(self):
        turns = _turns(3)
        history = _history(*turns)
        row = _checkpoint(turns[3].id)
        row.metadata[CONTEXT_CHECKPOINT_KEY]["through_offset"] = 3  # row is index 6, a1 is index 3
        history.add_message(row)
        return history

    def test_offset_resolves_after_reload(self):
        history = self._compacted()
        reloaded = self._reloaded(history)
        assert reloaded.messages[0].id != history.messages[0].id  # ids are fresh
        assert reloaded.get_messages_for_llm() == history.get_messages_for_llm()
        assert reloaded.get_messages_for_llm()[0]["content"].startswith("[Context checkpoint")

    def test_offset_survives_dropped_head(self):
        history = self._compacted()
        history.messages = history.messages[2:]  # a saved slice drops leading rows
        reloaded = self._reloaded(history)
        out = reloaded.get_messages_for_llm()
        assert out[0]["content"].startswith("[Context checkpoint") and out[0]["content"].endswith("q2")

    def test_bad_offset_is_ignored(self):
        history = self._compacted()
        history.messages[-1].metadata[CONTEXT_CHECKPOINT_KEY]["through_offset"] = 99
        reloaded = self._reloaded(history)
        assert len(reloaded.get_messages_for_llm()) == 6
