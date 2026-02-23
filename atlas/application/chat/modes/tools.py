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
        tool_choice_required: bool = False,
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
            tool_choice_required: Whether tool use is required
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
            tool_choice=("required" if tool_choice_required else "auto"),
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

        # Ensure update_callback is never None (critical for elicitation)
        effective_callback = update_callback
        if effective_callback is None:
            effective_callback = self._get_send_json()
            logger.debug("Tools mode: update_callback was None, using event_publisher.send_json fallback")

        if effective_callback is None:
            logger.warning("Tools mode: No update callback available - elicitation will not work!")

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
        tool_choice_required: bool = False,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Execute tools mode with token streaming."""
        tools_schema = await error_handler.safe_get_tools_schema(self.tool_manager, selected_tools)

        tool_choice = "required" if tool_choice_required else "auto"

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

        # Execute tool workflow (non-streaming tools, streaming synthesis)
        session_context = build_session_context(session)
        effective_callback = update_callback
        if effective_callback is None:
            effective_callback = self._get_send_json()

        # Execute tools
        # Convert tool_calls to plain dicts for API serialization
        # (streaming yields SimpleNamespace objects for attribute access
        # but litellm needs dicts when re-sending messages to the LLM)
        tool_calls_dicts = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in final_llm_response.tool_calls
        ]
        messages.append({
            "role": "assistant",
            "content": final_llm_response.content,
            "tool_calls": tool_calls_dicts,
        })
        tool_results: List[ToolResult] = []
        for tool_call in final_llm_response.tool_calls:
            result = await tool_executor.execute_single_tool(
                tool_call=tool_call,
                session_context=session_context,
                tool_manager=self.tool_manager,
                update_callback=effective_callback,
                config_manager=self.config_manager,
                skip_approval=self.skip_approval,
            )
            tool_results.append(result)
        for result in tool_results:
            messages.append({
                "role": "tool",
                "content": result.content,
                "tool_call_id": result.tool_call_id,
            })

        # Process artifacts
        if self.artifact_processor:
            await self.artifact_processor(session, tool_results, effective_callback)

        # Stream synthesis
        synthesis_content = await self._stream_synthesis(
            final_llm_response, messages, model, session_context, user_email, effective_callback,
        )

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

        return await stream_and_accumulate(
            token_generator=self.llm.stream_plain(
                model, synthesis_messages, user_email=user_email,
            ),
            event_publisher=self.event_publisher,
            fallback_fn=lambda: self.llm.call_plain(
                model, synthesis_messages, user_email=user_email,
            ),
            context_label="synthesis",
        )

    def _get_send_json(self) -> Optional[UpdateCallback]:
        """Get send_json callback from event publisher if available."""
        if hasattr(self.event_publisher, 'send_json'):
            callback = self.event_publisher.send_json
            logger.debug(f"_get_send_json: event_publisher.send_json = {callback is not None}")
            return callback
        logger.warning(f"_get_send_json: event_publisher does not have send_json method. Type: {type(self.event_publisher)}")
        return None
