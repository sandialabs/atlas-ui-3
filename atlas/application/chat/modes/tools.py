"""Tools mode runner - handles LLM calls with tool execution."""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from atlas.domain.chat.citation_register import (
    CITATION_REGISTER_KEY,
    CitationRegister,
    new_register,
)
from atlas.domain.errors import LLMMalformedToolCallError
from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    Message,
    MessageRole,
    ToolResult,
)
from atlas.domain.sessions.models import Session
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol, LLMResponse
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.modules.mcp_tools.atlas_server import CANVAS_TOOL_NAME, normalize_tool_name
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..preprocessors.message_builder import build_session_context
from ..utilities import error_handler, event_notifier, tool_executor
from ..utilities.agent_digest import build_tool_digest
from ..utilities.citation_publishing import attach_citations, publish_citations
from ..utilities.dropped_calls import publish_dropped_call_warning
from ..utilities.tool_history import ToolCallRecorder
from ..utilities.tool_selection import normalize_selected_tools
from ..utilities.tool_image_context import ToolImageInjector, model_supports_vision
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
            selected_data_sources: Optional list of data sources. Scopes what
                ``atlas_search`` may read; it does not trigger retrieval.
            user_email: Optional user email for authorization
            update_callback: Optional callback for streaming updates
            temperature: LLM temperature parameter

        Returns:
            Response dictionary
        """
        # The user message was appended by the orchestrator before this call,
        # so anything added from here on belongs to this turn. Remember where
        # it starts so the tool digest (issue #798) covers only this turn.
        turn_start_index = len(session.history.messages)

        # Resolve tool schemas from the user's selection only (#921): selected
        # data sources scope what ``atlas_search`` may read, they never add
        # the tool or run retrieval up front -- the model has to call the
        # tools the user actually ticked.
        tools_schema = await error_handler.safe_get_tools_schema(
            self.tool_manager,
            normalize_selected_tools(selected_tools),
            user_email,
        )

        llm_response = await error_handler.safe_call_llm_with_tools(
            llm_caller=self.llm,
            model=model,
            messages=messages,
            tools_schema=tools_schema,
            user_email=user_email,
            tool_choice="auto",
            temperature=temperature,
        )
        # Streaming off must not make a dropped call silent.
        await publish_dropped_call_warning(self.event_publisher, llm_response)

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
        # ``is not None``, not truthiness: an explicitly empty list means "no
        # sources" (e.g. a UserPromptSubmit hook narrowed the turn to nothing).
        # Dropping it would leave mcp_execution reading None and widening the
        # query back to every source the user is authorized for.
        if selected_data_sources is not None:
            session_context["selected_data_sources"] = selected_data_sources
        # One register per turn, seeded from the numbers this conversation has
        # already used, so ``[3]`` names one document for the whole transcript
        # and a document found by two searches keeps a single number (#874).
        citation_register = new_register(session.history.messages)
        session_context[CITATION_REGISTER_KEY] = citation_register

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

        try:
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
                # Issue #909: tool-returned images reach the synthesis call
                # as a synthetic user message when the model supports vision.
                image_injector=ToolImageInjector(
                    enabled=model_supports_vision(self.config_manager, model),
                ),
            )
        except BaseException:
            # A Stop / disconnect during tool execution would otherwise discard
            # every call that already completed, the same defect fixed for
            # agent mode in issue #755. Flush what ran, closing out any call
            # that never reported a result, then let the failure through.
            await recorder.unwind(session.history)
            raise

        try:
            # Process artifacts if handler provided
            if self.artifact_processor:
                await self.artifact_processor(session, tool_results, effective_callback)

            # Persist the tool calls before the final answer so reloaded history
            # reads user -> tool_call(s) -> assistant.
            recorder.flush(session.history)
        except BaseException:
            # A stop delivered during artifact processing would otherwise unwind
            # past the flush and discard every completed call (issue #755).
            await recorder.unwind(session.history)
            raise

        # Add final assistant message to history. The digest folds this turn's
        # tool calls into the model-visible content so a follow-up turn does not
        # re-derive them (issue #798, extending the agent-mode fix from #755).
        self._close_turn(
            session,
            turn_start_index,
            content=final_response,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
            citation_register=citation_register,
        )

        # Emit final chat response
        await self.event_publisher.publish_chat_response(
            message=final_response,
            has_pending_tools=False,
        )
        # Sources this turn's searches read, published once the answer is
        # complete (issue #874).
        await publish_citations(self.event_publisher, citation_register)
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
        # The user message was appended by the orchestrator before this call,
        # so anything added from here on belongs to this turn. Remember where
        # it starts so the tool digest (issue #798) covers only this turn.
        turn_start_index = len(session.history.messages)
        # Every narration segment persisted to history this turn (issue #957).
        persisted_narrations: List[str] = []
        # Narration from canvas-only rounds, held back at the segment close:
        # a turn that ends on such a response closes with that very text (the
        # closing message is the LLM-visible copy), so the row is written only
        # if the turn ends some other way.
        deferred_narrations: List[str] = []

        tools_schema = await error_handler.safe_get_tools_schema(
            self.tool_manager,
            normalize_selected_tools(selected_tools),
            user_email,
        )

        tool_choice = "auto"

        # Stream initial LLM call with tools
        accumulated_content = ""
        final_llm_response: Optional[LLMResponse] = None
        is_first = True
        streaming_error: Optional[Exception] = None

        try:
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

        # If streaming failed and we got no content, send the error to the
        # frontend. A malformed tool call is reported even when narration was
        # already streamed: the model announced work it could not perform, and
        # presenting that narration as a finished answer would hide the gap.
        if streaming_error and (
            not accumulated_content
            or isinstance(streaming_error, LLMMalformedToolCallError)
        ):
            error_class, user_msg, log_msg = error_handler.classify_llm_error(
                streaming_error,
            )
            logger.error("Streaming tools classified error: %s", log_msg)
            if accumulated_content:
                # The user watched this text stream in. Returning without
                # persisting it saves the turn with no assistant reply at all,
                # so the narration vanishes on reload while the error frame --
                # which is transient UI -- is all that was ever shown.
                session.history.add_message(Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    metadata={"incomplete": True, "error_type": error_handler.error_type_for(error_class)},
                ))
            await self.event_publisher.send_json({
                "type": "error",
                "message": user_msg,
                "error_type": error_handler.error_type_for(error_class),
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

        # A dropped-but-not-fatal call is otherwise invisible: the turn keeps
        # going with the calls that parsed, and neither the user nor the model
        # is told that one was discarded.
        await publish_dropped_call_warning(self.event_publisher, final_llm_response)

        # Has tool calls: signal end of initial stream if we sent tokens
        if accumulated_content:
            await self.event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )
            # The narration bubble is closed; persist it now. Tool execution
            # (and any approval wait) comes next, and until the turn closes
            # this text exists nowhere else -- the replay buffer (issue #957)
            # clears on the segment's is_last precisely because a closed
            # segment belongs to history, so history must have it before the
            # tools run. A canvas-only round is the exception: if the turn
            # closes on that response it closes with this very text (the
            # synthesis shortcut), and the closing message is LLM-visible
            # while an agent_intermediate row is not -- so the narration is
            # deferred, and flushed to history only if the turn ends some
            # other way (a continuation round answers instead).
            if self._is_canvas_only_response(final_llm_response):
                deferred_narrations.append(accumulated_content)
            else:
                persisted = self._persist_narration_row(session, accumulated_content)
                if persisted:
                    persisted_narrations.append(persisted)

        session_context = build_session_context(session)
        # See note above: propagate the per-request RAG selection so atlas_rag
        # tools behave consistently with agent mode in the streaming path too,
        # preserving an explicit empty selection.
        if selected_data_sources is not None:
            session_context["selected_data_sources"] = selected_data_sources
        # See note in run(): per-turn citation numbering, continued across the
        # conversation (#874).
        citation_register = new_register(session.history.messages)
        session_context[CITATION_REGISTER_KEY] = citation_register
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
        # Issue #909: one injector per turn tracks the rolling most-recent-N
        # cap across continuation rounds.
        image_injector = ToolImageInjector(
            enabled=model_supports_vision(self.config_manager, model),
        )

        try:
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

                # Issue #909: attach tool-returned images to the transcript so
                # the next continuation round (or synthesis) can see them.
                image_injector.after_tool_results(
                    messages, results,
                    tool_names={
                        self._tool_call_id(tc): self._tool_call_signature(tc)[0]
                        for tc in fresh
                    },
                )

                if self.artifact_processor:
                    await self.artifact_processor(session, results, effective_callback)

                # Persist this round's tool rows now, not only at turn end:
                # the next round's narration is written when its stream
                # segment closes (issue #957), and flushing per round keeps
                # the reloaded transcript interleaved the way the live view
                # was -- a narration, its tools, the next narration -- rather
                # than every narration bunched ahead of every tool row. The
                # flush is idempotent, so the closing flush at finalize still
                # stands (it simply has nothing left to write).
                recorder.flush(session.history)

                # Budget check: stop chaining once the extra-round budget is spent.
                if extra_round >= max_extra_rounds:
                    break
                extra_round += 1

                # Continue WITH tools so the model can chain another dependent call.
                next_text, current_response, err = await self._stream_tools_round(
                    model, messages, tools_schema,
                    user_email, temperature,
                )
                if err is not None:
                    # Provider error mid-continuation (e.g. the tool-choice
                    # rejection) -- fall back to a graceful final synthesis.
                    # Text the user watched stream in is still persisted:
                    # its segment closed inside the round, and dropping it
                    # here would leave a reload showing a turn that appears
                    # to have said nothing before its tools. A canvas-only
                    # response defers instead, exactly like the clean path:
                    # the close below may end on it, with this very text as
                    # the LLM-visible closing message.
                    if current_response is not None and self._is_canvas_only_response(current_response):
                        if next_text:
                            deferred_narrations.append(next_text)
                    else:
                        persisted = self._persist_narration_row(session, next_text)
                        if persisted:
                            persisted_narrations.append(persisted)
                    if current_response is None:
                        current_response = LLMResponse(content="")
                    break
                if current_response is None or not current_response.has_tool_calls():
                    # Model produced its final text answer -- finalize and return.
                    # The turn did not end on a canvas-only response, so any
                    # narration deferred from one belongs to history now.
                    self._flush_deferred_narrations(session, deferred_narrations)
                    final_text = next_text or (current_response.content if current_response else "")
                    return await self._finalize_text_response(
                        session, final_text, bool(next_text),
                        selected_tools, selected_data_sources, recorder,
                        turn_start_index=turn_start_index,
                        citation_register=citation_register,
                    )
                # else: loop to execute the newly requested tools. Persist the
                # narration first: it closed with its segment, and the tools
                # ahead may park on approval -- a reopen in that window reads
                # history, where this text otherwise does not exist yet. As at
                # the initial close, a canvas-only round defers instead: the
                # turn may end on this response, with this very text as the
                # LLM-visible closing message.
                if self._is_canvas_only_response(current_response):
                    deferred_narrations.append(next_text)
                else:
                    persisted = self._persist_narration_row(session, next_text)
                    if persisted:
                        persisted_narrations.append(persisted)

            # Budget exhausted or anti-loop tripped while the model still wanted
            # tools -> force a closing text answer via no-tools synthesis, hardened
            # against another tool-call attempt with a graceful message if the model
            # ignores that and the provider rejects. A canvas-only response needs
            # no synthesis call: the turn closes with the response's own prose --
            # or, when the provider put no text on the final response, with the
            # narration this round streamed (deferred above), falling back to the
            # placeholder only when neither exists.
            if self._is_canvas_only_response(current_response):
                content = (current_response.content or "").strip()
                synthesis_content = (
                    content
                    or (deferred_narrations[-1].strip() if deferred_narrations else "")
                    or "Content displayed in canvas."
                )
            else:
                synthesis_content = await self._stream_synthesis(
                    current_response, messages, model, session_context, user_email, effective_callback,
                )
            # Narration deferred from canvas-only rounds belongs to history
            # now -- except the text the turn is closing with, which the
            # closing message itself carries (LLM-visible).
            self._flush_deferred_narrations(session, deferred_narrations, except_text=synthesis_content)

            # Persist tool calls before the closing answer (issue #684).
            recorder.flush(session.history)

            # Carry a digest of the turn's tool calls so the next turn can see
            # what already ran (issue #798, extending the agent-mode fix from
            # #755 to the default tools-mode path).
            self._close_turn(
                session,
                turn_start_index,
                content=synthesis_content,
                metadata={
                    "tools": selected_tools,
                    **({"data_sources": selected_data_sources} if selected_data_sources else {}),
                },
                citation_register=citation_register,
            )
            await publish_citations(self.event_publisher, citation_register)
            await self.event_publisher.publish_response_complete()
            return event_notifier.create_chat_response(synthesis_content)
        except BaseException:
            # A Stop / disconnect mid-round would otherwise discard every
            # tool call recorded since the turn began -- the recorder only
            # flushes on the success path (issue #755). Narration deferred
            # from a canvas-only round is flushed too: the user watched it
            # stream in, and a cancel must not drop it with the turn.
            self._flush_deferred_narrations(session, deferred_narrations)
            await recorder.unwind(session.history)
            raise

    async def _stream_synthesis(
        self,
        llm_response: LLMResponse,
        messages: List[Dict[str, Any]],
        model: str,
        session_context: Dict[str, Any],
        user_email: Optional[str],
        update_callback: Optional[UpdateCallback],
    ) -> str:
        """Stream the tool synthesis LLM call.

        The canvas-only shortcut lives at the call site (the loop's close,
        which knows the deferred narrations); this always runs a real
        synthesis call.
        """
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

        # Build synthesis messages. Only plain-string user messages count as
        # the question: multimodal user turns (inline image/PDF blocks from
        # build_messages, or the synthetic tool-image message from issue
        # #909) carry a list of content blocks, and the prompt provider's
        # ``user_question.strip()`` would raise on those, silently dropping
        # the configured synthesis prompt.
        user_question = ""
        for m in reversed(messages):
            if (
                m.get("role") == "user"
                and isinstance(m.get("content"), str)
                and m.get("content")
            ):
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
        *,
        turn_start_index: int,
        citation_register: Optional[CitationRegister] = None,
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
        # Carry a digest of the turn's tool calls so the next turn can see what
        # already ran (issue #798, extending the agent-mode fix from #755).
        self._close_turn(
            session,
            turn_start_index,
            content=content,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
            citation_register=citation_register,
        )
        await publish_citations(self.event_publisher, citation_register)
        await self.event_publisher.publish_response_complete()
        return event_notifier.create_chat_response(content)

    def _is_canvas_only_response(self, response: Any) -> bool:
        """Whether every tool call on the response is the canvas tool.

        Mirrors the condition the synthesis shortcut applies: only then does
        the turn close with the response's own content, which is what makes a
        canvas-only round's narration special (see the persist sites).
        """
        tool_calls = [tc for tc in (getattr(response, "tool_calls", None) or []) if tc is not None]
        if not tool_calls:
            return False
        return all(
            normalize_tool_name(self._tool_call_signature(tc)[0]) == CANVAS_TOOL_NAME
            for tc in tool_calls
        )

    def _flush_deferred_narrations(
        self,
        session: Session,
        deferred: List[str],
        *,
        except_text: Optional[str] = None,
    ) -> None:
        """Write canvas-round narrations to history when the turn ends another way.

        A canvas-only round's narration is deferred at its segment close: a
        turn that ends on that response closes with the very same text (the
        closing message is the LLM-visible copy). When the turn ends any
        other way -- a continuation answers, or a real synthesis runs -- the
        deferred text exists nowhere, so it is flushed here as the same
        display-only row any other narration gets. ``except_text`` skips the
        text the turn is closing with (normalized: the deferred text was
        assembled from stream deltas, the closing value may be the provider's
        final field).
        """
        skip = (except_text or "").strip()
        for text in deferred:
            if text and text.strip() and text.strip() != skip:
                self._persist_narration_row(session, text)
        deferred.clear()

    def _persist_narration_row(self, session: Session, text: str) -> Optional[str]:
        """Write a closed narration segment into history the moment it closes.

        Tools mode streams pre-tool text as its own bubble, then runs the
        tools -- which can park on approval for minutes. Until the turn's
        closing message is written, that text exists nowhere else: the replay
        buffer (issue #957) clears on the segment's ``is_last`` precisely
        because a closed segment belongs to history. Persisting here is what
        keeps that true in tools mode -- the same display-only
        ``agent_intermediate`` row the agentic loop writes for a tool-call
        step's narration, excluded from ``get_messages_for_llm`` so
        strict-alternation providers never see back-to-back assistant turns.

        Returns the text persisted (``None`` for an empty segment), so the
        canvas-only synthesis shortcut can tell a narration that is already a
        history row from one that exists nowhere else.
        """
        if not text or not text.strip():
            return None
        session.history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content=text,
            metadata={
                "agent_intermediate": True,
                "message_type": "agent_intermediate",
            },
        ))
        return text

    def _close_turn(
        self,
        session: Session,
        turn_start_index: int,
        content: str,
        metadata: Dict[str, Any],
        citation_register: Optional[CitationRegister] = None,
    ) -> Message:
        """Append the turn's closing assistant message, carrying a tool digest.

        Mirrors ``AgentModeRunner._close_turn`` (issue #755) so a tools-mode
        turn also leaves a model-visible record of what its tools did. The
        recorder's working state dies with the turn and the persisted
        ``tool_call`` rows are display-only, so without a digest a follow-up
        turn re-derives everything the tools already established (issue #798).

        Tools mode closes with the model's own answer text (unlike the agent
        interrupted path, which substitutes a placeholder), so the digest only
        rides as metadata here -- ``get_messages_for_llm`` folds it into the
        content when the next turn reads history.
        """
        try:
            digest = build_tool_digest(session.history.messages, turn_start_index)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to build tools-mode tool digest", exc_info=True)
            digest = None
        if digest:
            metadata = {**metadata, AGENT_TOOL_DIGEST_KEY: digest}
        metadata = attach_citations(metadata, citation_register)
        message = Message(
            role=MessageRole.ASSISTANT,
            content=content,
            metadata=metadata,
        )
        session.history.add_message(message)
        return message

    def _get_send_json(self) -> Optional[UpdateCallback]:
        """Get send_json callback from event publisher if available."""
        if hasattr(self.event_publisher, 'send_json'):
            callback = self.event_publisher.send_json
            logger.debug(f"_get_send_json: event_publisher.send_json = {callback is not None}")
            return callback
        logger.warning(f"_get_send_json: event_publisher does not have send_json method. Type: {type(self.event_publisher)}")
        return None
