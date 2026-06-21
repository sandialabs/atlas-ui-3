"""Regression test: agent mode must surface generated files to the canvas/session.

Generated artifacts (e.g. a pptx) reach the canvas/session only when the
artifact processor is given a real ``send_json`` update callback -- the canvas
notification helpers early-return when the callback is None. Agent mode used to
pass ``None``, so files were stored (visible in the File library) but never
pushed to the canvas/session, unlike standard tools mode.
"""

import os
import sys
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.agent.protocols import AgentEvent, AgentResult  # type: ignore
from atlas.application.chat.modes.agent import AgentModeRunner  # type: ignore
from atlas.domain.messages.models import ToolResult  # type: ignore
from atlas.domain.sessions.models import Session  # type: ignore


class _FakeLoopEmittingArtifacts:
    """Agent loop that emits one tool-results event, then finishes."""

    def __init__(self, results):
        self._results = results

    async def run(self, *, event_handler, **kwargs):
        await event_handler(AgentEvent(type="agent_start", payload={"max_steps": 3, "strategy": "agentic"}))
        await event_handler(AgentEvent(type="agent_tool_results", payload={"results": self._results}))
        return AgentResult(final_answer="Here is your file.", steps=1, metadata={})


@pytest.mark.asyncio
async def test_agent_mode_passes_real_callback_to_artifact_processor():
    """The artifact processor must receive send_json (not None) so canvas/files
    updates for generated artifacts are emitted."""
    results = [ToolResult(tool_call_id="c1", content="pptx generated", success=True)]

    factory = MagicMock()
    factory.create = MagicMock(return_value=_FakeLoopEmittingArtifacts(results))

    event_publisher = MagicMock()
    event_publisher.send_json = AsyncMock()
    event_publisher.publish_agent_update = AsyncMock()

    artifact_processor = AsyncMock()

    runner = AgentModeRunner(
        agent_loop_factory=factory,
        event_publisher=event_publisher,
        artifact_processor=artifact_processor,
    )

    session = Session(id=uuid4(), user_email="test@example.com")

    await runner.run(
        session=session,
        model="test-model",
        messages=[{"role": "user", "content": "make a pptx"}],
        selected_tools=["pptx_generator_markdown_to_pptx"],
        selected_data_sources=None,
        max_steps=3,
    )

    artifact_processor.assert_awaited_once()
    call_args = artifact_processor.await_args.args
    # Signature: (session, results, update_callback)
    assert call_args[0] is session
    assert call_args[1] == results
    # The callback MUST be the real send_json, not None -- otherwise the canvas
    # notification helpers silently skip the update.
    assert call_args[2] is event_publisher.send_json
    assert call_args[2] is not None
