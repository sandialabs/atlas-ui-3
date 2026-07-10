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

    def test_large_arguments_and_result_are_elided_for_storage(self):
        # A base64 upload as input / a huge tool output must not be persisted
        # verbatim (it would bloat the saved conversation / DB row). The live UI
        # event is forwarded untouched; only what is written to history is capped.
        from atlas.application.chat.utilities.tool_history import _MAX_STR_CHARS

        big_in = "A" * (_MAX_STR_CHARS + 5000)
        big_out = "B" * (_MAX_STR_CHARS + 9000)
        forwarded = []

        async def inner(payload):
            forwarded.append(payload)

        recorder = ToolCallRecorder(inner)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "tc1",
                "tool_name": "upload", "server_name": "files",
                "arguments": {"data": big_in, "name": "x.png"},
            })
            await recorder({
                "type": "tool_complete", "tool_call_id": "tc1",
                "tool_name": "upload", "success": True, "result": big_out,
            })

        _run(play())

        meta = recorder.messages()[0].metadata
        stored_in = meta["arguments"]["data"]
        stored_out = meta["result"]
        assert len(stored_in) < len(big_in)
        assert stored_in.startswith("A" * 100)
        assert "truncated" in stored_in
        assert len(stored_out) < len(big_out)
        assert "truncated" in stored_out
        # Short sibling values are untouched.
        assert meta["arguments"]["name"] == "x.png"
        # The forwarded live event still carries the full, unredacted payload.
        assert forwarded[0]["arguments"]["data"] == big_in
        assert forwarded[1]["result"] == big_out

    def test_short_payloads_are_not_modified(self):
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "tc1",
                "tool_name": "calc", "server_name": "c",
                "arguments": {"a": 1, "b": "two"},
            })
            await recorder({
                "type": "tool_complete", "tool_call_id": "tc1",
                "tool_name": "calc", "success": True, "result": "3",
            })

        _run(play())
        meta = recorder.messages()[0].metadata
        assert meta["arguments"] == {"a": 1, "b": "two"}
        assert meta["result"] == "3"

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

    def test_auth_required_is_terminal_failed_not_stuck_calling(self):
        # The executor aborts a call after emitting auth_required (no
        # tool_complete/tool_error follows); the persisted row must not stay
        # status="calling" or it reloads as a forever-in-progress tool.
        recorder = ToolCallRecorder(None)

        async def play():
            await recorder({
                "type": "tool_start", "tool_call_id": "tc1",
                "tool_name": "corp_search", "server_name": "corp",
                "arguments": {"q": "x"},
            })
            await recorder({
                "type": "auth_required", "tool_call_id": "tc1",
                "tool_name": "corp_search", "server_name": "corp",
                "message": "Authentication required: token expired",
            })

        _run(play())

        messages = recorder.messages()
        assert len(messages) == 1
        assert messages[0].metadata["status"] == "failed"
        assert messages[0].metadata["result"] == "Authentication required: token expired"

    def test_flush_appends_then_clears(self):
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

        history = ConversationHistory()
        recorder.flush(history)
        assert len(history.messages) == 1
        # Second flush is a no-op (idempotent within a turn).
        recorder.flush(history)
        assert len(history.messages) == 1


class TestToolsModeRunnerWiring:
    """End-to-end: prove ``run()`` actually installs the recorder, captures the
    real streamed tool events, and flushes history as user -> tool_call -> assistant.

    The unit tests above exercise the recorder in isolation; this one exercises
    the seam in ``ToolsModeRunner`` where it is wired into the tool workflow.
    """

    def test_run_installs_recorder_and_persists_in_order(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from atlas.application.chat.modes.tools import ToolsModeRunner
        from atlas.domain.sessions.models import Session
        from atlas.interfaces.llm import LLMResponse

        # Seed the turn's user message exactly as the chat service does before
        # delegating to the runner.
        session = Session()
        session.history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))

        llm_response = LLMResponse(
            content="",
            tool_calls=[{"id": "tc1", "type": "function",
                         "function": {"name": "calc_add", "arguments": "{}"}}],
        )

        forwarded = []

        async def inner_callback(payload):
            forwarded.append(payload)

        async def fake_workflow(**kwargs):
            # The runner must hand the workflow the recorder (not the raw inner
            # callback), so events emitted here get captured for persistence
            # while still reaching the original callback.
            cb = kwargs["update_callback"]
            await cb({"type": "tool_start", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "server_name": "calc",
                      "arguments": {"a": 1, "b": 2}})
            await cb({"type": "tool_complete", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "success": True, "result": "3"})
            return "The answer is 3", []

        publisher = AsyncMock()
        runner = ToolsModeRunner(
            llm=MagicMock(),
            tool_manager=MagicMock(),
            event_publisher=publisher,
        )

        with patch("atlas.application.chat.modes.tools.error_handler.safe_get_tools_schema",
                   new=AsyncMock(return_value=[])), \
             patch("atlas.application.chat.modes.tools.error_handler.safe_call_llm_with_tools",
                   new=AsyncMock(return_value=llm_response)), \
             patch("atlas.application.chat.modes.tools.build_session_context",
                   return_value={}), \
             patch("atlas.application.chat.modes.tools.tool_executor.execute_tools_workflow",
                   new=fake_workflow):
            _run(runner.run(
                session=session,
                model="test-model",
                messages=[{"role": "user", "content": "add 1 and 2"}],
                selected_tools=["calc_add"],
                update_callback=inner_callback,
            ))

        roles = [(m.role, m.metadata.get("message_type")) for m in session.history.messages]
        assert roles == [
            (MessageRole.USER, None),
            (MessageRole.TOOL, "tool_call"),
            (MessageRole.ASSISTANT, None),
        ]
        tool_msg = session.history.messages[1]
        assert tool_msg.metadata["tool_name"] == "calc_add"
        assert tool_msg.metadata["arguments"] == {"a": 1, "b": 2}
        assert tool_msg.metadata["result"] == "3"
        assert session.history.messages[2].content == "The answer is 3"
        # The recorder forwarded the live events to the original callback.
        assert [p["type"] for p in forwarded] == ["tool_start", "tool_complete"]
        # The persisted tool row is excluded from the LLM context.
        llm_msgs = session.history.get_messages_for_llm()
        assert {m["role"] for m in llm_msgs} == {"user", "assistant"}


class TestAgenticLoopWiring:
    """End-to-end: prove the agentic loop installs the recorder and persists
    agent-mode tool calls into the conversation history (the #684 fix only
    covered ``ToolsModeRunner``; agent mode executes tools through the loop's
    own update callback, so it needs its own recorder wiring).
    """

    def _make_llm(self, responses):
        from unittest.mock import AsyncMock, MagicMock

        llm = MagicMock()
        llm.call_with_tools = AsyncMock(side_effect=list(responses))
        return llm

    def _tool_call_response(self):
        from atlas.interfaces.llm import LLMResponse

        return LLMResponse(
            content="",
            tool_calls=[{"id": "tc1", "type": "function",
                         "function": {"name": "calc_add", "arguments": '{"a": 1, "b": 2}'}}],
        )

    def _final_response(self):
        from atlas.interfaces.llm import LLMResponse

        return LLMResponse(content="The answer is 3")

    @staticmethod
    async def _fake_execute_multiple_tools(**kwargs):
        from unittest.mock import MagicMock

        cb = kwargs["update_callback"]
        await cb({"type": "tool_start", "tool_call_id": "tc1",
                  "tool_name": "calc_add", "server_name": "calc",
                  "arguments": {"a": 1, "b": 2}})
        await cb({"type": "tool_complete", "tool_call_id": "tc1",
                  "tool_name": "calc_add", "success": True, "result": "3"})
        result = MagicMock()
        result.content = "3"
        result.tool_call_id = "tc1"
        return [result]

    def _run_loop(self, connection):
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        from atlas.application.chat.agent.agentic_loop import AgenticLoop
        from atlas.application.chat.agent.protocols import AgentContext

        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
        context = AgentContext(
            session_id=uuid4(),
            user_email="user@example.com",
            files={},
            history=history,
        )

        loop = AgenticLoop(
            llm=self._make_llm([self._tool_call_response(), self._final_response()]),
            tool_manager=MagicMock(),
            prompt_provider=None,
            connection=connection,
        )

        async def event_handler(evt):
            pass

        with patch("atlas.application.chat.agent.agentic_loop.error_handler.safe_get_tools_schema",
                   new=AsyncMock(return_value=[])), \
             patch("atlas.application.chat.agent.agentic_loop.tool_executor.execute_multiple_tools",
                   new=self._fake_execute_multiple_tools):
            result = _run(loop.run(
                model="test-model",
                messages=[{"role": "user", "content": "add 1 and 2"}],
                context=context,
                selected_tools=["calc_add"],
                data_sources=None,
                max_steps=5,
                temperature=0.7,
                event_handler=event_handler,
                streaming=False,
            ))
        return result, history

    def test_run_persists_tool_calls_and_forwards_live_events(self):
        from unittest.mock import MagicMock

        forwarded = []

        async def send_json(payload):
            forwarded.append(payload)

        connection = MagicMock()
        connection.send_json = send_json

        result, history = self._run_loop(connection)

        assert result.final_answer == "The answer is 3"
        roles = [(m.role, m.metadata.get("message_type")) for m in history.messages]
        assert roles == [
            (MessageRole.USER, None),
            (MessageRole.TOOL, "tool_call"),
        ]
        tool_msg = history.messages[1]
        assert tool_msg.metadata["tool_name"] == "calc_add"
        assert tool_msg.metadata["server_name"] == "calc"
        assert tool_msg.metadata["arguments"] == {"a": 1, "b": 2}
        assert tool_msg.metadata["result"] == "3"
        assert tool_msg.metadata["status"] == "completed"
        # The recorder forwarded the live events to the websocket callback.
        assert [p["type"] for p in forwarded] == ["tool_start", "tool_complete"]
        # The persisted tool row is excluded from the LLM context.
        assert {m["role"] for m in history.get_messages_for_llm()} == {"user"}

    def test_run_persists_tool_calls_without_connection(self):
        # CLI-style runs have no websocket connection; recording must still work.
        result, history = self._run_loop(connection=None)

        assert result.final_answer == "The answer is 3"
        tool_rows = [m for m in history.messages
                     if m.metadata.get("message_type") == "tool_call"]
        assert len(tool_rows) == 1
        assert tool_rows[0].metadata["tool_name"] == "calc_add"
        assert tool_rows[0].metadata["status"] == "completed"

    def test_run_persists_tool_calls_across_steps_even_with_reused_ids(self):
        # Two loop steps, each with a tool call. The provider reuses the SAME
        # tool_call_id across steps (LiteLLM/local models can restart ids at
        # call_0 per response); per-step flushing must keep each invocation as
        # its own persisted row instead of the second overwriting the first.
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        from atlas.application.chat.agent.agentic_loop import AgenticLoop
        from atlas.application.chat.agent.protocols import AgentContext
        from atlas.interfaces.llm import LLMResponse

        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="add then multiply"))
        context = AgentContext(
            session_id=uuid4(), user_email="user@example.com", files={}, history=history,
        )

        def tool_response(call_id, name):
            return LLMResponse(
                content="",
                tool_calls=[{"id": call_id, "type": "function",
                             "function": {"name": name, "arguments": "{}"}}],
            )

        llm = MagicMock()
        llm.call_with_tools = AsyncMock(side_effect=[
            tool_response("call_0", "calc_add"),
            tool_response("call_0", "calc_mul"),
            LLMResponse(content="The answer is 6"),
        ])

        async def fake_execute_multiple_tools(**kwargs):
            cb = kwargs["update_callback"]
            results = []
            for tc in kwargs["tool_calls"]:
                await cb({"type": "tool_start", "tool_call_id": tc["id"],
                          "tool_name": tc["function"]["name"], "server_name": "calc",
                          "arguments": {}})
                await cb({"type": "tool_complete", "tool_call_id": tc["id"],
                          "tool_name": tc["function"]["name"], "success": True,
                          "result": "ok"})
                result = MagicMock()
                result.content = "ok"
                result.tool_call_id = tc["id"]
                results.append(result)
            return results

        loop = AgenticLoop(
            llm=llm, tool_manager=MagicMock(), prompt_provider=None, connection=None,
        )

        async def event_handler(evt):
            pass

        with patch("atlas.application.chat.agent.agentic_loop.error_handler.safe_get_tools_schema",
                   new=AsyncMock(return_value=[])), \
             patch("atlas.application.chat.agent.agentic_loop.tool_executor.execute_multiple_tools",
                   new=fake_execute_multiple_tools):
            result = _run(loop.run(
                model="test-model",
                messages=[{"role": "user", "content": "add then multiply"}],
                context=context,
                selected_tools=["calc_add", "calc_mul"],
                data_sources=None,
                max_steps=5,
                temperature=0.7,
                event_handler=event_handler,
                streaming=False,
            ))

        assert result.final_answer == "The answer is 6"
        assert result.steps == 3
        tool_rows = [m for m in history.messages
                     if m.metadata.get("message_type") == "tool_call"]
        assert [m.metadata["tool_name"] for m in tool_rows] == ["calc_add", "calc_mul"]
        assert all(m.metadata["status"] == "completed" for m in tool_rows)

    def test_run_persists_failed_tool_call_with_failed_status(self):
        # A tool that errors mid-run must persist as a failed row, not vanish.
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        from atlas.application.chat.agent.agentic_loop import AgenticLoop
        from atlas.application.chat.agent.protocols import AgentContext
        from atlas.interfaces.llm import LLMResponse

        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
        context = AgentContext(
            session_id=uuid4(), user_email="user@example.com", files={}, history=history,
        )

        llm = MagicMock()
        llm.call_with_tools = AsyncMock(side_effect=[
            LLMResponse(
                content="",
                tool_calls=[{"id": "tc1", "type": "function",
                             "function": {"name": "calc_add", "arguments": "{}"}}],
            ),
            LLMResponse(content="The tool failed."),
        ])

        async def fake_execute_multiple_tools(**kwargs):
            cb = kwargs["update_callback"]
            await cb({"type": "tool_start", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "server_name": "calc",
                      "arguments": {"a": 1}})
            await cb({"type": "tool_error", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "error": "division by zero"})
            result = MagicMock()
            result.content = "error"
            result.tool_call_id = "tc1"
            return [result]

        loop = AgenticLoop(
            llm=llm, tool_manager=MagicMock(), prompt_provider=None, connection=None,
        )

        async def event_handler(evt):
            pass

        with patch("atlas.application.chat.agent.agentic_loop.error_handler.safe_get_tools_schema",
                   new=AsyncMock(return_value=[])), \
             patch("atlas.application.chat.agent.agentic_loop.tool_executor.execute_multiple_tools",
                   new=fake_execute_multiple_tools):
            _run(loop.run(
                model="test-model",
                messages=[{"role": "user", "content": "add 1 and 2"}],
                context=context,
                selected_tools=["calc_add"],
                data_sources=None,
                max_steps=5,
                temperature=0.7,
                event_handler=event_handler,
                streaming=False,
            ))

        tool_rows = [m for m in history.messages
                     if m.metadata.get("message_type") == "tool_call"]
        assert len(tool_rows) == 1
        assert tool_rows[0].metadata["status"] == "failed"
        assert tool_rows[0].metadata["result"] == "division by zero"


class TestAgentModeRunnerPersistedOrder:
    """Cross-layer invariant: the loop flushes recorded tool calls before
    ``AgentModeRunner`` appends the final assistant message, so a full
    agent-mode run persists ``user -> tool_call(s) -> assistant``. Guards the
    seam between ``AgenticLoop.run()`` (flush) and ``AgentModeRunner.run()``
    (assistant append) that neither layer can enforce alone.
    """

    def test_agent_mode_run_persists_user_tool_assistant_order(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from atlas.application.chat.agent.factory import AgentLoopFactory
        from atlas.application.chat.modes.agent import AgentModeRunner
        from atlas.domain.sessions.models import Session
        from atlas.interfaces.llm import LLMResponse

        session = Session()
        session.history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))

        responses = iter([
            LLMResponse(
                content="I will calculate the sum.",
                tool_calls=[{"id": "tc1", "type": "function",
                             "function": {"name": "calc_add", "arguments": "{}"}}],
            ),
            LLMResponse(content="The answer is 3"),
        ])

        # AgentModeRunner always runs the loop with streaming=True, so the
        # loop consumes stream_with_tools (one async generator per LLM call).
        def stream_with_tools(*args, **kwargs):
            async def gen():
                yield next(responses)
            return gen()

        llm = MagicMock()
        llm.stream_with_tools = stream_with_tools

        connection = MagicMock()
        connection.send_json = AsyncMock()

        factory = AgentLoopFactory(llm=llm, tool_manager=MagicMock(), connection=connection)
        runner = AgentModeRunner(agent_loop_factory=factory, event_publisher=AsyncMock())

        async def fake_execute_multiple_tools(**kwargs):
            cb = kwargs["update_callback"]
            await cb({"type": "tool_start", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "server_name": "calc",
                      "arguments": {"a": 1, "b": 2}})
            await cb({"type": "tool_complete", "tool_call_id": "tc1",
                      "tool_name": "calc_add", "success": True, "result": "3"})
            result = MagicMock()
            result.content = "3"
            result.tool_call_id = "tc1"
            return [result]

        messages = [{"role": "user", "content": "add 1 and 2"}]
        with patch("atlas.application.chat.agent.agentic_loop.error_handler.safe_get_tools_schema",
                   new=AsyncMock(return_value=[])), \
             patch("atlas.application.chat.agent.agentic_loop.tool_executor.execute_multiple_tools",
                   new=fake_execute_multiple_tools):
            _run(runner.run(
                session=session,
                model="test-model",
                messages=messages,
                selected_tools=["calc_add"],
                selected_data_sources=None,
                max_steps=5,
            ))

        roles = [(m.role, m.metadata.get("message_type")) for m in session.history.messages]
        assert roles == [
            (MessageRole.USER, None),
            (MessageRole.ASSISTANT, "agent_intermediate"),
            (MessageRole.TOOL, "tool_call"),
            (MessageRole.ASSISTANT, None),
        ]
        intermediate_msg = session.history.messages[1]
        assert intermediate_msg.content == "I will calculate the sum."
        assert intermediate_msg.metadata.get("agent_intermediate") is True
        assert intermediate_msg.metadata.get("message_type") == "agent_intermediate"
        tool_msg = session.history.messages[2]
        assert tool_msg.metadata["tool_name"] == "calc_add"
        assert tool_msg.metadata["result"] == "3"
        final_msg = session.history.messages[3]
        assert final_msg.content == "The answer is 3"
        assert final_msg.metadata.get("agent_mode") is True
        assert messages[0]["role"] == "system"
        assert "before each tool call" in messages[0]["content"]


class TestHistoryExcludesToolCalls:
    def test_display_only_messages_not_sent_to_llm(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="add 1 and 2"))
        history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content="I will calculate the sum.",
            metadata={"message_type": "agent_intermediate", "agent_intermediate": True},
        ))
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
