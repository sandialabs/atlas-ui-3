"""Run one agent turn with streaming off, through the real LiteLLMCaller and
LiteLLM's HTTP client, against the strict mock model (issue #1230)."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.modules.config.config_manager import ConfigManager
from atlas.modules.llm.litellm_caller import LiteLLMCaller


async def main() -> int:
    llm = LiteLLMCaller(llm_config=ConfigManager().llm_config)

    async def execute(tool_call, context=None):
        return ToolResult(tool_call_id=tool_call.id, content="saved", success=True)

    tools = MagicMock()
    tools.execute_tool = AsyncMock(side_effect=execute)
    tools.get_tools_schema = MagicMock(return_value=[
        {"type": "function", "function": {"name": "atlas_canvas", "parameters": {"type": "object", "properties": {}}}},
    ])
    loop = AgenticLoop(llm=llm, tool_manager=tools, prompt_provider=None)
    loop.skip_approval = True
    context = AgentContext(session_id=uuid4(), user_email="test@test.com", files={}, history=ConversationHistory())
    result = await loop.run(
        model="strict-reasoner",
        messages=[{"role": "user", "content": "write notes"}],
        context=context, selected_tools=["atlas_canvas"], data_sources=None,
        max_steps=4, temperature=0.7, event_handler=AsyncMock(), streaming=False,
    )
    print(f"final_answer={result.final_answer!r}")
    leaked = any("PR1230-HIDDEN-REASONING" in (m.content or "") + repr(m.metadata) for m in context.history.messages)
    print(f"reasoning_in_history={leaked}")
    return 0 if result.final_answer == "FINAL-ANSWER-OK" and not leaked else 1


if __name__ == "__main__":
    os.environ.setdefault("LLM_MAX_RETRIES", "0")
    sys.exit(asyncio.run(main()))
