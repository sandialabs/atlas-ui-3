"""Compaction wired into the agent loop and the orchestrator (#877)."""

import uuid
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.application.chat.context.compaction_controller import ContextCompactor
from atlas.application.chat.context.compaction_policy import CompactionPolicy
from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.errors import ContextWindowExceededError
from atlas.domain.messages.models import (
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    ConversationHistory,
    Message,
    MessageRole,
    ToolResult,
)
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.interfaces.llm import LLMResponse
from atlas.modules.config.models import LLMConfig, ModelConfig


def _config(window):
    return SimpleNamespace(
        llm_config=LLMConfig(models={"m": ModelConfig(
            model_name="m", model_url="http://localhost/v1", context_window=window, max_tokens=500,
        )}),
        app_settings=SimpleNamespace(),
    )


def _words(n, tag):
    return " ".join(f"{tag}{i}" for i in range(n))


class OverflowLLM:
    """Tool-calling double: scripted responses, some of them overflow errors."""

    def __init__(self, script):
        self.script = list(script)
        self.tool_calls = 0
        self.summaries = 0

    async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                              temperature=0.7, user_email=None):
        self.tool_calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def call_plain(self, model, messages, temperature=None, max_tokens=None, user_email=None):
        self.summaries += 1
        return "## Goal\naudit\n## Accomplished\nran grep"


def _tool_manager(executions: List[str]):
    async def execute(tool_call, context=None):
        executions.append(tool_call.id)
        return ToolResult(tool_call_id=tool_call.id, content=_words(50, "out"), success=True)

    mgr = MagicMock()
    mgr.execute_tool = AsyncMock(side_effect=execute)
    mgr.get_tools_schema = MagicMock(return_value=[{"type": "function", "function": {"name": "grep", "parameters": {}}}])
    return mgr


def _call(call_id):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name="grep", arguments="{}"))


def _long_messages():
    messages = [{"role": "system", "content": "SYS"}]
    for i in range(12):
        messages.append({"role": "user", "content": _words(150, f"u{i}_")})
        messages.append({"role": "assistant", "content": _words(150, f"a{i}_")})
    messages.append({"role": "user", "content": "Now audit the repository"})
    return messages


async def _run_loop(llm, executions, auto=True, retries=2, window=8000):
    history = ConversationHistory()
    compactor = ContextCompactor(llm=llm, config_manager=_config(window), event_publisher=None)
    guard = compactor.run_guard(
        model="m", user_email="u@x", history=history,
        policy=CompactionPolicy(auto=auto, threshold_percent=99, max_retries=retries),
    )
    context = AgentContext(session_id=uuid.uuid4(), user_email="u@x", files={}, history=history, compaction=guard)
    loop = AgenticLoop(llm=llm, tool_manager=_tool_manager(executions), prompt_provider=None)
    loop.skip_approval = True

    async def handler(event):
        return None

    messages = _long_messages()
    result = await loop.run(
        model="m", messages=messages, context=context, selected_tools=["grep"],
        data_sources=None, max_steps=5, temperature=0.7, event_handler=handler,
    )
    return result, messages, history


class TestAgentLoopRecovery:
    @pytest.mark.asyncio
    async def test_overflow_compacts_and_resumes(self):
        llm = OverflowLLM([
            LLMResponse(content="checking", tool_calls=[_call("c1")]),
            ContextWindowExceededError("too long"),
            LLMResponse(content="All done."),
        ])
        executions: List[str] = []
        result, messages, history = await _run_loop(llm, executions, window=40000)
        assert result.final_answer == "All done."
        # The tool ran once; the retry rebuilt the request, not the action.
        assert executions == ["c1"]
        assert llm.summaries >= 1
        assert any("[Context checkpoint" in str(m.get("content")) for m in messages)
        # The latest step's call and result are still paired verbatim.
        tool_rows = [m for m in messages if m.get("role") == "tool"]
        assert tool_rows and tool_rows[-1]["tool_call_id"] == "c1"
        records = [m for m in history.messages if m.metadata.get("message_type") == CONTEXT_CHECKPOINT_MESSAGE_TYPE]
        assert [r.metadata[CONTEXT_CHECKPOINT_KEY]["trigger"] for r in records] == ["overflow"]

    @pytest.mark.asyncio
    async def test_repeated_overflow_surfaces(self):
        llm = OverflowLLM([ContextWindowExceededError("too long")] * 4)
        with pytest.raises(ContextWindowExceededError):
            await _run_loop(llm, [], retries=1, window=40000)
        assert llm.tool_calls == 2  # first try + one bounded retry

    @pytest.mark.asyncio
    async def test_auto_off_surfaces_immediately(self):
        llm = OverflowLLM([ContextWindowExceededError("too long")])
        with pytest.raises(ContextWindowExceededError):
            await _run_loop(llm, [], auto=False, window=40000)
        assert llm.summaries == 0

    @pytest.mark.asyncio
    async def test_near_limit_compacts_before_call(self):
        llm = OverflowLLM([LLMResponse(content="fine")])
        history_result = await _run_loop(llm, [], window=4000)
        result, messages, _ = history_result
        assert result.final_answer == "fine"
        assert llm.summaries >= 1
        assert messages[0] == {"role": "system", "content": "SYS"}


def _orchestrator(window=3000):
    llm = MagicMock()
    llm.call_plain = AsyncMock(return_value="## Goal\nchat\n## Accomplished\nanswered")
    publisher = MagicMock()
    publisher.publish_warning = AsyncMock()
    publisher.publish_response_complete = AsyncMock()
    publisher.send_json = AsyncMock()
    repo = InMemorySessionRepository()
    plain = MagicMock()
    plain.run_streaming = AsyncMock(return_value={"mode": "plain"})
    agent = MagicMock()
    agent.run = AsyncMock(return_value={"mode": "agent"})
    orch = ChatOrchestrator(
        llm=llm, event_publisher=publisher, session_repository=repo,
        plain_mode=plain, rag_mode=MagicMock(), tools_mode=MagicMock(), agent_mode=agent,
    )
    orch.turn_context.compactor.config_manager = _config(window)
    return orch, repo, llm, publisher, plain, agent


async def _session(repo, turns=6):
    session = Session(id=uuid.uuid4(), user_email="u@x")
    for i in range(turns):
        session.history.add_message(Message(role=MessageRole.USER, content=_words(120, f"q{i}_")))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content=_words(120, f"a{i}_")))
    await repo.create(session)
    return session


def _frames(publisher, kind):
    return [c.args[0] for c in publisher.send_json.await_args_list if c.args[0].get("type") == kind]


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_manual_compaction_adds_no_turn(self):
        orch, repo, llm, publisher, plain, _ = _orchestrator(window=100000)
        session = await _session(repo)
        count = len(session.history.messages)
        await orch.execute(session_id=session.id, content="ignored", model="m", user_email="u@x",
                           session=session, compact_only=True, compact_focus="  decisions  ")
        assert len(session.history.messages) == count + 1
        row = session.history.messages[-1]
        assert row.role == MessageRole.SYSTEM
        assert row.metadata[CONTEXT_CHECKPOINT_KEY]["focus"] == "decisions"
        assert not any(m.content == "ignored" for m in session.history.messages)
        plain.run_streaming.assert_not_awaited()
        publisher.publish_response_complete.assert_awaited()
        assert _frames(publisher, "context_compaction")[-1]["status"] == "completed"
        assert _frames(publisher, "context_usage")

    @pytest.mark.asyncio
    async def test_auto_compacts_before_turn(self):
        orch, repo, llm, publisher, plain, _ = _orchestrator(window=3000)
        session = await _session(repo)
        await orch.execute(session_id=session.id, content="next question", model="m",
                           user_email="u@x", session=session, auto_compact=True)
        assert llm.call_plain.await_count >= 1
        sent = plain.run_streaming.await_args.kwargs["messages"]
        assert "[Context checkpoint" in sent[0]["content"]
        assert sent[-1]["content"].endswith("next question") or "next question" in sent[0]["content"]
        usage = _frames(publisher, "context_usage")[-1]
        assert usage["context_window"] == 3000 and usage["window_source"] == "config"
        assert usage["checkpoints"] == 1

    @pytest.mark.asyncio
    async def test_auto_toggle_off_skips(self):
        orch, repo, llm, publisher, plain, _ = _orchestrator(window=3000)
        session = await _session(repo)
        await orch.execute(session_id=session.id, content="next", model="m",
                           user_email="u@x", session=session, auto_compact=False)
        llm.call_plain.assert_not_awaited()
        usage = _frames(publisher, "context_usage")[-1]
        assert usage["auto_compact"] is False
        assert usage["estimated_tokens"] > usage["threshold_tokens"]

    @pytest.mark.asyncio
    async def test_turn_interval_writes_checkpoint(self):
        orch, repo, llm, publisher, plain, _ = _orchestrator(window=200000)
        orch.turn_context.compactor.config_manager.app_settings = SimpleNamespace(context_compaction_turn_interval=3)
        session = await _session(repo, turns=4)
        await orch.execute(session_id=session.id, content="short", model="m",
                           user_email="u@x", session=session, auto_compact=True)
        done = [f for f in _frames(publisher, "context_compaction") if f["status"] == "completed"]
        assert done and done[0]["trigger"] == "turn_interval"
        sent = plain.run_streaming.await_args.kwargs["messages"]
        assert [m["role"] for m in sent] == ["user"] and sent[0]["content"].endswith("short")

    @pytest.mark.asyncio
    async def test_agent_run_gets_guard(self):
        orch, repo, _, _, _, agent = _orchestrator()
        orch.config_manager = SimpleNamespace(
            llm_config=LLMConfig(models={}),
            app_settings=SimpleNamespace(feature_agent_mode_available=True, agent_max_steps=10),
        )
        session = await _session(repo, turns=0)
        await orch.execute(session_id=session.id, content="go", model="m", user_email="u@x",
                           session=session, agent_mode=True, selected_tools=["grep"])
        guard = agent.run.await_args.kwargs["compaction"]
        assert guard is not None and guard.model == "m" and guard.user_email == "u@x"

    @pytest.mark.asyncio
    async def test_manual_compaction_refused_when_feature_off(self):
        orch, repo, _, publisher, _, _ = _orchestrator(window=100000)
        orch.turn_context.compactor.config_manager.app_settings = SimpleNamespace(
            feature_context_compaction_enabled=False)
        session = await _session(repo)
        count = len(session.history.messages)
        await orch.execute(session_id=session.id, content="", model="m", user_email="u@x",
                           session=session, compact_only=True)
        assert len(session.history.messages) == count
        assert _frames(publisher, "context_compaction")[-1]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_usage_frame_names_its_conversation(self):
        orch, repo, _, publisher, _, _ = _orchestrator(window=100000)
        session = await _session(repo)
        session.context["conversation_id"] = "conv-9"
        await orch.execute(session_id=session.id, content="hi", model="m", user_email="u@x", session=session)
        assert _frames(publisher, "context_usage")[-1]["conversation_id"] == "conv-9"
