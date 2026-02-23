from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from atlas.domain.messages.models import ToolResult
from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..utilities import error_handler, file_processor, tool_executor
from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from .streaming_final_answer import stream_final_answer


class ReActAgentLoop(AgentLoopProtocol):
    """Default Reason–Act–Observe agent loop extracted from ChatService._handle_agent_mode.

    Behavior matches existing implementation, including:
    - Reason/Observe via control tool calls with JSON fallback
    - Single tool call per Act step
    - Optional RAG integration
    - Streaming via emitted AgentEvents (adapter maps to notification_utils)
    - User input request & stop polling using connection-driven event handler
    """

    def __init__(
        self,
        *,
        llm: LLMProtocol,
        tool_manager: Optional[ToolManagerProtocol],
        prompt_provider: Optional[PromptProvider],
        connection: Any = None,
        config_manager=None,
    ) -> None:
        self.llm = llm
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.connection = connection
        self.config_manager = config_manager
        self.skip_approval = False

    # ---- Internal helpers (mirroring service implementation) ----
    def _latest_user_question(self, msgs: List[Dict[str, Any]]) -> str:
        for m in reversed(msgs):
            if m.get("role") == "user" and m.get("content"):
                return str(m.get("content"))
        return ""

    def _extract_tool_args(self, llm_response: LLMResponse, fname: str) -> Dict[str, Any]:
        try:
            if not llm_response or not llm_response.tool_calls:
                return {}
            for tc in llm_response.tool_calls:
                f = tc.get("function") if isinstance(tc, dict) else None
                if f and f.get("name") == fname:
                    raw_args = f.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            return json.loads(raw_args)
                        except Exception:
                            return {}
                    if isinstance(raw_args, dict):
                        return raw_args
            return {}
        except Exception:
            return {}

    def _parse_control_json(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            pass
        if not isinstance(text, str):
            return {}
        start = text.rfind("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return {}
        return {}

    async def _poll_control_message(self, timeout_sec: float = 0.01) -> Optional[Dict[str, Any]]:
        if not self.connection:
            return None
        try:
            return await asyncio.wait_for(self.connection.receive_json(), timeout=timeout_sec)
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
        streaming: bool = False,
        event_publisher=None,
    ) -> AgentResult:
        # Agent start
        await event_handler(AgentEvent(type="agent_start", payload={"max_steps": max_steps, "strategy": "react"}))

        steps = 0
        final_response: Optional[str] = None
        last_observation: Optional[str] = None
        user_question = self._latest_user_question(messages)
        files_manifest_obj = file_processor.build_files_manifest({
            "session_id": str(context.session_id),
            "user_email": context.user_email,
            "files": context.files,
            **{},
        })
        files_manifest_text = files_manifest_obj.get("content") if files_manifest_obj else None

        while steps < max_steps:
            steps += 1
            await event_handler(AgentEvent(type="agent_turn_start", payload={"step": steps}))

            # ----- Reason -----
            reason_prompt = None
            if self.prompt_provider:
                reason_prompt = self.prompt_provider.get_agent_reason_prompt(
                    user_question=user_question,
                    files_manifest=files_manifest_text,
                    last_observation=last_observation,
                )
            reason_messages = list(messages)
            if reason_prompt:
                reason_messages.append({"role": "system", "content": reason_prompt})

            reason_tools_schema: List[Dict[str, Any]] = [
                {
                    "type": "function",
                    "function": {
                        "name": "agent_decide_next",
                        "description": (
                            "Plan the next action. If you can answer now, set finish=true and provide final_answer. "
                            "If you need information from the user, set request_input={question: \"...\"}."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "finish": {"type": "boolean"},
                                "final_answer": {"type": "string"},
                                "request_input": {
                                    "type": "object",
                                    "properties": {
                                        "question": {"type": "string"}
                                    },
                                    "required": ["question"],
                                },
                                "next_plan": {"type": "string"},
                                "tools_to_consider": {"type": "array", "items": {"type": "string"}},
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]

            reason_resp: LLMResponse = await self.llm.call_with_tools(
                model, reason_messages, reason_tools_schema, "required", temperature=temperature, user_email=context.user_email
            )
            reason_ctrl = self._extract_tool_args(reason_resp, "agent_decide_next") or self._parse_control_json(reason_resp.content)
            reason_visible_text: str = reason_resp.content or ""
            if not reason_ctrl:
                reason_text_fallback = await self.llm.call_plain(model, reason_messages, temperature=temperature, user_email=context.user_email)
                reason_visible_text = reason_text_fallback
                reason_ctrl = self._parse_control_json(reason_text_fallback)

            await event_handler(AgentEvent(type="agent_reason", payload={"message": reason_visible_text, "step": steps}))

            finish_flag = bool(reason_ctrl.get("finish")) if isinstance(reason_ctrl, dict) else False
            req_input = reason_ctrl.get("request_input") if isinstance(reason_ctrl, dict) else None
            if not req_input and isinstance(reason_visible_text, str) and '"request_input"' in reason_visible_text:
                try:
                    import re as _re
                    m = _re.search(r'"request_input"\s*:\s*\{[^}]*"question"\s*:\s*"([^"]+)"', reason_visible_text)
                    if m:
                        req_input = {"question": m.group(1)}
                except Exception:
                    # Regex parsing failed, continue with JSON fallback
                    pass

            if req_input and isinstance(req_input, dict) and req_input.get("question"):
                await event_handler(AgentEvent(type="agent_request_input", payload={"question": str(req_input.get("question")), "step": steps}))
                user_reply: Optional[str] = None
                for _ in range(600):
                    ctrl = await self._poll_control_message(timeout_sec=0.1)
                    if ctrl and ctrl.get("type") == "agent_user_input" and ctrl.get("content"):
                        user_reply = str(ctrl.get("content"))
                        break
                    if ctrl and ctrl.get("type") == "agent_control" and ctrl.get("action") == "stop":
                        break
                if user_reply:
                    messages.append({"role": "user", "content": user_reply})
                    user_question = user_reply
                    last_observation = "User provided additional input."
                    continue
                break

            if finish_flag:
                final_response = reason_ctrl.get("final_answer") or reason_resp.content
                break

            # ----- Act -----
            tools_schema: List[Dict[str, Any]] = []
            if selected_tools and self.tool_manager:
                tools_schema = await error_handler.safe_get_tools_schema(self.tool_manager, selected_tools)

            tool_results: List[ToolResult] = []
            # Use "required" to force tool calling during Act phase
            # The LiteLLM caller has fallback logic to "auto" if "required" is not supported
            if tools_schema:
                if data_sources and context.user_email:
                    llm_response = await self.llm.call_with_rag_and_tools(
                        model, messages, data_sources, tools_schema, context.user_email, "required", temperature=temperature
                    )
                else:
                    llm_response = await self.llm.call_with_tools(
                        model, messages, tools_schema, "required", temperature=temperature, user_email=context.user_email
                    )

                if llm_response.has_tool_calls():
                    # Execute only first call
                    first_call = (llm_response.tool_calls or [None])[0]
                    if first_call is None:
                        if llm_response.content:
                            final_response = llm_response.content
                            break
                    messages.append({
                        "role": "assistant",
                        "content": llm_response.content,
                        "tool_calls": [first_call],
                    })
                    result = await tool_executor.execute_single_tool(
                        tool_call=first_call,
                        session_context={
                            "session_id": context.session_id,
                            "user_email": context.user_email,
                            "files": context.files,
                        },
                        tool_manager=self.tool_manager,
                        update_callback=(self.connection.send_json if self.connection else None),
                        config_manager=self.config_manager,
                        skip_approval=self.skip_approval,
                    )
                    tool_results.append(result)
                    messages.append({
                        "role": "tool",
                        "content": result.content,
                        "tool_call_id": result.tool_call_id,
                    })

                    # Emit an internal event with actual ToolResult(s) for the service to ingest artifacts
                    await event_handler(AgentEvent(type="agent_tool_results", payload={"results": tool_results}))
                else:
                    if llm_response.content:
                        final_response = llm_response.content
                        break

            # ----- Observe -----
            summaries: List[str] = []
            # We already emitted tool_complete with results above for ingestion; here just build readable summary text.
            # If needed, we can reconstruct from last messages.
            if messages:
                # crude extraction of last tool message
                for msg in reversed(messages):
                    if msg.get("role") == "tool":
                        content_preview = (msg.get("content") or "").strip()
                        if len(content_preview) > 400:
                            content_preview = content_preview[:400] + "..."
                        summaries.append(content_preview)
                        break
            tool_summaries_text = "\n".join(summaries) if summaries else "No tools were executed."

            observe_prompt = None
            if self.prompt_provider:
                observe_prompt = self.prompt_provider.get_agent_observe_prompt(
                    user_question=user_question,
                    tool_summaries=tool_summaries_text,
                    step=steps,
                )
            observe_messages = list(messages)
            if observe_prompt:
                observe_messages.append({"role": "system", "content": observe_prompt})
            observe_tools_schema: List[Dict[str, Any]] = [
                {
                    "type": "function",
                    "function": {
                        "name": "agent_observe_decide",
                        "description": "Given the observations, decide whether to continue another step or finish.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "should_continue": {"type": "boolean"},
                                "final_answer": {"type": "string"},
                                "observation": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]

            observe_resp: LLMResponse = await self.llm.call_with_tools(
                model, observe_messages, observe_tools_schema, "required", temperature=temperature, user_email=context.user_email
            )
            observe_ctrl = self._extract_tool_args(observe_resp, "agent_observe_decide") or self._parse_control_json(observe_resp.content)
            observe_visible_text: str = observe_resp.content or ""
            if not observe_ctrl:
                observe_text_fallback = await self.llm.call_plain(model, observe_messages, temperature=temperature, user_email=context.user_email)
                observe_visible_text = observe_text_fallback
                observe_ctrl = self._parse_control_json(observe_text_fallback)

            await event_handler(AgentEvent(type="agent_observe", payload={"message": observe_visible_text, "step": steps}))

            if isinstance(observe_ctrl, dict):
                final_candidate = observe_ctrl.get("final_answer")
                should_continue = observe_ctrl.get("should_continue", True)
                if final_candidate and isinstance(final_candidate, str) and final_candidate.strip():
                    final_response = final_candidate
                    break
                if not should_continue:
                    final_response = observe_visible_text
                    break

            last_observation = observe_visible_text

        if not final_response:
            if streaming and event_publisher:
                final_response = await stream_final_answer(
                    self.llm, event_publisher, model, messages,
                    temperature, context.user_email,
                )
            else:
                final_response = await self.llm.call_plain(model, messages, temperature=temperature, user_email=context.user_email)

        return AgentResult(final_answer=final_response, steps=steps, metadata={"agent_mode": True})
