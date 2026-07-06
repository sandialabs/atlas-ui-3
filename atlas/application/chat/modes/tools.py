"""Tools mode runner - handles LLM calls with tool execution."""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.messages.models import Message, MessageRole, ToolResult
from atlas.domain.sessions.models import Session
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..preprocessors.message_builder import build_session_context
from ..utilities import error_handler, event_notifier, tool_executor
from ..utilities.tool_history import ToolCallRecorder
from .streaming_helpers import stream_and_accumulate

logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class ToolsModeRunner:
    """
    Runner for tools mode.

    Executes LLM calls with tool integration, including tool execution
    and artifact processing.
    """

    def __init__(
        self,
        llm: LLMProtocol,
        tool_manager: ToolManagerProtocol,
        event_publisher: EventPublisher,
        prompt_provider: Optional[PromptProvider] = None,
        artifact_processor: Optional[Callable[[Session, List[ToolResult], Optional[UpdateCallback]], Awaitable[None]]] = None,
        config_manager=None,
    ):
        """
        Initialize tools mode runner.

        Args:
            llm: LLM protocol implementation
            tool_manager: Tool manager for tool execution
            event_publisher: Event publisher for UI updates
            prompt_provider: Optional prompt provider
            artifact_processor: Optional callback for processing tool artifacts
            config_manager: Optional config manager for approval settings
        """
        self.llm = llm
        self.tool_manager = tool_manager
        self.event_publisher = event_publisher
        self.prompt_provider = prompt_provider
        self.artifact_processor = artifact_processor
        self.config_manager = config_manager
        self.skip_approval = False

        # Verify event_publisher has send_json for elicitation support
        if hasattr(event_publisher, 'send_json'):
            logger.debug(f"ToolsModeRunner initialized with event_publisher that has send_json: {type(event_publisher)}")
        else:
            logger.warning(f"ToolsModeRunner initialized with event_publisher WITHOUT send_json: {type(event_publisher)}")

    async def run(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: List[str],
        selected_data_sources: Optional[List[str]] = None,
        user_email: Optional[str] = None,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Execute tools mode.

        Args:
            session: Current chat session
            model: LLM model to use
            messages: Message history
            selected_tools: List of tools to make available
            selected_data_sources: Optional list of data sources (for RAG+tools)
            user_email: Optional user email for authorization
            update_callback: Optional callback for streaming updates
            temperature: LLM temperature parameter

        Returns:
            Response dictionary
        """
        # Resolve tool schemas
        tools_schema = await error_handler.safe_get_tools_schema(self.tool_manager, selected_tools)

        # Call LLM with tools (and RAG if provided)
        llm_response = await error_handler.safe_call_llm_with_tools(
            llm_caller=self.llm,
            model=model,
            messages=messages,
            tools_schema=tools_schema,
            data_sources=selected_data_sources,
            user_email=user_email,
            tool_choice="auto",
            temperature=temperature,
        )

        # No tool calls -> treat as plain content
        if not llm_response or not llm_response.has_tool_calls():
            content = llm_response.content if llm_response else ""
            assistant_message = Message(role=MessageRole.ASSISTANT, content=content)
            session.history.add_message(assistant_message)

            await self.event_publisher.publish_chat_response(
                message=content,
                has_pending_tools=False,
            )
            await self.event_publisher.publish_response_complete()

            return event_notifier.create_chat_response(content)

        # Execute tool workflow
        session_context = build_session_context(session)
        # Carry the request's selected RAG data sources on the execution context
        # so the atlas_rag tools honor the UI selection in tools mode exactly as
        # they do in agent mode, instead of falling back to all authorized
        # sources. build_session_context() only reflects session state, not this
        # per-request selection.
        if selected_data_sources:
            session_context["selected_data_sources"] = selected_data_sources

        # Ensure update_callback is never None (critical for elicitation)
        effective_callback = update_callback
        if effective_callback is None:
            effective_callback = self._get_send_json()
            logger.debug("Tools mode: update_callback was None, using event_publisher.send_json fallback")

        if effective_callback is None:
            logger.warning("Tools mode: No update callback available - elicitation will not work!")

        # Record tool input/output as they stream to the UI so they persist in
        # the saved conversation and re-render on reload (issue #684).
        recorder = ToolCallRecorder(effective_callback)
        effective_callback = recorder

        final_response, tool_results = await tool_executor.execute_tools_workflow(
            llm_response=llm_response,
            messages=messages,
            model=model,
            session_context=session_context,
            tool_manager=self.tool_manager,
            llm_caller=self.llm,
            prompt_provider=self.prompt_provider,
            update_callback=effective_callback,
            config_manager=self.config_manager,
            skip_approval=self.skip_approval,
            user_email=user_email,
        )

        # Process artifacts if handler provided
        if self.artifact_processor:
            await self.artifact_processor(session, tool_results, effective_callback)

        # Persist the tool calls before the final answer so reloaded history
        # reads user -> tool_call(s) -> assistant.
        recorder.flush(session.history)

        # Add final assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=final_response,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
        )
        session.history.add_message(assistant_message)

        # Emit final chat response
        await self.event_publisher.publish_chat_response(
            message=final_response,
            has_pending_tools=False,
        )
        await self.event_publisher.publish_response_complete()

        return event_notifier.create_chat_response(final_response)

    async def run_streaming(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: List[str],
        selected_data_sources: Optional[List[str]] = None,
        user_email: Optional[str] = None,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Execute tools mode with token streaming."""
        tools_schema = await error_handler.safe_get_tools_schema(self.tool_manager, selected_tools)

        tool_choice = "auto"

        # Stream initial LLM call with tools
        accumulated_content = ""
        final_llm_response: Optional[LLMResponse] = None
        is_first = True
        streaming_error: Optional[Exception] = None

        try:
            if selected_data_sources and user_email:
                stream = self.llm.stream_with_rag_and_tools(
                    model, messages, selected_data_sources, tools_schema,
                    user_email, tool_choice, temperature=temperature,
                )
            else:
                stream = self.llm.stream_with_tools(
                    model, messages, tools_schema, tool_choice,
                    temperature=temperature, user_email=user_email,
                )

            async for item in stream:
                if isinstance(item, str):
                    await self.event_publisher.publish_token_stream(
                        token=item, is_first=is_first, is_last=False,
                    )
                    accumulated_content += item
                    is_first = False
                elif isinstance(item, LLMResponse):
                    final_llm_response = item
        except Exception as exc:
            logger.error("Streaming tools error: %s", exc)
            streaming_error = exc
            # Always send stream-end to prevent stuck UI cursor
            await self.event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )

        # If streaming failed and we got no content, send the error to the frontend
        if streaming_error and not accumulated_content:
            _error_class, user_msg, log_msg = error_handler.classify_llm_error(
                streaming_error,
            )
            logger.error("Streaming tools classified error: %s", log_msg)
            await self.event_publisher.send_json({
                "type": "error",
                "message": user_msg,
            })
            await self.event_publisher.publish_response_complete()
            return event_notifier.create_chat_response(user_msg)

        # No tool calls -> treat as plain streamed content
        if not final_llm_response or not final_llm_response.has_tool_calls():
            content = accumulated_content or (final_llm_response.content if final_llm_response else "")
            if accumulated_content:
                await self.event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )
            else:
                await self.event_publisher.publish_chat_response(
                    message=content, has_pending_tools=False,
                )

            assistant_message = Message(role=MessageRole.ASSISTANT, content=content)
            session.history.add_message(assistant_message)
            await self.event_publisher.publish_response_complete()
            return event_notifier.create_chat_response(content)

        # Has tool calls: signal end of initial stream if we sent tokens
        if accumulated_content:
            await self.event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )

        session_context = build_session_context(session)
        # See note above: propagate the per-request RAG selection so atlas_rag
        # tools behave consistently with agent mode in the streaming path too.
        if selected_data_sources:
            session_context["selected_data_sources"] = selected_data_sources
        effective_callback = update_callback
        if effective_callback is None:
            effective_callback = self._get_send_json()

        # Record tool input/output across every round so they persist in the
        # saved conversation and re-render on reload (issue #684).
        recorder = ToolCallRecorder(effective_callback)
        effective_callback = recorder

        # Bounded tool-calling loop. The initial response is round 0; the model
        # may take up to ``max_extra_rounds`` further rounds to chain dependent
        # tool calls (e.g. compute a value, then use it). An anti-loop guard
        # refuses repeated identical calls so a model cannot spin on one tool.
        # When the budget is exhausted (or the model keeps repeating), a final
        # no-tools synthesis produces the closing text answer. ``max_extra_rounds
        # == 0`` reproduces the classic single-round behavior.
        max_extra_rounds = self._max_extra_rounds()
        current_response = final_llm_response
        executed_signatures: set = set()
        extra_round = 0

        while True:
            tool_calls = [tc for tc in (current_response.tool_calls or []) if tc is not None]

            # Append the assistant message with tool_calls as plain dicts so they
            # round-trip to the next LLM call (streaming yields SimpleNamespace
            # objects, which serialize to an empty array and get rejected).
            messages.append({
                "role": "assistant",
                "content": current_response.content,
                "tool_calls": [self._tool_call_dict(tc) for tc in tool_calls],
            })

            repeated_ids = {
                self._tool_call_id(tc)
                for tc in tool_calls
                if self._tool_call_signature(tc) in executed_signatures
            }
            fresh = [
                tc for tc in tool_calls
                if self._tool_call_signature(tc) not in executed_signatures
            ]

            if not fresh:
                # Anti-loop: the model is only repeating calls it already made.
                # Satisfy the API (every tool_call_id needs a tool message) with
                # cached-result notes, then stop and synthesize a final answer.
                for tc in tool_calls:
                    messages.append({
                        "role": "tool",
                        "content": "(skipped: identical tool call already executed this turn)",
                        "tool_call_id": self._tool_call_id(tc),
                    })
                break

            results = await tool_executor.execute_multiple_tools(
                tool_calls=fresh,
                session_context=session_context,
                tool_manager=self.tool_manager,
                update_callback=effective_callback,
                config_manager=self.config_manager,
                skip_approval=self.skip_approval,
            )
            for tc in fresh:
                executed_signatures.add(self._tool_call_signature(tc))
            result_by_id = {r.tool_call_id: r.content for r in results}
            # Append tool results in the SAME order as the assistant tool_calls.
            for tc in tool_calls:
                tc_id = self._tool_call_id(tc)
                if tc_id in repeated_ids:
                    content = "(skipped: identical tool call already executed this turn)"
                else:
                    content = result_by_id.get(tc_id, "")
                messages.append({
                    "role": "tool",
                    "content": content,
                    "tool_call_id": tc_id,
                })

            if self.artifact_processor:
                await self.artifact_processor(session, results, effective_callback)

            # Budget check: stop chaining once the extra-round budget is spent.
            if extra_round >= max_extra_rounds:
                break
            extra_round += 1

            # Continue WITH tools so the model can chain another dependent call.
            next_text, current_response, err = await self._stream_tools_round(
                model, messages, tools_schema, selected_data_sources,
                user_email, temperature,
            )
            if err is not None:
                # Provider error mid-continuation (e.g. the tool-choice
                # rejection) -- fall back to a graceful final synthesis.
                if current_response is None:
                    current_response = LLMResponse(content="")
                break
            if current_response is None or not current_response.has_tool_calls():
                # Model produced its final text answer -- finalize and return.
                final_text = next_text or (current_response.content if current_response else "")
                return await self._finalize_text_response(
                    session, final_text, bool(next_text),
                    selected_tools, selected_data_sources, recorder,
                )
            # else: loop to execute the newly requested tools.

        # Budget exhausted or anti-loop tripped while the model still wanted
        # tools -> force a closing text answer via no-tools synthesis, hardened
        # against another tool-call attempt with a graceful message if the model
        # ignores that and the provider rejects.
        synthesis_content = await self._stream_synthesis(
            current_response, messages, model, session_context, user_email, effective_callback,
        )

        # Persist tool calls before the closing answer (issue #684).
        recorder.flush(session.history)

        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=synthesis_content,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
        )
        session.history.add_message(assistant_message)
        await self.event_publisher.publish_response_complete()
        return event_notifier.create_chat_response(synthesis_content)

    async def _stream_synthesis(
        self,
        llm_response: LLMResponse,
        messages: List[Dict[str, Any]],
        model: str,
        session_context: Dict[str, Any],
        user_email: Optional[str],
        update_callback: Optional[UpdateCallback],
    ) -> str:
        """Stream the tool synthesis LLM call."""
        # Check canvas-only shortcut
        canvas_calls = [tc for tc in llm_response.tool_calls if tc.function.name == "canvas_canvas"]
        if len(canvas_calls) == len(llm_response.tool_calls):
            return llm_response.content or "Content displayed in canvas."

        # Add files manifest
        files_manifest = tool_executor.build_files_manifest(session_context)
        if files_manifest:
            updated = {
                "role": "system",
                "content": (
                    "Available session files (updated after tool runs):\n"
                    f"{files_manifest['content'].split('Available session files:')[1].split('(You can ask')[0].strip()}\n\n"
                    "(You can ask to open or analyze any of these by name.)"
                ),
            }
            messages.append(updated)

        if update_callback:
            try:
                await update_callback({"type": "tool_synthesis_start"})
            except Exception:
                pass  # Best-effort UI notification; synthesis proceeds regardless

        # Build synthesis messages
        user_question = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                user_question = m["content"]
                break

        synthesis_messages = list(messages)
        if self.prompt_provider:
            prompt_text = self.prompt_provider.get_tool_synthesis_prompt(user_question or "the user's last request")
            if prompt_text:
                synthesis_messages.append({"role": "system", "content": prompt_text})

        # The synthesis call sends no tools, so if the model emits a tool call the
        # provider rejects the whole stream ("tool_choice is none, but model called
        # a tool"). Tell the model explicitly not to call tools here -- most models
        # comply and just summarize; for those that don't, _synthesis_error_message
        # turns the rejection into a clear, actionable reply instead of a crash.
        synthesis_messages.append({
            "role": "system",
            "content": (
                "You have already used all tools available for this turn. Do NOT "
                "call any more tools. Reply to the user with a plain-text answer "
                "that uses the tool results above."
            ),
        })

        return await stream_and_accumulate(
            token_generator=self.llm.stream_plain(
                model, synthesis_messages, user_email=user_email,
            ),
            event_publisher=self.event_publisher,
            fallback_fn=lambda: self.llm.call_plain(
                model, synthesis_messages, user_email=user_email,
            ),
            context_label="synthesis",
            on_error_message=self._synthesis_error_message,
        )

    # -- Bounded tool-calling loop helpers ---------------------------------

    def _max_extra_rounds(self) -> int:
        """Configured number of additional tool-calling rounds (default 3)."""
        try:
            return max(0, int(self.config_manager.app_settings.tools_mode_max_extra_rounds))
        except Exception:
            return 3

    def _agent_mode_available(self) -> bool:
        """Whether Agent Mode is enabled for this deployment (admin flag)."""
        try:
            return bool(self.config_manager.app_settings.feature_agent_mode_available)
        except Exception:
            return False

    @staticmethod
    def _tool_call_id(tc: Any) -> Optional[str]:
        if isinstance(tc, dict):
            return tc.get("id")
        return getattr(tc, "id", None)

    @staticmethod
    def _tool_call_signature(tc: Any):
        """Identity used by the anti-loop guard: (name, arguments)."""
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            if isinstance(fn, dict):
                return (fn.get("name", ""), fn.get("arguments", ""))
            return (getattr(fn, "name", ""), getattr(fn, "arguments", ""))
        fn = getattr(tc, "function", None)
        return (getattr(fn, "name", "") or "", getattr(fn, "arguments", "") or "")

    @staticmethod
    def _tool_call_dict(tc: Any) -> Dict[str, Any]:
        """Normalize a tool call to a plain OpenAI-format dict for re-sending."""
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {"name": getattr(fn, "name", ""), "arguments": getattr(fn, "arguments", "")}
            return {
                "id": tc.get("id"),
                "type": tc.get("type", "function") or "function",
                "function": {"name": fn.get("name", ""), "arguments": fn.get("arguments", "")},
            }
        fn = getattr(tc, "function", None)
        return {
            "id": getattr(tc, "id", None),
            "type": getattr(tc, "type", "function") or "function",
            "function": {
                "name": getattr(fn, "name", "") or "",
                "arguments": getattr(fn, "arguments", "") or "",
            },
        }

    def _synthesis_error_message(self, exc: Exception) -> str:
        """User-facing message when the synthesis call fails.

        The common failure here is the model trying to call yet another tool
        while we offer none, which the provider rejects. Turn that into a clear,
        actionable reply -- and only mention Agent Mode when it is actually
        available (an admin may have disabled it).
        """
        text = str(exc).lower()
        is_tool_choice_error = (
            "tool choice is none" in text
            or "model called a tool" in text
            or "midstreamfallback" in type(exc).__name__.lower()
        )
        if is_tool_choice_error:
            base = (
                "I ran the tool(s) above, but the model then tried to call another "
                "tool while finishing its answer, which standard tools mode can't do "
                "after its tool rounds are used up."
            )
            if self._agent_mode_available():
                return (
                    base
                    + " You can send a follow-up to continue, or turn on Agent Mode "
                    "to let me chain multiple tools automatically."
                )
            return base + " Send a follow-up (e.g. \"now do the next step\") and I'll continue."
        _err_class, user_msg, _log_msg = error_handler.classify_llm_error(exc)
        return user_msg

    async def _stream_tools_round(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: List[Dict[str, Any]],
        selected_data_sources: Optional[List[str]],
        user_email: Optional[str],
        temperature: float,
    ):
        """Run one continuation LLM call WITH tools, streaming any text tokens.

        Returns ``(accumulated_text, llm_response, error)``. On a streaming error
        the stream is closed and ``error`` is the exception (caller falls back to
        synthesis). Any streamed text is closed with an is_last token so the next
        UI segment (tool execution or synthesis) starts cleanly.
        """
        accumulated = ""
        response: Optional[LLMResponse] = None
        is_first = True
        try:
            if selected_data_sources and user_email:
                stream = self.llm.stream_with_rag_and_tools(
                    model, messages, selected_data_sources, tools_schema,
                    user_email, "auto", temperature=temperature,
                )
            else:
                stream = self.llm.stream_with_tools(
                    model, messages, tools_schema, "auto",
                    temperature=temperature, user_email=user_email,
                )
            async for item in stream:
                if isinstance(item, str):
                    await self.event_publisher.publish_token_stream(
                        token=item, is_first=is_first, is_last=False,
                    )
                    accumulated += item
                    is_first = False
                elif isinstance(item, LLMResponse):
                    response = item
        except Exception as exc:
            logger.error("Streaming tools continuation error: %s", exc)
            if accumulated:
                await self.event_publisher.publish_token_stream(
                    token="", is_first=False, is_last=True,
                )
            return accumulated, response, exc

        if accumulated:
            await self.event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )
        return accumulated, response, None

    async def _finalize_text_response(
        self,
        session: Session,
        content: str,
        already_streamed: bool,
        selected_tools: List[str],
        selected_data_sources: Optional[List[str]],
        recorder: Optional[ToolCallRecorder] = None,
    ) -> Dict[str, Any]:
        """Persist and emit a plain-text final answer produced mid-loop."""
        if not already_streamed:
            await self.event_publisher.publish_chat_response(
                message=content, has_pending_tools=False,
            )
        # Persist tool calls before the final answer so reloaded history reads
        # user -> tool_call(s) -> assistant (issue #684).
        if recorder is not None:
            recorder.flush(session.history)
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=content,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
        )
        session.history.add_message(assistant_message)
        await self.event_publisher.publish_response_complete()
        return event_notifier.create_chat_response(content)

    def _get_send_json(self) -> Optional[UpdateCallback]:
        """Get send_json callback from event publisher if available."""
        if hasattr(self.event_publisher, 'send_json'):
            callback = self.event_publisher.send_json
            logger.debug(f"_get_send_json: event_publisher.send_json = {callback is not None}")
            return callback
        logger.warning(f"_get_send_json: event_publisher does not have send_json method. Type: {type(self.event_publisher)}")
        return None
