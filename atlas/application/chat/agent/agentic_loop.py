"""Claude-native agentic loop -- mirrors Claude Code / Claude Desktop patterns.

No control tools, no forced tool choice, no separate reasoning phases.
The model naturally decides when to use tools and when to respond with text.

Loop:
  1. Call LLM with user tools + tool_choice="auto"
  2. If response has tool_calls -> execute them -> add results -> loop
  3. If response is text only -> done (that's the final answer)

This strategy works best with Anthropic models (Claude) but is compatible
with any provider via LiteLLM. It is the simplest and most token-efficient
strategy because it trusts the model to manage its own control flow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..utilities import error_handler, tool_executor
from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from .streaming_final_answer import stream_final_answer

logger = logging.getLogger(__name__)


class AgenticLoop(AgentLoopProtocol):
    """Claude-native agentic loop with no scaffolding overhead.

    Unlike the ReAct, Think-Act, and Act strategies, this loop uses zero
    control tools (no ``finished``, ``agent_decide_next``, etc.) and never
    forces tool choice. The model receives the real user tools with
    ``tool_choice="auto"`` and is free to:

    * Call one or more tools, then see results and decide again.
    * Respond with text only, which signals completion.

    This matches how Claude Code and Claude Desktop drive tool-use loops
    and produces the best results with Anthropic models.
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

        while steps < max_steps:
            steps += 1
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

            messages.append({
                "role": "assistant",
                "content": llm_response.content,
                "tool_calls": tool_calls,
            })

            results = await tool_executor.execute_multiple_tools(
                tool_calls=tool_calls,
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
                await event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )

        if final_response is None:
            final_response = LLMResponse(content=accumulated_content)

        # If the response is text-only (no tools), close the stream
        if not final_response.has_tool_calls() and accumulated_content:
            await event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )

        return final_response
