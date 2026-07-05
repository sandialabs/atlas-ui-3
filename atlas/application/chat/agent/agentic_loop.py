"""Native agentic loop -- no scaffolding, no control tools.

No control tools, no forced tool choice, no separate reasoning phases.
The model naturally decides when to use tools and when to respond with text.

Loop:
  1. Call LLM with user tools + tool_choice="auto"
  2. If response has tool_calls -> execute them -> add results -> loop
  3. If response is text only -> done (that's the final answer)

This strategy works best with models that have strong native tool-use
training but is compatible with any provider via LiteLLM. It is the
simplest and most token-efficient strategy because it trusts the model
to manage its own control flow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..utilities import error_handler, tool_executor
from ..utilities.tool_history import ToolCallRecorder
from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from .streaming_final_answer import stream_final_answer

logger = logging.getLogger(__name__)


def _to_tool_call_dict(tc: Any) -> Dict[str, Any]:
    """Normalize a tool call to a plain OpenAI-format dict.

    Tool calls reach the loop either as attribute-access objects (litellm
    pydantic models from the non-streaming path, or ``SimpleNamespace`` from
    the streaming accumulator) or already as dicts (e.g. from tests). Only
    plain dicts serialize correctly when the assistant message is re-sent to
    the provider on the next turn, so coerce everything to dicts here.
    """
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {"name": getattr(fn, "name", ""), "arguments": getattr(fn, "arguments", "")}
        return {
            "id": tc.get("id"),
            "type": tc.get("type", "function"),
            "function": {
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", ""),
            },
        }
    function = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", None),
        "type": getattr(tc, "type", "function") or "function",
        "function": {
            "name": getattr(function, "name", "") or "",
            "arguments": getattr(function, "arguments", "") or "",
        },
    }


class AgenticLoop(AgentLoopProtocol):
    """Native agentic loop with no scaffolding overhead.

    Unlike the ReAct, Think-Act, and Act strategies, this loop uses zero
    control tools (no ``finished``, ``agent_decide_next``, etc.) and never
    forces tool choice. The model receives the real user tools with
    ``tool_choice="auto"`` and is free to:

    * Call one or more tools, then see results and decide again.
    * Respond with text only, which signals completion.

    This produces the best results with models that have strong native
    tool-use training but works with all providers via LiteLLM.
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
        await event_handler(AgentEvent(
            type="agent_start",
            payload={"max_steps": max_steps, "strategy": "agentic"},
        ))

        tools_schema: List[Dict[str, Any]] = []
        if selected_tools and self.tool_manager:
            tools_schema = await error_handler.safe_get_tools_schema(
                self.tool_manager, selected_tools,
            )

        steps = 0
        final_answer: Optional[str] = None
        use_streaming = streaming and event_publisher

        # Record tool input/output as they stream to the UI so agent-mode tool
        # calls persist in the saved conversation and re-render on reload.
        # Issue #684 covered tools mode by wrapping its update callback; agent
        # mode executes tools through this loop's own callback, so it needs the
        # same wrapper here.
        recorder = ToolCallRecorder(self.connection.send_json if self.connection else None)

        while steps < max_steps:
            steps += 1

            # Sanitize messages: OpenAI rejects empty tool_calls arrays
            for i, msg in enumerate(messages):
                if isinstance(msg, dict) and "tool_calls" in msg and not msg["tool_calls"]:
                    logger.warning("Stripping empty tool_calls from messages[%d]", i)
                    del msg["tool_calls"]

            await event_handler(AgentEvent(
                type="agent_turn_start", payload={"step": steps},
            ))

            llm_response = await self._call_llm(
                model, messages, tools_schema, data_sources,
                context, temperature, use_streaming, event_publisher,
            )

            if not llm_response.has_tool_calls():
                final_answer = llm_response.content or ""
                break

            # Model chose to call tools -- execute all in parallel, then loop
            tool_calls = [tc for tc in (llm_response.tool_calls or []) if tc is not None]
            if not tool_calls:
                final_answer = llm_response.content or ""
                break

            # Convert tool_calls to plain dicts for the assistant message so they
            # round-trip to the next LLM call. The streaming path yields
            # SimpleNamespace objects (for attribute access during execution),
            # but litellm needs dicts when re-sending messages to the LLM --
            # otherwise the tool_calls serialize to an empty array and providers
            # like OpenAI reject the follow-up call (breaking multi-step chains).
            messages.append({
                "role": "assistant",
                "content": llm_response.content,
                "tool_calls": [_to_tool_call_dict(tc) for tc in tool_calls],
            })

            results = await tool_executor.execute_multiple_tools(
                tool_calls=tool_calls,
                session_context={
                    "session_id": context.session_id,
                    "user_email": context.user_email,
                    "files": context.files,
                    # Required so MCP tool calls reuse a persistent session via
                    # MCPSessionManager. Without it, call_tool() falls back to a
                    # single-use session per call and stateful MCP servers raise
                    # session errors between sequential tool calls. Fall back to
                    # the session id (matching ChatService's default conversation
                    # scoping) so direct callers that omit conversation_id still
                    # get one stable persistent session instead of None.
                    "conversation_id": context.conversation_id or str(context.session_id),
                },
                tool_manager=self.tool_manager,
                update_callback=recorder,
                config_manager=self.config_manager,
                skip_approval=self.skip_approval,
            )

            for result in results:
                messages.append({
                    "role": "tool",
                    "content": result.content,
                    "tool_call_id": result.tool_call_id,
                })

            await event_handler(AgentEvent(
                type="agent_tool_results", payload={"results": results},
            ))

        # Max steps exhausted without a text-only response
        if final_answer is None:
            if use_streaming:
                final_answer = await stream_final_answer(
                    self.llm, event_publisher, model, messages,
                    temperature, context.user_email,
                )
            else:
                final_answer = await self.llm.call_plain(
                    model, messages, temperature=temperature,
                    user_email=context.user_email,
                )

        # Persist the turn's tool calls before the caller appends the final
        # assistant message so reloaded history reads
        # user -> tool_call(s) -> assistant (mirrors ToolsModeRunner).
        recorder.flush(context.history)

        await event_handler(AgentEvent(
            type="agent_completion", payload={"steps": steps},
        ))
        return AgentResult(
            final_answer=final_answer,
            steps=steps,
            metadata={"agent_mode": True, "strategy": "agentic"},
        )

    async def _call_llm(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
        data_sources: Optional[List[str]],
        context: AgentContext,
        temperature: float,
        use_streaming: bool,
        event_publisher,
    ) -> LLMResponse:
        """Call the LLM once, optionally streaming text tokens to the UI.

        When streaming is enabled and the response contains only text (no
        tool calls), tokens are published as they arrive so the user sees
        progressive output. When tool calls are present the accumulated
        content and tool calls are returned in the ``LLMResponse``.
        """
        if use_streaming:
            return await self._call_llm_streaming(
                model, messages, tools_schema, data_sources,
                context, temperature, event_publisher,
            )

        if data_sources and context.user_email:
            return await self.llm.call_with_rag_and_tools(
                model, messages, data_sources, tools_schema,
                context.user_email, "auto", temperature=temperature,
            )
        return await self.llm.call_with_tools(
            model, messages, tools_schema, "auto",
            temperature=temperature, user_email=context.user_email,
        )

    async def _call_llm_streaming(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
        data_sources: Optional[List[str]],
        context: AgentContext,
        temperature: float,
        event_publisher,
    ) -> LLMResponse:
        """Stream an LLM call, publishing tokens and returning the final response."""
        if data_sources and context.user_email:
            stream = self.llm.stream_with_rag_and_tools(
                model, messages, data_sources, tools_schema,
                context.user_email, "auto", temperature=temperature,
            )
        else:
            stream = self.llm.stream_with_tools(
                model, messages, tools_schema, "auto",
                temperature=temperature, user_email=context.user_email,
            )

        accumulated_content = ""
        final_response: Optional[LLMResponse] = None
        is_first = True

        try:
            async for item in stream:
                if isinstance(item, str):
                    await event_publisher.publish_token_stream(
                        token=item, is_first=is_first, is_last=False,
                    )
                    accumulated_content += item
                    is_first = False
                elif isinstance(item, LLMResponse):
                    final_response = item
        except Exception:
            logger.exception("Error during streaming LLM call in agentic loop")
            if accumulated_content:
                # Partial text already streamed to the UI -- close the stream and
                # return what we have rather than discarding it.
                await event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )
            else:
                # Nothing was produced before the error. Surface it instead of
                # returning an empty response that looks to the user like the
                # model silently said nothing (e.g. the provider rejecting a
                # mid-stream tool call with "tool_choice is none, but model
                # called a tool"). The caller's error handling publishes a
                # user-visible message.
                raise

        if final_response is None:
            final_response = LLMResponse(content=accumulated_content)

        # If the response is text-only (no tools), close the stream
        if not final_response.has_tool_calls() and accumulated_content:
            await event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )

        return final_response
