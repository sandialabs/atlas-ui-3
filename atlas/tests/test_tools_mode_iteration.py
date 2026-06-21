"""Tests for bounded multi-round tool calling in standard (non-agent) tools mode.

Standard tools mode used to do exactly one tool round, then a no-tools synthesis
call. If the model tried to call another tool during synthesis, the provider
rejected the whole stream ("tool_choice is none, but model called a tool") and
the turn failed with a misleading error.

It now runs a bounded loop: after the first round it may take up to
``tools_mode_max_extra_rounds`` (default 3) further rounds to chain dependent
tool calls, guarded against repeating identical calls. When the budget is spent
(or the model keeps repeating), a hardened no-tools synthesis closes the turn,
and a stubborn tool-choice rejection becomes a clear, availability-aware message.
"""

from types import SimpleNamespace
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.application.chat.modes.tools import ToolsModeRunner
from atlas.domain.messages.models import ToolResult
from atlas.interfaces.llm import LLMResponse


def _tc(call_id: str, name: str, arguments: str = "{}"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class ScriptedToolsLLM:
    """stream_with_tools pops one scripted turn per call; stream_plain synthesizes."""

    def __init__(
        self,
        turns: List[Tuple[Optional[str], Optional[list]]],
        synthesis: str = "Final summary.",
        synthesis_error: Optional[Exception] = None,
    ):
        self._turns = list(turns)
        self.tool_stream_calls = 0
        self.synthesis = synthesis
        self.synthesis_error = synthesis_error

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        self.tool_stream_calls += 1
        text, tool_calls = self._turns.pop(0) if self._turns else (None, None)
        if text:
            yield text
        yield LLMResponse(content=text or "", tool_calls=tool_calls)

    async def stream_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                        user_email, tool_choice="auto", temperature=0.7):
        async for item in self.stream_with_tools(model, messages, tools_schema, tool_choice,
                                                  temperature=temperature, user_email=user_email):
            yield item

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        if self.synthesis_error:
            raise self.synthesis_error
        yield self.synthesis

    async def call_plain(self, model, messages, temperature=0.7, user_email=None):
        if self.synthesis_error:
            raise self.synthesis_error
        return self.synthesis


def _config(max_extra_rounds=3, agent_available=False):
    return SimpleNamespace(
        app_settings=SimpleNamespace(
            tools_mode_max_extra_rounds=max_extra_rounds,
            feature_agent_mode_available=agent_available,
        )
    )


def _publisher():
    pub = AsyncMock()
    pub.publish_token_stream = AsyncMock()
    pub.publish_chat_response = AsyncMock()
    pub.publish_response_complete = AsyncMock()
    pub.send_json = AsyncMock()
    return pub


def _runner(llm, config_manager=None):
    tool_manager = MagicMock()
    tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
    return ToolsModeRunner(
        llm=llm,
        tool_manager=tool_manager,
        event_publisher=_publisher(),
        config_manager=config_manager,
    )


def _session():
    session = MagicMock()
    session.history = MagicMock()
    session.history.add_message = MagicMock()
    session.session_id = "s1"
    session.files = {}
    return session


async def _run(runner, messages, executed_names):
    """Run run_streaming with tool execution patched to record call order."""
    async def _execute_multiple(tool_calls, session_context, tool_manager,
                                update_callback=None, config_manager=None, skip_approval=False):
        results = []
        for tc in tool_calls:
            executed_names.append(tc.function.name)
            results.append(ToolResult(tool_call_id=tc.id, content=f"{tc.function.name}=ok", success=True))
        return results

    with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
        mock_te.execute_multiple_tools = _execute_multiple
        mock_te.build_files_manifest = MagicMock(return_value=None)
        return await runner.run_streaming(
            session=_session(),
            model="test-model",
            messages=messages,
            selected_tools=["calc", "pptx"],
        )


@pytest.mark.asyncio
async def test_chains_dependent_tools_then_answers():
    """calc -> pptx -> text answer, all within the round budget, no synthesis error."""
    llm = ScriptedToolsLLM(turns=[
        ("computing", [_tc("c1", "calc", '{"e":"2+2"}')]),
        ("building deck", [_tc("c2", "pptx", '{"title":"X"}')]),
        ("Done! The deck is ready.", None),
    ])
    runner = _runner(llm, _config(max_extra_rounds=3))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "calc then pptx"}], executed)

    assert executed == ["calc", "pptx"]
    assert "Done!" in resp["message"]


@pytest.mark.asyncio
async def test_anti_loop_stops_on_repeated_identical_call():
    """A model repeating the identical tool call is stopped and synthesized."""
    same = ("again", [_tc("c1", "calc", '{"e":"2+2"}')])
    llm = ScriptedToolsLLM(
        turns=[
            ("computing", [_tc("c1", "calc", '{"e":"2+2"}')]),
            same,  # identical signature -> anti-loop guard trips
        ],
        synthesis="Here is the result: 4.",
    )
    runner = _runner(llm, _config(max_extra_rounds=3))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "calc"}], executed)

    # calc executed once; the repeat was refused (not executed again).
    assert executed == ["calc"]
    assert resp["message"] == "Here is the result: 4."


@pytest.mark.asyncio
async def test_extra_round_budget_is_respected():
    """With max_extra_rounds=1, only one continuation round runs before synthesis."""
    llm = ScriptedToolsLLM(
        turns=[
            ("r0", [_tc("c1", "a", "{}")]),
            ("r1", [_tc("c2", "b", "{}")]),
            ("r2", [_tc("c3", "c", "{}")]),  # would run, but budget is spent
        ],
        synthesis="Wrapped up.",
    )
    runner = _runner(llm, _config(max_extra_rounds=1))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    # Round 0 (a) + one extra round (b); c is never reached.
    assert executed == ["a", "b"]
    assert resp["message"] == "Wrapped up."


@pytest.mark.asyncio
async def test_zero_extra_rounds_is_classic_single_round():
    """max_extra_rounds=0 reproduces single-round behavior: execute then synthesize."""
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis="Single-round answer.",
    )
    runner = _runner(llm, _config(max_extra_rounds=0))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert executed == ["calc"]
    assert llm.tool_stream_calls == 1  # no continuation call
    assert resp["message"] == "Single-round answer."


@pytest.mark.asyncio
async def test_graceful_message_mentions_agent_mode_when_available():
    """If synthesis is rejected for a tool-call attempt, the message adapts to availability."""
    err = RuntimeError("litellm.APIError: Tool choice is none, but model called a tool")
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis_error=err,
    )
    runner = _runner(llm, _config(max_extra_rounds=0, agent_available=True))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert "Agent Mode" in resp["message"]
    assert "tried to call another tool" in resp["message"]


@pytest.mark.asyncio
async def test_graceful_message_neutral_when_agent_mode_disabled():
    """When agent mode is admin-disabled, the message must not suggest enabling it."""
    err = RuntimeError("litellm.APIError: Tool choice is none, but model called a tool")
    llm = ScriptedToolsLLM(
        turns=[("r0", [_tc("c1", "calc", "{}")])],
        synthesis_error=err,
    )
    runner = _runner(llm, _config(max_extra_rounds=0, agent_available=False))
    executed: List[str] = []

    resp = await _run(runner, [{"role": "user", "content": "go"}], executed)

    assert "Agent Mode" not in resp["message"]
    assert "follow-up" in resp["message"].lower()
