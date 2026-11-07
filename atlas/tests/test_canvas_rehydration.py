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
from atlas.domain.messages.canvas_context import (
    CANVAS_ARTIFACT_MESSAGE_TYPE,
    format_canvas_block,
    latest_canvas_supplement,
)
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
            "tool_name": tool_name,
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

    def test_display_order_not_start_order_is_the_latest(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            {
                "type": "tool_start", "tool_call_id": "a",
                "tool_name": "atlas_canvas", "arguments": {"content": "A"},
            },
            {
                "type": "tool_start", "tool_call_id": "b",
                "tool_name": "atlas_canvas", "arguments": {"content": "B"},
            },
            {"type": "canvas_content", "tool_call_id": "b", "tool_name": "atlas_canvas", "content": "B-shown"},
            {"type": "canvas_content", "tool_call_id": "a", "tool_name": "atlas_canvas", "content": "A-shown"},
            {
                "type": "tool_complete", "tool_call_id": "b",
                "tool_name": "atlas_canvas", "success": True, "result": "ok",
            },
            {
                "type": "tool_complete", "tool_call_id": "a",
                "tool_name": "atlas_canvas", "success": True, "result": "ok",
            },
        ])
        history = ConversationHistory()
        recorder.flush(history)
        bodies = [item.metadata["canvas_content"] for item in _artifacts(history)]
        assert bodies == ["B-shown", "A-shown"]
        supplement = latest_canvas_supplement(history.messages, set())
        assert "A-shown" in supplement
        assert "B-shown" not in supplement

    def test_reused_call_id_stays_two_documents(self):
        from atlas.application.chat.runs.context import clear_current_run, set_current_run

        # The recorder keeps only events for the run it was built inside.
        set_current_run("run-1", "conv-1")
        try:
            recorder = ToolCallRecorder(None)
        finally:
            clear_current_run()

        def events(step, content):
            identity = {
                "invocation_id": f"run-1:{step}:call_0",
                "run_id": "run-1",
                "step": step,
                "tool_call_id": "call_0",
                "tool_name": "atlas_canvas",
            }
            return [
                {"type": "tool_start", "arguments": {"content": content}, **identity},
                {"type": "canvas_content", "content": content, **identity},
                {"type": "tool_complete", "success": True, "result": "ok", **identity},
            ]

        _play(recorder, events(0, "first document") + events(1, "second document"))
        assert recorder.messages() == []
        history = ConversationHistory()
        recorder.flush(history)
        artifacts = _artifacts(history)
        assert [item.metadata["canvas_content"] for item in artifacts] == [
            "first document", "second document",
        ]
        assert artifacts[0].metadata["artifact_id"] == "run-1:0:call_0"
        assert artifacts[1].metadata["artifact_id"] == "run-1:1:call_0"
        assert artifacts[0].metadata["run_id"] == "run-1"
        assert artifacts[1].metadata["step"] == 1

    def test_canvas_frames_are_stamped_without_a_projection_row(self):
        from unittest.mock import AsyncMock, MagicMock

        from atlas.application.chat.utilities import tool_executor as te
        from atlas.application.chat.utilities.tool_executor import execute_single_tool
        from atlas.domain.messages.models import ToolResult

        tool_call = MagicMock()
        tool_call.id = "call_0"
        tool_call.function.name = "atlas_canvas"
        tool_call.function.arguments = '{"content": "doc"}'

        tool_manager = MagicMock()
        tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "atlas_canvas",
                "parameters": {"properties": {"content": {}}},
            }
        }]
        tool_manager.get_server_for_tool.return_value = "atlas"
        tool_manager.execute_tool = AsyncMock(return_value=ToolResult(
            tool_call_id="call_0",
            content="Canvas content displayed: doc",
            success=True,
        ))

        published = []

        async def _publish(event):
            published.append(event)
            return None

        captured = []

        async def _capture(payload):
            captured.append(payload)

        original = te.publish_execution_event
        te.publish_execution_event = _publish
        try:
            _run(execute_single_tool(
                tool_call=tool_call,
                session_context={
                    "user_email": "u@x.com",
                    "run_id": "run-1",
                    "conversation_id": "conv-1",
                    "agent_step": 1,
                },
                tool_manager=tool_manager,
                update_callback=_capture,
                skip_approval=True,
            ))
        finally:
            te.publish_execution_event = original

        assert published == []
        starts = [item for item in captured if item.get("type") == "tool_start"]
        assert starts[0]["invocation_id"] == "run-1:1:call_0"
        assert starts[0]["run_id"] == "run-1"
        assert starts[0]["step"] == 1
        shown = [item for item in captured if item.get("type") == "canvas_content"]
        assert shown[0]["invocation_id"] == "run-1:1:call_0"
        assert shown[0]["content"] == "doc"
        from atlas.application.chat.runs.context import clear_current_run, set_current_run

        set_current_run("run-1", "conv-1")
        try:
            recorder = ToolCallRecorder(None)
        finally:
            clear_current_run()
        _play(recorder, captured)
        assert recorder.messages() == []
        history = ConversationHistory()
        recorder.flush(history)
        artifact = _artifacts(history)[0]
        assert artifact.metadata["artifact_id"] == "run-1:1:call_0"
        assert artifact.metadata["canvas_content"] == "doc"
        assert artifact.metadata["step"] == 1

    @pytest.mark.parametrize("success", [True, False])
    def test_only_a_successful_write_is_shown(self, success):
        from unittest.mock import MagicMock

        from atlas.application.chat.utilities.event_notifier import notify_tool_complete
        from atlas.domain.messages.models import ToolResult

        tool_call = MagicMock()
        tool_call.id = "c1"
        tool_call.function.name = "atlas_canvas"
        captured = []

        async def _capture(payload):
            captured.append(payload)

        result = ToolResult(tool_call_id="c1", content="ok" if success else "blocked", success=success)
        _run(notify_tool_complete(tool_call, result, {"content": "doc"}, _capture))
        shown = [item for item in captured if item.get("type") == "canvas_content"]
        if success:
            assert shown == [{
                "type": "canvas_content", "content": "doc",
                "tool_call_id": "c1", "tool_name": "atlas_canvas",
            }]
        else:
            assert shown == []

    def test_approval_edit_is_what_later_turns_read(self):
        from unittest.mock import AsyncMock, MagicMock

        from atlas.application.chat.utilities import tool_executor as te
        from atlas.application.chat.utilities.tool_executor import execute_single_tool
        from atlas.domain.messages.models import ToolResult

        tool_call = MagicMock()
        tool_call.id = "c1"
        tool_call.function.name = "atlas_canvas"
        tool_call.function.arguments = '{"content": "draft from the model"}'

        tool_manager = MagicMock()
        tool_manager.get_tools_schema.return_value = [{
            "function": {
                "name": "atlas_canvas",
                "parameters": {"properties": {"content": {}}},
            }
        }]
        tool_manager.get_server_for_tool.return_value = "atlas"
        tool_manager.execute_tool = AsyncMock(return_value=ToolResult(
            tool_call_id="c1",
            content="Canvas content displayed: edited",
            success=True,
        ))

        approval_manager = MagicMock()
        approval_request = MagicMock()
        approval_request.wait_for_response = AsyncMock(return_value={
            "approved": True,
            "arguments": {"content": "edited by the user"},
        })
        approval_manager.create_approval_request.return_value = approval_request
        approval_manager.cleanup_request = MagicMock()

        captured = []

        async def _capture(payload):
            captured.append(payload)

        original_get = te.get_approval_manager
        original_requires = te.requires_approval
        te.get_approval_manager = lambda: approval_manager
        te.requires_approval = lambda name, cfg: (True, True, False)
        try:
            _run(execute_single_tool(
                tool_call=tool_call,
                session_context={"user_email": "u@x.com"},
                tool_manager=tool_manager,
                update_callback=_capture,
                config_manager=MagicMock(),
            ))
        finally:
            te.get_approval_manager = original_get
            te.requires_approval = original_requires

        shown = [item for item in captured if item.get("type") == "canvas_content"]
        assert shown[0]["content"] == "edited by the user"
        started = [item for item in captured if item.get("type") == "tool_start"]
        assert started[0]["arguments"]["content"] == "edited by the user"

        recorder = ToolCallRecorder(None)
        _play(recorder, captured)
        assert recorder.messages() == []
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="write it"))
        recorder.flush(history)
        history.add_message(Message(role=MessageRole.ASSISTANT, content="Done."))
        assert _artifacts(history)[0].metadata["canvas_content"] == "edited by the user"
        llm = history.get_messages_for_llm()
        assert "edited by the user" in llm[-1]["content"]
        assert "draft from the model" not in llm[-1]["content"]

    def test_displayed_frame_overwrites_the_start_arguments(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            {
                "type": "tool_start", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "arguments": {"content": "draft"},
            },
            {"type": "canvas_content", "tool_call_id": "c1", "tool_name": "atlas_canvas", "content": SCRIPT},
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
                "type": "canvas_content", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "content": {"k": "v"},
            },
            {
                "type": "tool_complete", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "success": True, "result": "ok",
            },
        ])
        history = ConversationHistory()
        recorder.flush(history)
        assert _artifacts(history)[0].metadata["canvas_content"] == '{"k": "v"}'

    def test_stop_keeps_the_shown_text_and_does_not_announce_a_row(self):
        seen = []

        async def inner(payload):
            seen.append(payload)

        recorder = ToolCallRecorder(inner)
        _play(recorder, [
            {
                "type": "tool_start", "tool_call_id": "c1",
                "tool_name": "atlas_canvas", "arguments": {"content": SCRIPT},
            },
            {"type": "canvas_content", "tool_call_id": "c1", "tool_name": "atlas_canvas", "content": SCRIPT},
        ])
        history = ConversationHistory()
        recorder.flush(history, mark_incomplete=True)
        _run(recorder.notify_incomplete())
        artifact = _artifacts(history)[0]
        assert artifact.metadata["canvas_content"] == SCRIPT
        assert artifact.metadata["status"] == "interrupted"
        assert not any(p.get("type") == "tool_interrupted" for p in seen)

    def test_stop_before_display_stores_no_draft(self):
        recorder = ToolCallRecorder(None)
        _play(recorder, [{
            "type": "tool_start", "tool_call_id": "c1",
            "tool_name": "atlas_canvas", "arguments": {"content": "draft awaiting approval"},
        }])
        history = ConversationHistory()
        recorder.flush(history, mark_incomplete=True)
        assert _artifacts(history) == []

    @pytest.mark.parametrize("settle", [
        {"type": "tool_error", "error": "Tool blocked by policy hook: no"},
        {"type": "tool_complete", "success": False, "result": "blocked"},
    ])
    def test_denied_write_after_a_shown_one_keeps_the_shown_document(self, settle):
        recorder = ToolCallRecorder(None)
        _play(recorder, [
            *_canvas_events("atlas_canvas", "the real document", tool_call_id="c1"),
            {
                "type": "tool_start", "tool_call_id": "c2",
                "tool_name": "atlas_canvas", "arguments": {"content": "denied draft"},
            },
            {"tool_call_id": "c2", "tool_name": "atlas_canvas", **settle},
        ])
        history = ConversationHistory()
        recorder.flush(history)
        bodies = [item.metadata["canvas_content"] for item in _artifacts(history)]
        assert bodies == ["the real document"]
        assert "denied draft" not in latest_canvas_supplement(history.messages, set())

    def test_another_tools_stamped_frame_is_not_a_document(self):
        from atlas.application.chat.runs.context import clear_current_run, set_current_run

        set_current_run("run-1", "conv-1")
        try:
            recorder = ToolCallRecorder(None)
        finally:
            clear_current_run()
        canvas_identity = {
            "invocation_id": "run-1:0:call_0", "run_id": "run-1", "step": 0,
            "tool_call_id": "call_0", "tool_name": "atlas_canvas",
        }
        # A later step's MCP tool reuses call_0 and emits a canvas update
        # through its tracked callback, which stamps its own identity.
        other_identity = {
            "invocation_id": "run-1:1:call_0", "run_id": "run-1", "step": 1,
            "tool_call_id": "call_0", "tool_name": "demo_render",
        }
        _play(recorder, [
            {"type": "tool_start", "arguments": {"content": "doc"}, **canvas_identity},
            {"type": "canvas_content", "content": "doc", **canvas_identity},
            {"type": "tool_complete", "success": True, "result": "ok", **canvas_identity},
            {"type": "canvas_content", "content": "progress view", **other_identity},
            # The same tool name with a different invocation still must not
            # land on the earlier write.
            {"type": "canvas_content", "content": "stray", **{**canvas_identity, "invocation_id": "run-1:2:call_0"}},
        ])
        history = ConversationHistory()
        recorder.flush(history)
        artifacts = _artifacts(history)
        assert [item.metadata["canvas_content"] for item in artifacts] == ["doc"]
        assert artifacts[0].metadata["artifact_id"] == "run-1:0:call_0"


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

    def test_large_canvas_counts_toward_the_keep_budget(self):
        # Short chat around a large document must not all fit in the keep
        # budget. The document is what makes the earlier turn worth summarizing.
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="hi"))
        history.add_message(canvas_artifact_message({
            "tool_call_id": "c1", "tool_name": "atlas_canvas",
            "canvas_content": "y" * 40_000, "status": "completed",
        }))
        history.add_message(Message(role=MessageRole.ASSISTANT, content="ok"))
        history.add_message(Message(role=MessageRole.USER, content="next"))
        _boundary, segment, _previous = ContextCompactor._history_segment(history, 8_000)
        assert any("y" * 100 in item["content"] for item in segment)


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
