from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from interfaces.llm import LLMProtocol, LLMResponse
from interfaces.tools import ToolManagerProtocol
from modules.prompts.prompt_provider import PromptProvider

from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from ..utilities import error_utils, tool_utils


class ThinkActAgentLoop(AgentLoopProtocol):
    """UserInput -> Think (planning) -> repeat N times: Act -> Think -> Final Think.

    Differences vs ReActAgentLoop:
    - Single "think" function used for both planning and observation phases.
    - Executes at most one tool per action step.
    - Does not reuse the existing MCP think functions; uses internal prompts via LLM tools.
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
        await event_handler(AgentEvent(type="agent_start", payload={"max_steps": max_steps}))

        steps = 0
        final_answer: Optional[str] = None

        # Initial think
        think_tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "agent_think",
                    "description": "Think step: analyze the user input and context, outline next action or finish. Be concise. At max two sentense. You are only thinkig, not acting right now.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "finish": {"type": "boolean"},
                            "final_answer": {"type": "string"},
                            "next_action_hint": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            }
        ]

        def parse_args(resp: LLMResponse) -> Dict[str, Any]:
            try:
                # Prefer tool args if present
                if getattr(resp, "tool_calls", None):
                    for tc in resp.tool_calls:
                        f = tc.get("function") if isinstance(tc, dict) else None
                        if f and f.get("name") == "agent_think":
                            args = f.get("arguments")
                            if isinstance(args, str):
                                import json
                                try:
                                    return json.loads(args)
                                except Exception:
                                    return {}
                            if isinstance(args, dict):
                                return args
                # Fallback to plain JSON content
                import json
                return json.loads(resp.content or "{}")
            except Exception:
                return {}

        # Emit a synthesized think text to UI
        async def emit_think(text: str, step: int) -> None:
            await event_handler(AgentEvent(type="agent_reason", payload={"message": text, "step": step}))

        # First think
        steps += 1
        await event_handler(AgentEvent(type="agent_turn_start", payload={"step": steps}))
        first_think = await self.llm.call_with_tools(model, messages, think_tools_schema, "required", temperature=temperature)
        think_args = parse_args(first_think)
        await emit_think(first_think.content or "", steps)
        if think_args.get("finish"):
            final_answer = think_args.get("final_answer") or first_think.content
        else:
            # Action loop
            while steps < max_steps and final_answer is None:
                # Act: single tool selection and execution
                tools_schema: List[Dict[str, Any]] = []
                if selected_tools and self.tool_manager:
                    tools_schema = await error_utils.safe_get_tools_schema(self.tool_manager, selected_tools)

                if tools_schema:
                    if data_sources and context.user_email:
                        llm_response = await self.llm.call_with_rag_and_tools(
                            model, messages, data_sources, tools_schema, context.user_email, "auto", temperature=temperature
                        )
                    else:
                        llm_response = await self.llm.call_with_tools(
                            model, messages, tools_schema, "auto", temperature=temperature
                        )

                    if llm_response.has_tool_calls():
                        first_call = (llm_response.tool_calls or [None])[0]
                        if first_call is None:
                            final_answer = llm_response.content or ""
                            break
                        messages.append({"role": "assistant", "content": llm_response.content, "tool_calls": [first_call]})
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
                        messages.append({"role": "tool", "content": result.content, "tool_call_id": result.tool_call_id})
                        # Notify service to ingest artifacts
                        await event_handler(AgentEvent(type="agent_tool_results", payload={"results": [result]}))
                    else:
                        if llm_response.content:
                            final_answer = llm_response.content
                            break

                # Think after action
                steps += 1
                await event_handler(AgentEvent(type="agent_turn_start", payload={"step": steps}))
                think_resp = await self.llm.call_with_tools(model, messages, think_tools_schema, "required", temperature=temperature)
                think_args = parse_args(think_resp)
                await emit_think(think_resp.content or "", steps)
                if think_args.get("finish"):
                    final_answer = think_args.get("final_answer") or think_resp.content
                    break

        if not final_answer:
            final_answer = await self.llm.call_plain(model, messages, temperature=temperature)

        await event_handler(AgentEvent(type="agent_completion", payload={"steps": steps}))
        return AgentResult(final_answer=final_answer, steps=steps, metadata={"agent_mode": True, "strategy": "think-act"})
