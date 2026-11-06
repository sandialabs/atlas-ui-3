"""Canvas documents survive a reload and stay readable on the next turn (#1157).

The canvas tool used to exist only as a live panel event. Its persisted tool
result was a 100-character echo, and the recorder skipped the call, so a
reloaded conversation had nothing to put back in the panel and the next turn
could not quote the original text.
"""

import asyncio

import pytest

from atlas.application.chat.context.compaction_controller import ContextCompactor
from atlas.application.chat.utilities.canvas_history import canvas_artifact_message
from atlas.application.chat.utilities.conversation_loader import load_messages_into_history
from atlas.application.chat.utilities.tool_history import ToolCallRecorder
from atlas.domain.messages.canvas_context import CANVAS_ARTIFACT_MESSAGE_TYPE, format_canvas_block
from atlas.domain.messages.models import (
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    ConversationHistory,
    Message,
    MessageRole,
)
from atlas.modules.chat_history.database import reset_engine

SCRIPT = "print('hello')\nvalue = 1\nCANVAS>>>\nprint('bye')\n"
LONG = "x" * 9000


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def _run(coro):
    return asyncio.run(coro)


def _play(recorder, events):
    async def play():
        for event in events:
            await recorder(event)

    _run(play())


def _canvas_events(tool_name, content, tool_call_id="c1", result="Canvas content displayed: ..."):
    return [
        {
            "type": "tool_start",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "server_name": "atlas",
            "arguments": {"content": content},
        },
        {
            "type": "canvas_content",
            "tool_call_id": tool_call_id,
            "content": content,
        },
        {
            "type": "tool_complete",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "success": True,
            "result": result,
        },
    ]


def _artifacts(history):
    return [
        m for m in history.messages
        if m.metadata.get("message_type") == CANVAS_ARTIFACT_MESSAGE_TYPE
    ]


class TestRecorderKeepsCanvasText:
    @pytest.mark.parametrize("tool_name", ["atlas_canvas", "canvas_canvas"])
    def test_exact_text_is_an_artifact_not_a_tool_row(self, tool_name):
        recorder = ToolCallRecorder(None)
        _play(recorder, _canvas_events(tool_name, SCRIPT, result="Canvas content displayed: " + SCRIPT[:100]))
        assert recorder.messages() == []
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="write a script"))
        recorder.flush(history)
        history.add_message(Message(role=MessageRole.ASSISTANT, content="Wrote it to the canvas."))

        artifacts = _artifacts(history)
        assert len(artifacts) == 1
        assert artifacts[0].metadata["canvas_content"] == SCRIPT
        assert artifacts[0].metadata["artifact_id"] == "c1"
        assert artifacts[0].role == MessageRole.SYSTEM
        assert ">>>" in artifacts[0].metadata["canvas_content"]

        llm = history.get_messages_for_llm()
        assert [m["role"] for m in llm] == ["user", "assistant"]
        assert "tool" not in {m["role"] for m in llm}
        assert "Wrote it to the canvas." in llm[1]["content"]
        assert "print('hello')" in llm[1]["content"]
        assert "print('bye')" in llm[1]["content"]
        # A line that is only the closer must not end the block early.
        assert ">CANVAS>>>" in llm[1]["content"]

    def test_long_text_is_not_elided_and_markup_is_not_escaped(self):
        markup = "<script>alert(1)</script>\n" + LONG
        recorder = ToolCallRecorder(None)
        _play(recorder, _canvas_events("atlas_canvas", markup))
        history = ConversationHistory()
        recorder.flush(history)
        stored = _artifacts(history)[0].metadata["canvas_content"]
        assert stored == markup
        assert "truncated" not in stored
        assert "&lt;" not in stored

    def test_displayed_frame_overwrites_the_start_arguments(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            {
                "type": "tool_start", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "arguments": {"content": "draft"},
            },
            {"type": "canvas_content", "tool_call_id": "c1", "content": SCRIPT},
            {
                "type": "tool_complete", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "success": True, "result": "short",
            },
        ])
        history = ConversationHistory()
        recorder.flush(history)
        assert _artifacts(history)[0].metadata["canvas_content"] == SCRIPT

    def test_progress_canvas_update_is_not_stored(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            {"type": "canvas_content", "content": "intermediate frame"},
            *_canvas_events("atlas_canvas", SCRIPT),
            {"type": "canvas_content", "content": "another tool's frame"},
        ])
        history = ConversationHistory()
        recorder.flush(history)
        artifacts = _artifacts(history)
        assert len(artifacts) == 1
        assert artifacts[0].metadata["canvas_content"] == SCRIPT

    def test_empty_content_is_not_persisted(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, _canvas_events("atlas_canvas", ""))
        history = ConversationHistory()
        recorder.flush(history)
        assert history.messages == []

    def test_structured_content_is_stored_as_json(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            {
                "type": "tool_start", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "arguments": {"content": {"k": "v"}},
            },
            {
                "type": "tool_complete", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "success": True, "result": "ok",
            },
        ])
        history = ConversationHistory()
        recorder.flush(history)
        assert _artifacts(history)[0].metadata["canvas_content"] == '{"k": "v"}'

    def test_stop_keeps_the_text_and_does_not_announce_a_row(self):
        seen = []

        async def inner(payload):
            seen.append(payload)

        recorder = ToolCallRecorder(inner)
        _play(recorder, [{
            "type": "tool_start", "tool_call_id": "c1",
            "tool_name": "atlas_canvas", "arguments": {"content": SCRIPT},
        }])
        history = ConversationHistory()
        recorder.flush(history, mark_incomplete=True)
        _run(recorder.notify_incomplete())
        artifact = _artifacts(history)[0]
        assert artifact.metadata["canvas_content"] == SCRIPT
        assert artifact.metadata["status"] == "interrupted"
        assert not any(p.get("type") == "tool_interrupted" for p in seen)


class TestLaterTurnsCanReadTheCanvas:
    def test_checkpoint_still_carries_the_newest_canvas(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="write a script"))
        history.add_message(canvas_artifact_message({
            "tool_call_id": "c1", "tool_name": "atlas_canvas",
            "canvas_content": SCRIPT, "status": "completed",
        }))
        history.add_message(Message(role=MessageRole.ASSISTANT, content="Wrote it."))
        covered = history.messages[-1]
        history.add_message(Message(
            role=MessageRole.SYSTEM,
            content="Context compacted",
            metadata={
                "message_type": CONTEXT_CHECKPOINT_MESSAGE_TYPE,
                CONTEXT_CHECKPOINT_KEY: {
                    "scope": "conversation",
                    "summary": "The user asked for a script.",
                    "through_message_id": str(covered.id),
                    "through_offset": 1,
                },
            },
        ))
        history.add_message(Message(role=MessageRole.USER, content="print that script exactly"))

        llm = history.get_messages_for_llm()
        assert len(llm) == 1
        assert llm[0]["role"] == "user"
        assert "print that script exactly" in llm[0]["content"]
        assert "print('hello')" in llm[0]["content"]
        assert "print('bye')" in llm[0]["content"]
        assert ">CANVAS>>>" in llm[0]["content"]

    def test_fold_caps_the_model_view_without_touching_storage(self):
        huge = "y" * 200_001
        message = canvas_artifact_message({
            "tool_call_id": "c1", "tool_name": "atlas_canvas", "canvas_content": huge,
        })
        block = format_canvas_block([message])
        assert "truncated 1 chars" in block
        assert "y" * 200_000 in block
        assert "y" * 200_001 not in block
        assert message.metadata["canvas_content"] == huge

    def test_summarizer_segment_includes_the_canvas_it_covers(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="write a script"))
        history.add_message(canvas_artifact_message({
            "tool_call_id": "c1", "tool_name": "atlas_canvas",
            "canvas_content": "print(1)\n", "status": "completed",
        }))
        history.add_message(Message(role=MessageRole.ASSISTANT, content="Done."))
        history.add_message(Message(role=MessageRole.USER, content="thanks, keep this turn"))
        _boundary, segment, _previous = ContextCompactor._history_segment(history, 1)
        assert segment
        assert any("print(1)" in item["content"] for item in segment)


class TestRepositoryRoundTrip:
    def test_saved_canvas_reloads_and_folds(self, tmp_path):
        from atlas.modules.chat_history import (
            ConversationRepository,
            get_session_factory,
            init_database,
        )

        recorder = ToolCallRecorder(None)
        _play(recorder, _canvas_events("atlas_canvas", SCRIPT))
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="write a script"))
        recorder.flush(history)
        history.add_message(Message(role=MessageRole.ASSISTANT, content="Wrote it."))
        messages = []
        for msg in history.messages:
            payload = msg.to_dict()
            payload["message_type"] = msg.metadata.get("message_type", "chat")
            messages.append(payload)

        init_database(f"duckdb:///{tmp_path / 'canvas.db'}")
        repo = ConversationRepository(get_session_factory())
        repo.save_conversation(
            conversation_id="conv-1157",
            user_email="user@test.com",
            title="canvas",
            model="test-model",
            messages=messages,
        )
        loaded = repo.get_conversation("conv-1157", "user@test.com")
        stored = next(m for m in loaded["messages"] if m["message_type"] == CANVAS_ARTIFACT_MESSAGE_TYPE)
        assert stored["metadata"]["canvas_content"] == SCRIPT
        assert stored["metadata"]["artifact_id"] == "c1"

        restored = ConversationHistory()
        load_messages_into_history(restored, loaded["messages"], "conv-1157")
        llm = restored.get_messages_for_llm()
        assert "print('hello')" in llm[-1]["content"]
        assert "print('bye')" in llm[-1]["content"]
        assert all(m["role"] != "tool" for m in llm)
