"""Tests for tool-call input/output persistence (issue #684).

Tool calls used to surface only as transient WebSocket events, so the tool
name, input arguments, and output result vanished when a saved conversation was
reloaded or exported. These tests cover the capture/persist/exclude pipeline:

- ``ToolCallRecorder`` turns ``tool_start`` / ``tool_complete`` / ``tool_error``
  events into display-only ``tool_call`` messages while still forwarding the
  events to the real callback.
- ``ConversationHistory.get_messages_for_llm`` excludes those display-only rows
  so they are never replayed to the model.
- The repository round-trips the tool metadata so a reloaded conversation can
  re-render the tool input/output.
"""

import pytest

from atlas.application.chat.utilities.tool_history import ToolCallRecorder
from atlas.domain.messages.models import (
    ConversationHistory,
    Message,
    MessageRole,
)
from atlas.modules.chat_history.database import reset_engine


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import (
        ConversationRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'tool_calls.db'}")
    return ConversationRepository(get_session_factory())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestToolCallRecorder:
    def test_builds_tool_call_message_from_events(self):
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start",
                "tool_call_id": "tc1",
                "tool_name": "calc_add",
                "server_name": "calc",
                "arguments": {"a": 1, "b": 2},
            })
            await recorder({
                "type": "tool_complete",
                "tool_call_id": "tc1",
                "tool_name": "calc_add",
                "success": True,
                "result": "3",
            })

        _run(play())
        messages = recorder.messages()
        assert len(messages) == 1
        msg = messages[0]
        assert msg.role == MessageRole.TOOL
        assert msg.metadata["message_type"] == "tool_call"
        assert msg.metadata["tool_name"] == "calc_add"
        assert msg.metadata["server_name"] == "calc"
        assert msg.metadata["arguments"] == {"a": 1, "b": 2}
        assert msg.metadata["result"] == "3"
        assert msg.metadata["status"] == "completed"

    def test_forwards_events_to_inner_callback(self):
        seen = []

        async def inner(payload):
            seen.append(payload)

        recorder = ToolCallRecorder(inner)
        _run(recorder({"type": "tool_start", "tool_call_id": "x", "tool_name": "t"}))
        assert seen == [{"type": "tool_start", "tool_call_id": "x", "tool_name": "t"}]

    def test_failed_tool_marks_status_failed(self):
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "tc1",
                "tool_name": "do_thing", "server_name": "srv", "arguments": {},
            })
            await recorder({
                "type": "tool_error", "tool_call_id": "tc1",
                "tool_name": "do_thing", "error": "boom",
            })

        _run(play())
        msg = recorder.messages()[0]
        assert msg.metadata["status"] == "failed"
        assert msg.metadata["result"] == "boom"

    def test_preserves_call_order_and_handles_multiple(self):
        recorder = ToolCallRecorder(None)

        async def play():
            for i in (1, 2, 3):
                await recorder({
                    "type": "tool_start", "tool_call_id": f"tc{i}",
                    "tool_name": f"tool_{i}", "server_name": "s", "arguments": {},
                })
                await recorder({
                    "type": "tool_complete", "tool_call_id": f"tc{i}",
                    "tool_name": f"tool_{i}", "success": True, "result": str(i),
                })

        _run(play())
        names = [m.metadata["tool_name"] for m in recorder.messages()]
        assert names == ["tool_1", "tool_2", "tool_3"]

    def test_skips_canvas_tool(self):
        # canvas_canvas renders into the canvas panel, not the transcript.
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "c1",
                "tool_name": "canvas_canvas", "server_name": "canvas",
                "arguments": {"content": "x"},
            })
            await recorder({
                "type": "tool_complete", "tool_call_id": "c1",
                "tool_name": "canvas_canvas", "success": True, "result": "ok",
            })

        _run(play())
        assert recorder.messages() == []

    def test_ignores_unrelated_events(self):
        recorder = ToolCallRecorder(None)
        _run(recorder({"type": "token", "content": "hi"}))
        _run(recorder({"type": "tool_progress", "tool_call_id": "tc1", "progress": 0.5}))
        # progress with no start yields no renderable tool name
        assert recorder.messages() == []

    def test_flush_to_history_appends_then_clears(self):
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "tc1",
                "tool_name": "t", "server_name": "s", "arguments": {},
            })
            await recorder({
                "type": "tool_complete", "tool_call_id": "tc1",
                "tool_name": "t", "success": True, "result": "ok",
            })

        _run(play())

        class _Sess:
            history = ConversationHistory()

        sess = _Sess()
        recorder.flush_to_history(sess)
        assert len(sess.history.messages) == 1
        # Second flush is a no-op (idempotent within a turn).
        recorder.flush_to_history(sess)
        assert len(sess.history.messages) == 1


class TestHistoryExcludesToolCalls:
    def test_tool_call_messages_not_sent_to_llm(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
        history.add_message(Message(
            role=MessageRole.TOOL,
            content="Tool call: calc_add",
            metadata={"message_type": "tool_call", "tool_name": "calc_add"},
        ))
        history.add_message(Message(role=MessageRole.ASSISTANT, content="The answer is 3"))

        llm_messages = history.get_messages_for_llm()
        assert llm_messages == [
            {"role": "user", "content": "add 1 and 2"},
            {"role": "assistant", "content": "The answer is 3"},
        ]


class TestRepositoryRoundTrip:
    def test_tool_call_metadata_survives_save_and_load(self, repo):
        messages = [
            {"role": "user", "content": "add 1 and 2", "message_type": "chat"},
            {
                "role": "tool",
                "content": "Tool call: calc_add",
                "message_type": "tool_call",
                "metadata": {
                    "message_type": "tool_call",
                    "tool_call_id": "tc1",
                    "tool_name": "calc_add",
                    "server_name": "calc",
                    "arguments": {"a": 1, "b": 2},
                    "result": "3",
                    "status": "completed",
                },
            },
            {"role": "assistant", "content": "The answer is 3", "message_type": "chat"},
        ]
        repo.save_conversation(
            conversation_id="conv-684",
            user_email="user@test.com",
            title="math",
            model="gpt-4o",
            messages=messages,
        )

        loaded = repo.get_conversation("conv-684", "user@test.com")
        assert loaded is not None
        tool_msgs = [m for m in loaded["messages"] if m["message_type"] == "tool_call"]
        assert len(tool_msgs) == 1
        meta = tool_msgs[0]["metadata"]
        assert meta["tool_name"] == "calc_add"
        assert meta["arguments"] == {"a": 1, "b": 2}
        assert meta["result"] == "3"
        assert meta["status"] == "completed"
