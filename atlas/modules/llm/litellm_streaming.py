"""Streaming methods for LiteLLMCaller.

Extracted to keep litellm_caller.py under the 400-line guideline.
These methods are mixed into LiteLLMCaller via LiteLLMStreamingMixin.
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

from litellm import acompletion
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from atlas.application.chat.capture.capture_context import record_llm_call
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.metrics_logger import log_metric
from atlas.core.telemetry import set_attrs, start_span
from atlas.domain.errors import LLMMalformedToolCallError

from .models import LLMResponse, split_provider
from .tool_call_guard import (
    partition_tool_calls_by_json_validity,
    tool_call_function_field,
)

logger = logging.getLogger(__name__)

# The OpenAI-compatible finish_reason vocabulary. Anything else is provider text
# and is reported as "other" rather than interpolated into a log line or a span.
KNOWN_FINISH_REASONS = frozenset(
    {"stop", "length", "tool_calls", "function_call", "content_filter"}
)


class LiteLLMStreamingMixin:
    """Mixin providing streaming LLM methods for LiteLLMCaller.

    Expects the host class to provide:
      - _get_litellm_model_name(model_name) -> str
      - _get_model_kwargs(model_name, temperature, user_email) -> dict
      - _prepare_messages(model_name, messages) -> list
      - _query_all_rag_sources(data_sources, rag_service, user_email, messages) -> (successful, exclusions, failures)
      - _build_rag_completion_response(rag_response, display_source) -> str
      - _build_rag_exclusion_notice(exclusions) -> str
      - _build_rag_failure_notice(failures) -> str
      - _combine_rag_contexts(source_responses) -> tuple
      - _rag_insert_index(messages) -> int
      - _rag_service attribute
    """

    async def stream_plain(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        user_email: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream plain LLM response token-by-token.

        Yields string chunks as they arrive from the LLM provider.
        """
        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

        if max_tokens is not None:
            model_kwargs["max_tokens"] = max_tokens

        # PreLlmCall hook (GH #713): zero-overhead when no hooks registered.
        # Deny raises HookBlockedError which propagates through the generator.
        mod = await self._fire_pre_llm_hook(model_name, messages, user_email=user_email)
        if mod is not None:
            model_name, messages, _ = await self._apply_pre_llm_modify(mod, model_name, messages, user_email=user_email)
            litellm_model = self._get_litellm_model_name(model_name)
            model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)
            if max_tokens is not None:
                model_kwargs["max_tokens"] = max_tokens

        provider, model_suffix = split_provider(litellm_model)
        span_attrs = {
            "model": litellm_model,
            "provider": provider,
            "model_version": model_suffix,
            "temperature": model_kwargs.get("temperature"),
            "max_tokens": model_kwargs.get("max_tokens"),
            "streaming": True,
            "has_tools": False,
            "message_count": len(messages),
        }

        with start_span("llm.call", span_attrs) as span:
            start_ns = time.monotonic_ns()
            try:
                total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
                logger.info("Streaming plain LLM call: %d messages, %d chars", len(messages), total_chars)

                response = await acompletion(
                    model=litellm_model,
                    messages=self._prepare_messages(model_name, messages),
                    stream=True,
                    **model_kwargs,
                )

                chunk_count = 0
                total_chunks_seen = 0
                accumulated_chars = 0
                async for chunk in response:
                    total_chunks_seen += 1
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if total_chunks_seen <= 3:
                        logger.debug(
                            "Stream chunk #%d for %s: choices=%s, delta=%s, content_len=%s",
                            total_chunks_seen, model_name,
                            bool(chunk.choices), type(delta).__name__ if delta else None,
                            len(delta.content) if delta and delta.content else 0,
                        )
                    if delta and delta.content:
                        yield delta.content
                        chunk_count += 1
                        accumulated_chars += len(delta.content)
                        # Yield control periodically to prevent backpressure buildup
                        if chunk_count % 50 == 0:
                            await asyncio.sleep(0)

                if chunk_count == 0 and total_chunks_seen > 0:
                    logger.warning(
                        "Stream for %s received %d chunks but yielded 0 tokens",
                        model_name, total_chunks_seen,
                    )
                log_metric("llm_call", user_email, model=model_name, message_count=len(messages))
                set_attrs(span, {
                    "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "chunk_count": chunk_count,
                    "output_chars": accumulated_chars,
                    # Streaming LLM calls don't carry usage metadata; publish a
                    # char-based estimate under a distinct name so downstream
                    # aggregations never silently mix real token counts with
                    # approximations.
                    "output_tokens_estimate": accumulated_chars // 4,
                    "retry_count": 0,
                })

            except Exception as exc:
                set_attrs(span, {
                    "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(exc).__name__,
                })
                logger.error("Error in streaming LLM call: %s", exc, exc_info=True)
                self._raise_llm_domain_error(exc)

    def _handle_malformed_tool_calls(
        self,
        malformed: List[Any],
        kept: List[Any],
        finish_reason: Optional[str],
        model_name: str,
        span: Any = None,
        user_email: Optional[str] = None,
    ) -> List[str]:
        """Log dropped tool calls and fail the turn when none are left.

        Dropping is always right -- unparseable arguments cannot be executed
        faithfully, and re-sending them breaks every later request. What differs
        is whether the turn can still make progress: if at least one well-formed
        call survives, the model gets those results and can reissue the rest.
        If nothing survives, raise, so the user is told the turn failed instead
        of silently receiving a reply that skipped the work it announced.

        Returns the dropped names so the caller can put them on the response and
        the consumers can warn about a partial drop.
        """
        # Names come from model output and the model id is request-supplied, so
        # both reach the log only after sanitizing: either carrying a newline
        # could otherwise forge a log line. Each value is sanitized directly at
        # the point it is interpolated -- .github/codeql/extensions.yml models
        # sanitize_for_logging's return value as safe, and that model tracks a
        # direct call, not taint laundered through a list first.
        names = [
            sanitize_for_logging(tool_call_function_field(tc, "name", "")) or "unknown"
            for tc in malformed
        ]
        # The copy is keyed on the output limit specifically: a content filter
        # also cuts a response off, but "ran out of room" would be a false
        # explanation. The *repair policy* uses the broader check, because any
        # unclean finish means the last call may be incomplete.
        truncated = finish_reason == "length"
        # Allow-listed rather than interpolated: finish_reason is provider text.
        safe_reason = finish_reason if finish_reason in KNOWN_FINISH_REASONS else "other"
        logger.error(
            "Dropping %d malformed tool call(s) from %s (finish_reason=%s, names=%s); "
            "%d well-formed call(s) kept",
            len(malformed),
            sanitize_for_logging(model_name),
            safe_reason,
            sanitize_for_logging(", ".join(names)),
            len(kept),
        )
        set_attrs(span, {
            "malformed_tool_calls": len(malformed),
            "finish_reason": safe_reason,
        })
        log_metric(
            "malformed_tool_call", user_email, model=model_name,
            dropped=len(malformed), kept=len(kept), finish_reason=safe_reason,
        )
        if kept:
            return names
        if truncated:
            message = (
                "The model ran out of room while writing its tool call, so the "
                "request was cut off mid-argument. Please try again -- asking for "
                "one thing at a time usually clears it. (Starting a new "
                "conversation shortens the input, which does not help here: the "
                "limit was reached on the way out, not on the way in.)"
            )
        else:
            message = (
                "The model produced a tool call that was not valid JSON, so it "
                "could not be run. Please try again."
            )
        raise LLMMalformedToolCallError(message, tool_names=names, truncated=truncated)

    def _guard_tool_calls(
        self,
        tool_calls: Optional[List[Any]],
        finish_reason: Optional[str],
        model_name: str,
        user_email: Optional[str] = None,
        span: Any = None,
    ) -> Tuple[Optional[List[Any]], List[str], bool]:
        """Apply the malformed-tool-call guard to one response.

        Shared by the streaming and non-streaming callers so the policy -- and
        the ``finish_reason`` rule behind the user-facing copy -- lives in one
        place and cannot drift between them. Returns
        ``(kept_calls, dropped_names, dropped_were_truncated)``; raises
        ``LLMMalformedToolCallError`` when nothing usable survives.
        """
        if not tool_calls:
            return tool_calls or None, [], False
        kept, malformed = partition_tool_calls_by_json_validity(
            tool_calls, finish_reason=finish_reason,
        )
        dropped: List[str] = []
        if malformed:
            dropped = self._handle_malformed_tool_calls(
                malformed,
                kept=kept,
                finish_reason=finish_reason,
                model_name=model_name,
                span=span,
                user_email=user_email,
            )
        # An empty array is a shape providers reject on the follow-up request,
        # so normalize it away rather than leaving it for the loop to strip.
        return kept or None, dropped, finish_reason == "length"

    async def stream_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
        user_email: Optional[str] = None,
    ) -> AsyncGenerator[Union[str, LLMResponse], None]:
        """Stream LLM response with tool support.

        Yields str chunks for text content as they arrive.
        Accumulates tool_calls fragments across chunks.
        Yields a final LLMResponse with accumulated tool_calls at the end.
        """
        if not tools_schema:
            async for chunk in self.stream_plain(model_name, messages, temperature=temperature, user_email=user_email):
                yield chunk
            return

        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

        # PreLlmCall hook (GH #713)
        mod = await self._fire_pre_llm_hook(model_name, messages, user_email=user_email, tools_schema=tools_schema)
        if mod is not None:
            model_name, messages, tools_schema = await self._apply_pre_llm_modify(
                mod, model_name, messages, tools_schema, user_email=user_email
            )
            litellm_model = self._get_litellm_model_name(model_name)
            model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

        provider, model_suffix = split_provider(litellm_model)
        span_attrs = {
            "model": litellm_model,
            "provider": provider,
            "model_version": model_suffix,
            "temperature": model_kwargs.get("temperature"),
            "max_tokens": model_kwargs.get("max_tokens"),
            "streaming": True,
            "has_tools": True,
            "tool_choice": tool_choice,
            "tools_schema_count": len(tools_schema),
            "message_count": len(messages),
        }

        with start_span("llm.call", span_attrs) as span:
            start_ns = time.monotonic_ns()
            try:
                total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
                logger.info(
                    "Streaming LLM call with tools: %d messages, %d chars, %d tools",
                    len(messages), total_chars, len(tools_schema),
                )

                response = await acompletion(
                    model=litellm_model,
                    messages=self._prepare_messages(model_name, messages),
                    tools=tools_schema,
                    tool_choice=tool_choice,
                    stream=True,
                    **model_kwargs,
                )

                accumulated_content = ""
                accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}
                chunk_count = 0
                finish_reason: Optional[str] = None

                async for chunk in response:
                    choice = chunk.choices[0] if chunk.choices else None
                    if choice is not None:
                        # Providers send the reason on the final chunk; keep the
                        # last non-empty one so a truncated turn ("length") can
                        # be told apart from a model that simply emitted bad JSON.
                        finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                    delta = choice.delta if choice is not None else None
                    if not delta:
                        continue

                    # Yield text content as it arrives
                    if delta.content:
                        accumulated_content += delta.content
                        yield delta.content
                        chunk_count += 1
                        # Yield control periodically to prevent backpressure buildup
                        if chunk_count % 50 == 0:
                            await asyncio.sleep(0)

                    # Accumulate tool call fragments
                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index if hasattr(tc_delta, "index") else 0
                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": getattr(tc_delta, "id", None) or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            entry = accumulated_tool_calls[idx]
                            if hasattr(tc_delta, "id") and tc_delta.id:
                                entry["id"] = tc_delta.id
                            if hasattr(tc_delta, "function") and tc_delta.function:
                                if hasattr(tc_delta.function, "name") and tc_delta.function.name:
                                    entry["function"]["name"] += tc_delta.function.name
                                if hasattr(tc_delta.function, "arguments") and tc_delta.function.arguments:
                                    entry["function"]["arguments"] += tc_delta.function.arguments

                # Build final tool_calls list using litellm's own response type
                # rather than SimpleNamespace. The objects are appended verbatim
                # to the assistant message and re-sent on the follow-up request
                # (agentic loop). litellm's Bedrock Converse transformer accesses
                # tool calls with dict syntax (``"function" in tool``,
                # ``tool["id"]``); SimpleNamespace supports only attribute access,
                # so a Bedrock follow-up raised "Unable to convert openai tool
                # calls to bedrock tool calls". ChatCompletionMessageToolCall
                # supports BOTH attribute access (used by the tool executor) and
                # dict access (used by the Bedrock transform), matching the
                # non-streaming path exactly.
                tool_calls_list = None
                if accumulated_tool_calls:
                    tool_calls_list = []
                    for k in sorted(accumulated_tool_calls.keys()):
                        tc = accumulated_tool_calls[k]
                        tool_calls_list.append(ChatCompletionMessageToolCall(
                            id=tc["id"],
                            type=tc["type"],
                            function=Function(
                                name=tc["function"]["name"],
                                arguments=tc["function"]["arguments"],
                            ),
                        ))

                # Reject tool calls whose arguments are not parseable JSON
                # before they can be executed or appended to the conversation.
                # A model that runs out of output tokens mid-tool-call leaves a
                # fragment like ``{"filename": "long-name.c``; the provider
                # re-parses every tool call on the next request, so persisting
                # one poisons the conversation permanently -- every later turn
                # comes back as a 400 that no retry can clear.
                tool_calls_list, dropped_tool_calls, dropped_were_truncated = (
                    self._guard_tool_calls(
                        tool_calls_list, finish_reason, model_name,
                        user_email=user_email, span=span,
                    )
                )

                log_metric(
                    "llm_call", user_email, model=model_name,
                    message_count=len(messages),
                    tool_count=len(tool_calls_list) if tool_calls_list else 0,
                )
                set_attrs(span, {
                    "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "chunk_count": chunk_count,
                    "output_chars": len(accumulated_content),
                    "output_tokens_estimate": len(accumulated_content) // 4,
                    "tool_calls_count": len(tool_calls_list) if tool_calls_list else 0,
                    "retry_count": 0,
                })

                # Opt-in fine-tune capture: record full I/O for this call when a
                # consenting user's turn has an active capture context. No-op and
                # cheap when capture is off (the common case).
                record_llm_call(messages, tools_schema, accumulated_content, tool_calls_list)

                yield LLMResponse(
                    content=accumulated_content,
                    tool_calls=tool_calls_list,
                    model_used=model_name,
                    dropped_tool_calls=dropped_tool_calls or None,
                    dropped_tool_calls_truncated=dropped_were_truncated,
                )

            except Exception as exc:
                set_attrs(span, {
                    "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(exc).__name__,
                })
                logger.error("Error in streaming LLM call with tools: %s", exc, exc_info=True)
                self._raise_llm_domain_error(exc, tools_schema=tools_schema)

    async def stream_with_rag(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        user_email: str,
        rag_service=None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response with RAG integration.

        Runs RAG query (non-streaming), then streams the LLM call.
        """
        if not data_sources:
            async for chunk in self.stream_plain(model_name, messages, temperature=temperature, user_email=user_email):
                yield chunk
            return

        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            raise ValueError("RAG service not configured")

        # Query RAG sources (non-streaming)
        source_responses, rag_exclusions, rag_failures = await self._query_all_rag_sources(
            data_sources, rag_service, user_email, messages,
        )

        if not source_responses:
            if rag_failures:
                # Every RAG query failed: tell the LLM (and thus the user)
                # instead of silently answering as if nothing happened (GH #844).
                logger.warning(
                    "[stream+RAG] All RAG sources failed (%d); informing LLM and user",
                    len(rag_failures),
                )
                messages_with_rag = messages.copy()
                messages_with_rag.insert(
                    self._rag_insert_index(messages_with_rag),
                    {
                        "role": "system",
                        "content": (
                            "The user selected data sources for RAG retrieval, but "
                            "every query failed and no context was retrieved.\n"
                            f"{self._build_rag_failure_notice(rag_failures).strip()}\n\n"
                            "Answer the user's question, but begin by telling them you "
                            "were unable to retrieve any context from their selected "
                            "data sources and that they may need to retry or contact "
                            "an administrator."
                        ),
                    },
                )
                async for chunk in self.stream_plain(model_name, messages_with_rag, temperature=temperature, user_email=user_email):
                    yield chunk
                return
            async for chunk in self.stream_plain(model_name, messages, temperature=temperature, user_email=user_email):
                yield chunk
            return

        # Single source with direct completion
        rag_metadata = None
        if len(data_sources) == 1:
            display_source, rag_response = source_responses[0]
            if rag_response.is_completion:
                yield self._build_rag_completion_response(rag_response, display_source)
                return
            rag_content = rag_response.content
            rag_metadata = rag_response.metadata
            context_label = f"Retrieved context from {display_source}"
        else:
            rag_content, rag_metadata = self._combine_rag_contexts(source_responses)
            context_label = f"Retrieved context from {len(source_responses)} RAG sources"

        # Build citation instructions from metadata
        citation_block = ""
        if rag_metadata:
            citation_block = self._build_citation_instructions(rag_metadata)

        messages_with_rag = messages.copy()
        messages_with_rag.insert(self._rag_insert_index(messages_with_rag), {
            "role": "system",
            "content": (
                f"{context_label}:\n\n{rag_content}"
                f"{self._build_rag_exclusion_notice(rag_exclusions)}"
                f"{self._build_rag_failure_notice(rag_failures)}"
                f"{citation_block}\n\n"
                "Use this context to inform your response. "
                "Cite sources inline using [1], [2], etc. where applicable."
            ),
        })

        async for chunk in self.stream_plain(model_name, messages_with_rag, temperature=temperature, user_email=user_email):
            yield chunk

        # Yield the references section as a final chunk
        if rag_metadata:
            references_section = self._format_rag_references(rag_metadata)
            if references_section:
                yield f"\n\n---\n{references_section}"
