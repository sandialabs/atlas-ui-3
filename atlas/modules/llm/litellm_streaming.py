"""Streaming methods for LiteLLMCaller.

Extracted to keep litellm_caller.py under the 400-line guideline.
These methods are mixed into LiteLLMCaller via LiteLLMStreamingMixin.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from litellm import acompletion

from atlas.core.metrics_logger import log_metric

from .models import LLMResponse

logger = logging.getLogger(__name__)


class LiteLLMStreamingMixin:
    """Mixin providing streaming LLM methods for LiteLLMCaller.

    Expects the host class to provide:
      - _get_litellm_model_name(model_name) -> str
      - _get_model_kwargs(model_name, temperature, user_email) -> dict
      - _query_all_rag_sources(data_sources, rag_service, user_email, messages) -> list
      - _build_rag_completion_response(rag_response, display_source) -> str
      - _combine_rag_contexts(source_responses) -> tuple
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

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info("Streaming plain LLM call: %d messages, %d chars", len(messages), total_chars)

            response = await acompletion(
                model=litellm_model,
                messages=messages,
                stream=True,
                **model_kwargs,
            )

            chunk_count = 0
            total_chunks_seen = 0
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
                    # Yield control periodically to prevent backpressure buildup
                    if chunk_count % 50 == 0:
                        await asyncio.sleep(0)

            if chunk_count == 0 and total_chunks_seen > 0:
                logger.warning(
                    "Stream for %s received %d chunks but yielded 0 tokens",
                    model_name, total_chunks_seen,
                )
            log_metric("llm_call", user_email, model=model_name, message_count=len(messages))

        except Exception as exc:
            logger.error("Error in streaming LLM call: %s", exc, exc_info=True)
            raise Exception(f"Failed to stream LLM: {exc}") from exc

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

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(
                "Streaming LLM call with tools: %d messages, %d chars, %d tools",
                len(messages), total_chars, len(tools_schema),
            )

            response = await acompletion(
                model=litellm_model,
                messages=messages,
                tools=tools_schema,
                tool_choice=tool_choice,
                stream=True,
                **model_kwargs,
            )

            accumulated_content = ""
            accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}
            chunk_count = 0

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
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

            # Build final tool_calls list as namespace objects (matching
            # litellm's non-streaming response format with attribute access)
            tool_calls_list = None
            if accumulated_tool_calls:
                tool_calls_list = []
                for k in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[k]
                    tool_calls_list.append(SimpleNamespace(
                        id=tc["id"],
                        type=tc["type"],
                        function=SimpleNamespace(
                            name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"],
                        ),
                    ))

            log_metric(
                "llm_call", user_email, model=model_name,
                message_count=len(messages),
                tool_count=len(tool_calls_list) if tool_calls_list else 0,
            )

            yield LLMResponse(
                content=accumulated_content,
                tool_calls=tool_calls_list,
                model_used=model_name,
            )

        except Exception as exc:
            logger.error("Error in streaming LLM call with tools: %s", exc, exc_info=True)
            raise Exception(f"Failed to stream LLM with tools: {exc}") from exc

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
        source_responses = await self._query_all_rag_sources(
            data_sources, rag_service, user_email, messages,
        )

        if not source_responses:
            async for chunk in self.stream_plain(model_name, messages, temperature=temperature, user_email=user_email):
                yield chunk
            return

        # Single source with direct completion
        if len(data_sources) == 1:
            display_source, rag_response = source_responses[0]
            if rag_response.is_completion:
                yield self._build_rag_completion_response(rag_response, display_source)
                return
            rag_content = rag_response.content
            context_label = f"Retrieved context from {display_source}"
        else:
            rag_content, _ = self._combine_rag_contexts(source_responses)
            context_label = f"Retrieved context from {len(source_responses)} RAG sources"

        messages_with_rag = messages.copy()
        messages_with_rag.insert(-1, {
            "role": "system",
            "content": f"{context_label}:\n\n{rag_content}\n\nUse this context to inform your response.",
        })

        async for chunk in self.stream_plain(model_name, messages_with_rag, temperature=temperature, user_email=user_email):
            yield chunk

    async def stream_with_rag_and_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        tools_schema: List[Dict],
        user_email: str,
        tool_choice: str = "auto",
        rag_service=None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[Union[str, LLMResponse], None]:
        """Stream LLM response with both RAG and tool support.

        Runs RAG query (non-streaming), then streams the LLM call with tools.
        """
        if not data_sources:
            async for item in self.stream_with_tools(
                model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email
            ):
                yield item
            return

        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            raise ValueError("RAG service not configured")

        source_responses = await self._query_all_rag_sources(
            data_sources, rag_service, user_email, messages,
        )

        if not source_responses:
            async for item in self.stream_with_tools(
                model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email
            ):
                yield item
            return

        if len(data_sources) == 1:
            display_source, rag_response = source_responses[0]
            if rag_response.is_completion:
                yield LLMResponse(
                    content=self._build_rag_completion_response(rag_response, display_source),
                )
                return
            rag_content = rag_response.content
            context_label = f"Retrieved context from {display_source}"
        else:
            rag_content, _ = self._combine_rag_contexts(source_responses)
            context_label = f"Retrieved context from {len(source_responses)} RAG sources"

        messages_with_rag = messages.copy()
        messages_with_rag.insert(-1, {
            "role": "system",
            "content": f"{context_label}:\n\n{rag_content}\n\nUse this context to inform your response.",
        })

        async for item in self.stream_with_tools(
            model_name, messages_with_rag, tools_schema, tool_choice, temperature=temperature, user_email=user_email
        ):
            yield item
