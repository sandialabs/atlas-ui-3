from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from interfaces.llm import LLMProtocol
from interfaces.tools import ToolManagerProtocol
from modules.prompts.prompt_provider import PromptProvider

from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from ..utilities import error_utils, tool_utils


class ActAgentLoop(AgentLoopProtocol):
    """Pure action agent loop - just execute tools in a loop until done.

    No explicit reasoning or observation steps. The LLM directly decides which
    tools to call and when to finish. Fastest strategy with minimal overhead.

    Exit conditions:
    - LLM calls the "finished" tool with a final_answer
    - No tool calls returned (LLM provides text response)
    - Max steps reached
    """

    def __init__(
        self,
        *,
        llm: LLMProtocol,
        tool_manager: Optional[ToolManagerProtocol],
        prompt_provider: Optional[PromptProvider],
        connection: Any = None,
    ) -> None:
        self.llm = llm
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.connection = connection

    def _extract_finished_args(self, tool_calls: List[Dict[str, Any]]) -> Optional[str]:
        """Extract final_answer from finished tool call if present."""
        try:
            for tc in tool_calls:
                f = tc.get("function") if isinstance(tc, dict) else None
                if f and f.get("name") == "finished":
                    raw_args = f.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                            return args.get("final_answer")
                        except Exception:
                            return None
                    if isinstance(raw_args, dict):
                        return raw_args.get("final_answer")
            return None
        except Exception:
            return None

    async def run(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        context: AgentContext,
        selected_tools: Optional[List[str]],
        data_sources: Optional[List[str]],
        max_steps: int,
        temperature: float,
        event_handler: AgentEventHandler,
    ) -> AgentResult:
        await event_handler(AgentEvent(type="agent_start", payload={"max_steps": max_steps, "strategy": "act"}))

        steps = 0
        final_answer: Optional[str] = None

        # Define the "finished" control tool
        finished_tool_schema = {
            "type": "function",
            "function": {
                "name": "finished",
                "description": "Call this when you have completed the task and are ready to provide a final answer to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "final_answer": {
                            "type": "string",
                            "description": "The final response to provide to the user",
                        },
                    },
                    "required": ["final_answer"],
                    "additionalProperties": False,
                },
            },
        }

        while steps < max_steps and final_answer is None:
            steps += 1
            await event_handler(AgentEvent(type="agent_turn_start", payload={"step": steps}))

            # Build tools schema: user tools + finished tool
            tools_schema: List[Dict[str, Any]] = [finished_tool_schema]
            if selected_tools and self.tool_manager:
                user_tools = await error_utils.safe_get_tools_schema(self.tool_manager, selected_tools)
                tools_schema.extend(user_tools)

            # Call LLM with tools - using "required" to force tool calling during Act phase
            # The LiteLLM caller has fallback logic to "auto" if "required" is not supported
            if data_sources and context.user_email:
                llm_response = await self.llm.call_with_rag_and_tools(
                    model, messages, data_sources, tools_schema, context.user_email, "required", temperature=temperature
                )
            else:
                llm_response = await self.llm.call_with_tools(
                    model, messages, tools_schema, "required", temperature=temperature
                )

            # Process response
            if llm_response.has_tool_calls():
                tool_calls = llm_response.tool_calls or []

                # Check if finished tool was called
                final_answer = self._extract_finished_args(tool_calls)
                if final_answer:
                    break

                # Execute first non-finished tool call
                first_call = None
                for tc in tool_calls:
                    f = tc.get("function") if isinstance(tc, dict) else None
                    if f and f.get("name") != "finished":
                        first_call = tc
                        break

                if first_call is None:
                    # Only finished tool or no valid tools
                    final_answer = llm_response.content or "Task completed."
                    break

                # Execute the tool
                messages.append({
                    "role": "assistant",
                    "content": llm_response.content,
                    "tool_calls": [first_call],
                })

                result = await tool_utils.execute_single_tool(
                    tool_call=first_call,
                    session_context={
                        "session_id": context.session_id,
                        "user_email": context.user_email,
                        "files": context.files,
                    },
                    tool_manager=self.tool_manager,
                    update_callback=(self.connection.send_json if self.connection else None),
                )

                messages.append({
                    "role": "tool",
                    "content": result.content,
                    "tool_call_id": result.tool_call_id,
                })

                # Emit tool results for artifact ingestion
                await event_handler(AgentEvent(type="agent_tool_results", payload={"results": [result]}))
            else:
                # No tool calls - treat content as final answer
                final_answer = llm_response.content or "Task completed."
                break

        # Fallback if no final answer after max steps
        if not final_answer:
            final_answer = await self.llm.call_plain(model, messages, temperature=temperature)

        await event_handler(AgentEvent(type="agent_completion", payload={"steps": steps}))
        return AgentResult(final_answer=final_answer, steps=steps, metadata={"agent_mode": True, "strategy": "act"})
