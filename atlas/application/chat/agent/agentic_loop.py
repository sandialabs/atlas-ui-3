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

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from atlas.domain.errors import LLMMalformedToolCallError
from atlas.domain.messages.models import Message, MessageRole
from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.mcp_tools.sleep_tool import TURN_BUDGET_KEY
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..utilities import error_handler, tool_executor
from ..utilities.dropped_calls import publish_dropped_call_warning
from ..utilities.search_tool_selection import with_search_tool
from ..utilities.tool_history import ToolCallRecorder
from .protocols import AgentContext, AgentEvent, AgentEventHandler, AgentLoopProtocol, AgentResult
from .steering import SteeringChannel
from .streaming_final_answer import stream_final_answer

logger = logging.getLogger(__name__)

# Cap on a single injected steering message. Steering is a short instruction
# that re-directs a running agent; an unbounded payload could dominate the
# prompt and the persisted history. Oversized messages are truncated, not
# dropped, so the user's intent still reaches the model (issue #824 review).
_STEERING_MAX_CHARS = 8000


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
        steering: Optional[SteeringChannel] = None,
    ) -> AgentResult:
        await event_handler(AgentEvent(
            type="agent_start",
            payload={"max_steps": max_steps, "strategy": "agentic"},
        ))

        # Selecting data sources makes ``atlas_search`` available; it never
        # runs retrieval on its own. The model decides whether to call it, and
        # the call shows up in the UI like any other tool.
        effective_tools = with_search_tool(
            selected_tools, data_sources, self.config_manager,
        )

        tools_schema: List[Dict[str, Any]] = []
        if effective_tools and self.tool_manager:
            tools_schema = await error_handler.safe_get_tools_schema(
                self.tool_manager, effective_tools,
            )

        use_streaming = streaming and event_publisher

        # Record tool input/output as they stream to the UI so agent-mode tool
        # calls persist in the saved conversation and re-render on reload.
        # Issue #684 covered tools mode by wrapping its update callback; agent
        # mode executes tools through this loop's own callback, so it needs the
        # same wrapper here.
        recorder = ToolCallRecorder(self.connection.send_json if self.connection else None)

        try:
            steps, final_answer = await self._run_steps(
                model=model,
                messages=messages,
                context=context,
                tools_schema=tools_schema,
                data_sources=data_sources,
                max_steps=max_steps,
                temperature=temperature,
                event_handler=event_handler,
                use_streaming=use_streaming,
                event_publisher=event_publisher,
                recorder=recorder,
                steering=steering,
            )
        except BaseException:
            # A stop, a client disconnect, or a mid-step failure leaves this
            # step's already-executed tool calls only in the recorder. Flush
            # them so the interrupted turn still persists what actually ran
            # (issue #755); anything that never reported a result is closed out
            # rather than left rendering as in-progress forever.
            await recorder.unwind(context.history)
            raise

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
            metadata={
                "agent_mode": True,
                "strategy": "agentic",
            },
        )

    async def _run_steps(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        context: AgentContext,
        tools_schema: List[Dict[str, Any]],
        data_sources: Optional[List[str]],
        max_steps: int,
        temperature: float,
        event_handler: AgentEventHandler,
        use_streaming: bool,
        event_publisher,
        recorder: ToolCallRecorder,
        steering: Optional[SteeringChannel] = None,
    ) -> Tuple[int, Optional[str]]:
        """Run the tool-calling steps, returning ``(steps, final_answer)``.

        ``final_answer`` is ``None`` when the step budget ran out before the
        model produced a text-only response.
        """
        steps = 0
        final_answer: Optional[str] = None
        # Per-turn scratchpad for the built-in sleep tool. A local here (not an
        # instance attribute) is the turn's lifetime: AgentLoopFactory caches
        # one loop object and reuses it across turns, so state hung off `self`
        # would let one turn inherit another's spent sleep budget.
        turn_sleep_budget: Dict[str, Any] = {}

        while steps < max_steps:
            steps += 1

            # Issue #824: inject any steering messages the user sent while the
            # loop was running. Drained here at the iteration boundary (not
            # mid-step) so an in-flight tool call finishes before the new user
            # turn reaches the model. The loop is neither broken nor stopped;
            # the steering text becomes a normal user turn in history.
            _inject_steering(steering, messages, context, steps)

            # Sanitize messages: OpenAI rejects empty tool_calls arrays
            for i, msg in enumerate(messages):
                if isinstance(msg, dict) and "tool_calls" in msg and not msg["tool_calls"]:
                    logger.warning("Stripping empty tool_calls from messages[%d]", i)
                    del msg["tool_calls"]

            await event_handler(AgentEvent(
                type="agent_turn_start", payload={"step": steps},
            ))

            llm_response = await self._call_llm(
                model, messages, tools_schema,
                context, temperature, use_streaming, event_publisher,
            )

            if not llm_response.has_tool_calls():
                # The model produced a text-only response, which normally ends
                # the loop. But if the user steered *while this response was
                # being generated*, that steering is still waiting in the
                # channel -- folding the response in as intermediate narration
                # and continuing lets the model address the new instruction
                # instead of handing the user a final answer that ignores it.
                # The loop is not stopped; the next iteration's boundary drain
                # injects the steering and calls the LLM again. The drain is
                # deliberately NOT done here: at the step-budget boundary the
                # ``continue`` exits the loop, and injecting here would persist
                # a USER turn no model ever saw. Leaving it in the channel lets
                # the runner surface it as a leftover instead (issue #824).
                if steering is not None and steering.has_pending():
                    _fold_text_only_as_intermediate(
                        messages, context, llm_response, steps,
                    )
                    continue
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
            if llm_response.content and llm_response.content.strip():
                # The loop is the single owner of narration persistence: write
                # the intermediate assistant text straight into history (before
                # the tool_call rows) so reloads match the live transcript. It
                # is a display-only ``agent_intermediate`` row, excluded from
                # get_messages_for_llm() so strict-alternation providers never
                # see back-to-back assistant turns on the next request.
                context.history.add_message(Message(
                    role=MessageRole.ASSISTANT,
                    content=llm_response.content,
                    metadata={
                        "agent_mode": True,
                        "agent_intermediate": True,
                        "message_type": "agent_intermediate",
                        "step": steps,
                    },
                ))

            results = await tool_executor.execute_multiple_tools(
                tool_calls=tool_calls,
                session_context={
                    "session_id": context.session_id,
                    "user_email": context.user_email,
                    "files": context.files,
                    # None and [] mean different things downstream: None is "the
                    # user made no selection" (atlas_search falls back to every
                    # authorized source), [] is "explicitly no sources" (query
                    # nothing). Collapsing None to [] would break RAG for every
                    # agent turn where the user picked no sources.
                    "selected_data_sources": data_sources,
                    # Trusted compliance level so RAG tools enforce the boundary;
                    # the model cannot set or change this.
                    "compliance_level": context.compliance_level,
                    # Required so MCP tool calls reuse a persistent session via
                    # MCPSessionManager. Without it, call_tool() falls back to a
                    # single-use session per call and stateful MCP servers raise
                    # session errors between sequential tool calls. Fall back to
                    # the session id (matching ChatService's default conversation
                    # scoping) so direct callers that omit conversation_id still
                    # get one stable persistent session instead of None.
                    "conversation_id": context.conversation_id or str(context.session_id),
                    # Mutable, one per turn: atlas_agent_sleep accumulates the
                    # seconds it has slept here so the turn's total wait is
                    # bounded, not just each individual call.
                    TURN_BUDGET_KEY: turn_sleep_budget,
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

            # Flush this step's recorded tool calls into history now rather
            # than once at end of run: providers may reuse tool_call_ids
            # across steps (e.g. ids restarting at call_0 per response), and
            # the recorder keys by id, so a later step would overwrite an
            # earlier row. Per-step flushing scopes ids to one step and keeps
            # every invocation as its own row; all rows still land before the
            # final assistant message the caller appends after run() returns.
            recorder.flush(context.history)

        return steps, final_answer

    async def _call_llm(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
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
                model, messages, tools_schema,
                context, temperature, event_publisher,
            )

        # No RAG pre-injection: retrieval only happens if the model calls
        # ``atlas_search``, which runs through the normal tool path.
        response = await self.llm.call_with_tools(
            model, messages, tools_schema, "auto",
            temperature=temperature, user_email=context.user_email,
        )
        # Streaming off must not make a dropped call silent.
        await publish_dropped_call_warning(event_publisher, response)
        return response

    async def _call_llm_streaming(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
        context: AgentContext,
        temperature: float,
        event_publisher,
    ) -> LLMResponse:
        """Stream an LLM call, publishing tokens and returning the final response."""
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
        except LLMMalformedToolCallError:
            # The model announced tool calls that could not be run, and none
            # survived the JSON check. Treating the narration as a finished
            # answer would hand the user a reply that silently skipped the work
            # it just promised, so this failure propagates even when text was
            # already streamed. Close the open bubble first so the UI does not
            # keep a live cursor behind the error.
            logger.warning("Malformed tool call ended the streaming agent turn")
            if accumulated_content:
                await event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )
                # Match tools mode: text the user watched stream in is kept, as
                # the same display-only agent_intermediate row a completed step
                # writes, so a reload shows what actually happened rather than
                # a turn that appears to have said nothing.
                context.history.add_message(Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    metadata={
                        "agent_mode": True,
                        "agent_intermediate": True,
                        "message_type": "agent_intermediate",
                        "incomplete": True,
                    },
                ))
            raise
        except Exception:
            logger.exception("Error during streaming LLM call in agentic loop")
            if not accumulated_content:
                # Nothing was produced before the error. Surface it instead of
                # returning an empty response that looks to the user like the
                # model silently said nothing (e.g. the provider rejecting a
                # mid-stream tool call with "tool_choice is none, but model
                # called a tool"). The caller's error handling publishes a
                # user-visible message.
                raise
            # Partial text already streamed to the UI -- fall through to the
            # single stream-close below and return what we have rather than
            # discarding it.

        if final_response is None:
            final_response = LLMResponse(content=accumulated_content)
        elif accumulated_content and not final_response.content:
            final_response.content = accumulated_content

        # A partial drop keeps the turn alive, so it has to be said out loud or
        # it is invisible.
        await publish_dropped_call_warning(event_publisher, final_response)

        # Close any streamed text, including narration for tool-call turns, so
        # each iteration finalizes as its own UI bubble before tool rows render.
        if accumulated_content:
            await event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )

        return final_response


def _inject_steering(
    steering: Optional[SteeringChannel],
    messages: List[Dict[str, Any]],
    context: AgentContext,
    step: int,
) -> int:
    """Drain pending steering messages and append each as a normal user turn.

    Non-blocking: returns immediately when no channel is supplied or the queue
    is empty. Each drained message is appended both to the live ``messages``
    list (so the next LLM call sees it) and to ``context.history`` (so the turn
    persists and reloads show it). The message carries no display-only
    ``message_type``, so later turns include it via ``get_messages_for_llm`` --
    it is a genuine user turn, not a steering annotation.

    Oversized payloads are truncated to ``_STEERING_MAX_CHARS`` so a single
    queued steer cannot dominate the prompt or the history. Returns the count of
    messages injected so callers can log/observe.
    """
    if steering is None:
        return 0
    drained = 0
    while True:
        try:
            content = steering.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if content is None:
            continue
        text = content if isinstance(content, str) else str(content)
        if not text:
            continue
        if len(text) > _STEERING_MAX_CHARS:
            text = text[:_STEERING_MAX_CHARS]
            logger.warning(
                "Truncated a steering message from %d to %d chars at step %d",
                len(text), _STEERING_MAX_CHARS, step,
            )
        messages.append({"role": "user", "content": text})
        context.history.add_message(Message(
            role=MessageRole.USER,
            content=text,
            metadata={"steered": True, "step": step},
        ))
        drained += 1
    if drained:
        logger.info(
            "Injected %d steering message(s) into the agent loop at step %d",
            drained, step,
        )
    return drained


def _fold_text_only_as_intermediate(
    messages: List[Dict[str, Any]],
    context: AgentContext,
    llm_response: LLMResponse,
    step: int,
) -> None:
    """Keep a would-be final text-only response as intermediate narration.

    Used when steering arrives during a text-only response (issue #824): the
    response is not the final answer, so it is folded in as the same
    display-only ``agent_intermediate`` row a tool-call step's narration uses,
    and appended to the live ``messages`` list so the next LLM call can build
    on it after addressing the steering. The turn's closing assistant message
    is still written later by the agent runner, so strict-alternation providers
    never see back-to-back assistant turns on a later request.
    """
    content = llm_response.content or ""
    if not content:
        return
    messages.append({"role": "assistant", "content": content})
    context.history.add_message(Message(
        role=MessageRole.ASSISTANT,
        content=content,
        metadata={
            "agent_mode": True,
            "agent_intermediate": True,
            "message_type": "agent_intermediate",
            "steered_over": True,
            "step": step,
        },
    ))
