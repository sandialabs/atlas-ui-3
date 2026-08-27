"""Tests for the agent steering transport routing and leftover handling.

Issue #824. The loop-level injection is covered in ``test_agentic_loop.py``;
this file covers the transport routing decision (``should_steer``) and the
runner's leftover surfacing, which the review flagged as the untested hop
between the WebSocket endpoint and the loop.
"""

import asyncio
import os
import sys
from typing import List
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.agent.steering import (
    STEERING_QUEUE_MAXSIZE,
    SteeringChannel,
    should_steer,
)


class _FakeTask:
    """Minimal stand-in for an asyncio.Task with a controllable done() flag."""

    def __init__(self, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def _active(steering=None, task=None, conversation_id="conv-A"):
    return {
        "task": task if task is not None else _FakeTask(done=False),
        "steering": steering,
        "conversation_id": conversation_id,
    }


class TestShouldSteer:
    def test_no_task_does_not_steer(self):
        channel = SteeringChannel()
        channel.activate()
        assert not should_steer({"task": None, "steering": channel}, "conv-A")

    def test_done_task_does_not_steer(self):
        channel = SteeringChannel()
        channel.activate()
        state = _active(steering=channel, task=_FakeTask(done=True))
        assert not should_steer(state, "conv-A")

    def test_inactive_channel_does_not_steer(self):
        # A turn that requested agent mode but fell back to a non-agent turn
        # never activates the channel, so a message must start a fresh turn.
        channel = SteeringChannel()  # active stays False
        state = _active(steering=channel)
        assert not should_steer(state, "conv-A")

    def test_no_channel_does_not_steer(self):
        state = {"task": _FakeTask(done=False), "steering": None, "conversation_id": "x"}
        assert not should_steer(state, "x")

    def test_active_channel_same_conversation_steers(self):
        channel = SteeringChannel()
        channel.activate()
        state = _active(steering=channel)
        assert should_steer(state, "conv-A")

    def test_different_conversation_does_not_steer(self):
        # A message typed after the user switched conversations must start a
        # fresh turn in the new conversation, not inject into the old one.
        channel = SteeringChannel()
        channel.activate()
        state = _active(steering=channel, conversation_id="conv-A")
        assert not should_steer(state, "conv-B")

    def test_both_none_conversation_ids_steer(self):
        # No conversation binding on either side is the normal first-turn case.
        channel = SteeringChannel()
        channel.activate()
        state = {"task": _FakeTask(done=False), "steering": channel,
                 "conversation_id": None}
        assert should_steer(state, None)

    def test_stale_channel_after_turn_ends_does_not_steer(self):
        # After the runner deactivates the channel in its finally, a later
        # message must not route into the finished loop.
        channel = SteeringChannel()
        channel.activate()
        channel.deactivate()
        state = _active(steering=channel)
        assert not should_steer(state, "conv-A")


class TestSteeringChannelBounds:
    def test_queue_is_bounded(self):
        channel = SteeringChannel()
        assert channel.queue.maxsize == STEERING_QUEUE_MAXSIZE
        for i in range(STEERING_QUEUE_MAXSIZE):
            channel.queue.put_nowait(f"msg-{i}")
        with pytest.raises(asyncio.QueueFull):
            channel.queue.put_nowait("overflow")

    def test_drain_leftovers_returns_undrained_messages(self):
        channel = SteeringChannel()
        channel.queue.put_nowait("a")
        channel.queue.put_nowait("b")
        leftovers = channel.drain_leftovers()
        assert leftovers == ["a", "b"]
        assert not channel.has_pending()


@pytest.mark.asyncio
async def test_agent_runner_surfaces_undrained_steering_as_warning():
    """When the loop stops draining with a steer still queued, the runner
    tells the user to resend instead of silently dropping it (issue #824)."""
    from atlas.application.chat.agent import AgentLoopFactory
    from atlas.application.chat.modes.agent import AgentModeRunner
    from atlas.domain.messages.models import ToolResult
    from atlas.interfaces.llm import LLMProtocol, LLMResponse

    channel = SteeringChannel()

    class _BudgetLLM(LLMProtocol):
        """One tool-call step, then the step budget runs out.

        On the only LLM call it queues a steer AFTER the iteration-boundary
        drain, so the steer is still in the channel when the loop exits at
        max_steps=1 -- a genuine leftover.
        """

        def __init__(self):
            self.step = 0

        async def call_plain(self, *a, **kw):
            return "final"

        async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                  temperature=0.7, **kw):
            self.step += 1
            # Queue the steer during the LLM call (after the top-of-iteration
            # drain), so it is pending when the budget exits the loop.
            channel.queue.put_nowait("late steer")
            return LLMResponse(content="running", tool_calls=[
                MagicMock(id="c1", type="function",
                         function=MagicMock(name="noop", arguments="{}"))
            ])

        async def call_with_rag_and_tools(self, *a, **kw):
            return await self.call_with_tools(a[0], a[1], a[3], "auto")

        async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                    temperature=0.7, **kw):
            resp = await self.call_with_tools(model, messages, tools_schema, tool_choice,
                                              temperature)
            yield resp

        async def stream_with_rag_and_tools(self, *a, **kw):
            async for item in self.stream_with_tools(a[0], a[1], a[3], "auto"):
                yield item

        async def stream_plain(self, *a, **kw):
            yield "final"

    async def fake_execute(tool_call_obj, context=None):
        return ToolResult(tool_call_id="c1", content="ok", success=True)

    tool_mgr = MagicMock()
    tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)
    tool_mgr.get_tools_schema = MagicMock(return_value=[
        {"type": "function", "function": {"name": "noop", "parameters": {}}}
    ])

    factory = AgentLoopFactory(llm=_BudgetLLM(), tool_manager=tool_mgr,
                               prompt_provider=None)
    factory.skip_approval = True

    warnings: List[str] = []

    class _Pub:
        async def publish_agent_update(self, update_type=None, **kw):
            pass

        async def publish_warning(self, message=None, **kw):
            if message:
                warnings.append(message)

        async def publish_token_stream(self, *a, **kw):
            pass

        async def publish_chat_response(self, *a, **kw):
            pass

        async def publish_response_complete(self, *a, **kw):
            pass

    runner = AgentModeRunner(agent_loop_factory=factory, event_publisher=_Pub(),
                             default_strategy="agentic")

    from atlas.domain.sessions.models import Session
    session = Session(id=uuid4(), user_email="t@example.com")
    session.context["conversation_id"] = "conv-A"

    await runner.run(
        session=session, model="m",
        messages=[{"role": "user", "content": "go"}],
        selected_tools=["noop"], selected_data_sources=None,
        max_steps=1, temperature=0.7, steering=channel,
    )

    assert channel.active is False
    assert warnings, "expected a leftover warning to be published"
    assert "not applied" in warnings[0]
    assert channel.queue.empty()
