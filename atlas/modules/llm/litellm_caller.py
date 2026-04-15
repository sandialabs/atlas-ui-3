"""
LiteLLM-based LLM calling interface that handles all modes of LLM interaction.

This module provides a clean interface for calling LLMs using LiteLLM in different modes:
- Plain LLM calls (no tools)
- LLM calls with RAG integration
- LLM calls with tool support
- LLM calls with both RAG and tools

LiteLLM provides unified access to multiple LLM providers with automatic
fallbacks, cost tracking, and provider-specific optimizations.
"""

import asyncio
import logging
import os
import random
import re
import warnings
from collections import defaultdict
from typing import Any, Dict, List, NoReturn, Optional, Tuple

# Suppress Pydantic deprecation warnings from litellm's response processing.
# litellm accesses Pydantic v2.11+ deprecated instance attributes
# (model_fields, model_computed_fields) on every streaming chunk, generating
# thousands of warnings per response.  These are cosmetic -- litellm handles
# them correctly -- and the spam can mask real issues in logs.
try:
    from pydantic import PydanticDeprecatedSince211
    warnings.filterwarnings("ignore", category=PydanticDeprecatedSince211)
except ImportError:
    pass  # Pydantic <2.11 does not define this category; suppression not needed

import litellm
from litellm import acompletion

from atlas.core.metrics_logger import log_metric
from atlas.domain.errors import (
    CONTEXT_WINDOW_KEYWORDS,
    ContextWindowExceededError,
    LLMAuthenticationError,
    LLMServiceError,
    LLMTimeoutError,
    RateLimitError,
)
from atlas.modules.config.config_manager import resolve_env_var

from .litellm_streaming import LiteLLMStreamingMixin
from .models import LLMResponse

logger = logging.getLogger(__name__)

# Configure LiteLLM settings
litellm.drop_params = True  # Drop unsupported params instead of erroring

# Retry configuration for transient LLM errors
MAX_LLM_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0


class LiteLLMCaller(LiteLLMStreamingMixin):
    """Clean interface for all LLM calling patterns using LiteLLM.

    Note: this class may set provider-specific LLM API key environment
    variables (for example ``OPENAI_API_KEY``) to maintain compatibility
    with LiteLLM's internal provider detection. These mutations are
    best-effort only and are not intended to provide strong isolation
    guarantees in multi-tenant or highly concurrent environments.
    """

    def __init__(self, llm_config=None, debug_mode: bool = False, rag_service=None):
        """Initialize with optional config dependency injection.

        Args:
            llm_config: LLM configuration object
            debug_mode: Enable verbose LiteLLM logging (overridden by feature flag)
            rag_service: UnifiedRAGService for RAG-augmented calls
        """
        if llm_config is None:
            from atlas.modules.config import config_manager
            self.llm_config = config_manager.llm_config
        else:
            self.llm_config = llm_config

        # Store RAG service for RAG queries
        self._rag_service = rag_service

        # Set litellm verbosity based on debug mode, but respect the suppress feature flag
        # The feature flag takes precedence - if suppression is enabled, never set verbose
        from atlas.modules.config.config_manager import get_app_settings
        app_settings = get_app_settings()
        if app_settings.feature_suppress_litellm_logging:
            litellm.set_verbose = False
        else:
            litellm.set_verbose = debug_mode

    @staticmethod
    def _raise_llm_domain_error(exc: Exception) -> NoReturn:
        """Classify a litellm exception and raise the corresponding domain error.

        This ensures the WebSocket handler receives specific error types
        (RateLimitError, LLMTimeoutError, etc.) instead of a generic Exception,
        which allows it to send meaningful error messages to the frontend.
        """
        error_str = str(exc)
        error_type = type(exc).__name__

        # Map litellm exception types to domain errors
        if isinstance(exc, litellm.RateLimitError) or "rate limit" in error_str.lower():
            raise RateLimitError(
                "The LLM service is experiencing high traffic. Please try again in a moment."
            ) from exc
        if isinstance(exc, litellm.Timeout) or "timeout" in error_str.lower():
            raise LLMTimeoutError(
                "The LLM service request timed out. Please try again."
            ) from exc
        if isinstance(exc, litellm.AuthenticationError) or any(
            kw in error_str.lower()
            for kw in ("unauthorized", "authentication", "invalid api key", "invalid_api_key")
        ):
            raise LLMAuthenticationError(
                "There was an authentication issue with the LLM service. "
                "Please check your API key or contact your administrator."
            ) from exc
        if isinstance(exc, litellm.ContextWindowExceededError) or any(
            kw in error_str.lower() for kw in CONTEXT_WINDOW_KEYWORDS
        ):
            raise ContextWindowExceededError(
                "Your conversation is too long for this model's context window. "
                "Please start a new conversation or switch to a model with a larger context window."
            ) from exc

        # All other LLM errors get a generic but user-friendly message
        # Include the original error type in the log-level message for debugging
        logger.error("LLM call failed (%s): %s", error_type, error_str)
        raise LLMServiceError(
            "The LLM service encountered an error. Please try again or select a different model."
        ) from exc

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        """Check if an LLM error is transient and worth retrying.

        Auth errors are never retryable. Rate limits, timeouts, and
        generic service errors (5xx) are retried with backoff.
        """
        error_str = str(exc).lower()

        # Auth errors will never succeed on retry
        if isinstance(exc, litellm.AuthenticationError) or any(
            kw in error_str
            for kw in ("unauthorized", "authentication", "invalid api key", "invalid_api_key")
        ):
            return False

        # Context window errors will never succeed on retry
        if isinstance(exc, litellm.ContextWindowExceededError) or any(
            kw in error_str for kw in CONTEXT_WINDOW_KEYWORDS
        ):
            return False

        # Rate limit, timeout, and server errors are transient
        if isinstance(exc, (litellm.RateLimitError, litellm.Timeout)):
            return True
        if any(
            kw in error_str
            for kw in ("rate limit", "timeout", "timed out", "server error", "503", "502", "429")
        ):
            return True

        # ServiceUnavailableError if litellm exposes it
        if hasattr(litellm, "ServiceUnavailableError") and isinstance(
            exc, litellm.ServiceUnavailableError
        ):
            return True

        return False

    async def _acompletion_with_retry(self, **kwargs):
        """Call litellm.acompletion with automatic retry for transient errors.

        Retries up to MAX_LLM_RETRIES times with exponential backoff and jitter.
        Auth errors are raised immediately without retry.
        """
        last_exc = None
        for attempt in range(MAX_LLM_RETRIES + 1):
            try:
                return await acompletion(**kwargs)
            except Exception as exc:
                last_exc = exc
                remaining = MAX_LLM_RETRIES - attempt
                if remaining > 0 and self._is_retryable_error(exc):
                    delay = RETRY_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        MAX_LLM_RETRIES + 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        raise last_exc  # pragma: no cover – loop always raises or returns

    @staticmethod
    def _parse_qualified_data_source(qualified_data_source: str) -> str:
        """Extract corpus name from a qualified data source identifier.

        Qualified data sources have format "server:source_id" (e.g., "atlas_rag:technical-docs").
        The prefix is used for routing in multi-RAG setups, but the RAG API expects just
        the corpus name.

        Args:
            qualified_data_source: Data source ID, optionally prefixed with server name.

        Returns:
            The corpus/source name without the server prefix.
        """
        if ":" in qualified_data_source:
            _, data_source = qualified_data_source.split(":", 1)
            logger.debug("Stripped RAG server prefix: %s -> %s", qualified_data_source, data_source)
            return data_source
        return qualified_data_source

    def _build_rag_completion_response(
        self,
        rag_response,
        display_source: str
    ) -> str:
        """Build formatted response for direct RAG completions.

        Args:
            rag_response: RAGResponse object with is_completion=True
            display_source: Display name of the data source

        Returns:
            Formatted response string with RAG completion note and metadata
        """
        response_parts = []
        response_parts.append(f"*Response from {display_source} (RAG completions endpoint):*\n")
        response_parts.append(rag_response.content)

        # Append references if available
        if rag_response.metadata:
            references_section = self._format_rag_references(rag_response.metadata)
            if references_section:
                response_parts.append(f"\n\n---\n{references_section}")

        return "\n".join(response_parts)

    async def _query_all_rag_sources(
        self,
        data_sources: List[str],
        rag_service,
        user_email: str,
        messages: List[Dict[str, str]],
    ) -> List[Tuple[str, Any]]:
        """Query all RAG data sources in parallel, batching by server.

        Sources sharing the same server are sent as a single batched request
        (one HTTP call with multiple corpora) instead of N separate calls.
        Different servers are queried in parallel.

        Args:
            data_sources: Qualified data source identifiers (server:source_id).
            rag_service: UnifiedRAGService instance.
            user_email: User email for access control.
            messages: Conversation messages for RAG context.

        Returns:
            List of (display_source, rag_response) tuples, one per server batch.
        """
        # Group data sources by server
        server_groups: Dict[str, List[str]] = defaultdict(list)
        for qualified_source in data_sources:
            if ":" in qualified_source:
                server_name = qualified_source.split(":", 1)[0]
            else:
                server_name = "__default__"
            server_groups[server_name].append(qualified_source)

        logger.info(
            "[RAG] Batching %d sources across %d server(s): %s",
            len(data_sources),
            len(server_groups),
            {k: len(v) for k, v in server_groups.items()},
        )

        async def _query_server_batch(server_name: str, sources: List[str]):
            # Build a display label from all source names in this batch
            display_parts = [self._parse_qualified_data_source(s) for s in sources]
            display = ", ".join(display_parts)

            if len(sources) == 1:
                # Single source: use the existing single-source path
                response = await rag_service.query_rag(user_email, sources[0], messages)
            else:
                # Multiple sources on same server: batch into one request
                response = await rag_service.query_rag_batch(
                    user_email, sources, messages,
                )
            return (display, response)

        results = await asyncio.gather(
            *[_query_server_batch(srv, srcs) for srv, srcs in server_groups.items()],
            return_exceptions=True,
        )

        successful: List[Tuple[str, Any]] = []
        for (server_name, _sources), result in zip(server_groups.items(), results):
            if isinstance(result, Exception):
                logger.error("[RAG] Failed to query server %s: %s", server_name, result)
            else:
                successful.append(result)

        return successful

    @staticmethod
    def _combine_rag_contexts(
        source_responses: List[Tuple[str, Any]],
    ) -> Tuple[str, Optional[Any]]:
        """Combine RAG responses from multiple sources into a single context block.

        Args:
            source_responses: List of (display_source, rag_response) tuples.

        Returns:
            (combined_content, merged_metadata) -- merged_metadata is the metadata
            from the first source that has it, or None.
        """
        parts: List[str] = []
        merged_metadata = None

        for display_source, rag_response in source_responses:
            content = rag_response.content if rag_response.content else ""
            parts.append(f"### Context from {display_source}:\n{content}")
            if rag_response.metadata and merged_metadata is None:
                merged_metadata = rag_response.metadata

        combined = "\n\n".join(parts)
        return combined, merged_metadata

    def _get_litellm_model_name(self, model_name: str) -> str:
        """Convert internal model name to LiteLLM compatible format."""
        if model_name not in self.llm_config.models:
            raise ValueError(f"Model {model_name} not found in configuration")

        model_config = self.llm_config.models[model_name]
        model_id = model_config.model_name

        # Map common providers to LiteLLM format.
        # Order matters: check specific providers (groq, openrouter) before
        # generic ones (openai) since some URLs contain "openai" in the path
        # (e.g. api.groq.com/openai/v1).
        if "openrouter" in model_config.model_url:
            return f"openrouter/{model_id}"
        elif "groq" in model_config.model_url:
            # Groq uses OpenAI-compatible endpoints; use openai/ prefix with
            # api_base override (set in _get_model_kwargs) so litellm routes
            # to the correct base URL.
            return f"openai/{model_id}"
        elif "openai" in model_config.model_url:
            return f"openai/{model_id}"
        elif "anthropic" in model_config.model_url:
            return f"anthropic/{model_id}"
        elif "google" in model_config.model_url:
            return f"google/{model_id}"
        elif "cerebras" in model_config.model_url:
            return f"cerebras/{model_id}"
        else:
            # For custom endpoints, use the model_id directly
            return model_id

    @staticmethod
    def _resolve_user_api_key(model_name: str, user_email: Optional[str]) -> str:
        """Look up a per-user API key from token storage.

        Raises ValueError when the key is missing so callers surface a clear
        authentication-required error to the user.
        """
        if not user_email:
            raise ValueError(
                f"Model '{model_name}' requires a per-user API key but no user_email was provided."
            )
        from atlas.modules.mcp_tools.token_storage import get_token_storage
        token_storage = get_token_storage()
        stored = token_storage.get_valid_token(user_email, f"llm:{model_name}")
        if stored is None:
            raise ValueError(
                f"Model '{model_name}' requires a per-user API key. "
                f"Please configure your API key in the model settings."
            )
        return stored.token_value

    @staticmethod
    def _resolve_globus_api_key(model_name: str, globus_scope: str, user_email: Optional[str]) -> str:
        """Look up a Globus-provided token for a specific resource server.

        Globus OAuth stores scoped tokens keyed as 'globus:{resource_server}'.
        Models configure which scope to use via the 'globus_scope' field.

        Raises ValueError when the token is missing so callers surface a clear
        authentication-required error to the user.
        """
        if not user_email:
            raise ValueError(
                f"Model '{model_name}' requires Globus authentication but no user_email was provided."
            )
        if not globus_scope:
            raise ValueError(
                f"Model '{model_name}' has api_key_source='globus' but no globus_scope configured."
            )
        from atlas.modules.mcp_tools.token_storage import get_token_storage
        token_storage = get_token_storage()
        storage_key = f"globus:{globus_scope}"
        stored = token_storage.get_valid_token(user_email, storage_key)
        if stored is None:
            raise ValueError(
                f"Model '{model_name}' requires Globus authentication for scope '{globus_scope}'. "
                f"Please log in via Globus to obtain the required access token."
            )
        return stored.token_value

    def _get_model_kwargs(
        self, model_name: str, temperature: Optional[float] = None, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get LiteLLM kwargs for a specific model."""
        if model_name not in self.llm_config.models:
            raise ValueError(f"Model {model_name} not found in configuration")

        model_config = self.llm_config.models[model_name]
        kwargs = {
            "max_tokens": model_config.max_tokens or 1000,
        }

        # Use provided temperature or fall back to config temperature
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = model_config.temperature or 0.7

        # Resolve API key based on api_key_source
        api_key_source = getattr(model_config, "api_key_source", "system")
        if api_key_source == "user":
            api_key = self._resolve_user_api_key(model_name, user_email)
        elif api_key_source == "globus":
            globus_scope = getattr(model_config, "globus_scope", None)
            api_key = self._resolve_globus_api_key(model_name, globus_scope, user_email)
        else:
            # Set API key - resolve environment variables
            try:
                api_key = resolve_env_var(model_config.api_key)
            except ValueError as e:
                logger.error(f"Failed to resolve API key for model {model_name}: {e}")
                raise

        if api_key:
            # Always pass api_key to LiteLLM for all providers
            kwargs["api_key"] = api_key

            # Additionally set provider-specific env vars for LiteLLM's internal logic
            def _set_env_var_if_needed(env_key: str, value: str) -> None:
                existing = os.environ.get(env_key)
                if existing is None:
                    os.environ[env_key] = value
                elif existing != value:
                    logger.warning(
                        "Overwriting existing environment variable %s for model %s",
                        env_key,
                        model_name,
                    )
                    os.environ[env_key] = value

            if "openrouter" in model_config.model_url:
                _set_env_var_if_needed("OPENROUTER_API_KEY", api_key)
            elif "groq" in model_config.model_url:
                _set_env_var_if_needed("GROQ_API_KEY", api_key)
            elif "openai" in model_config.model_url:
                _set_env_var_if_needed("OPENAI_API_KEY", api_key)
            elif "anthropic" in model_config.model_url:
                _set_env_var_if_needed("ANTHROPIC_API_KEY", api_key)
            elif "google" in model_config.model_url:
                _set_env_var_if_needed("GOOGLE_API_KEY", api_key)
            elif "cerebras" in model_config.model_url:
                _set_env_var_if_needed("CEREBRAS_API_KEY", api_key)
            else:
                # Custom endpoint - set OPENAI_API_KEY as fallback for
                # OpenAI-compatible endpoints. This is a heuristic and
                # only updates the env var if it is unset or already
                # matches the same value.
                _set_env_var_if_needed("OPENAI_API_KEY", api_key)

        # Set custom API base for non-standard endpoints
        if hasattr(model_config, 'model_url') and model_config.model_url:
            if not any(provider in model_config.model_url for provider in ["openrouter", "api.openai.com", "api.anthropic.com", "api.cerebras.ai"]):
                kwargs["api_base"] = model_config.model_url

        # Handle extra headers with environment variable expansion
        if model_config.extra_headers:
            extra_headers_resolved = {}
            for header_key, header_value in model_config.extra_headers.items():
                try:
                    resolved_value = resolve_env_var(header_value)
                    extra_headers_resolved[header_key] = resolved_value
                except ValueError as e:
                    logger.error(f"Failed to resolve extra header '{header_key}' for model {model_name}: {e}")
                    raise
            kwargs["extra_headers"] = extra_headers_resolved

        return kwargs

    @staticmethod
    def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip empty tool_calls arrays from messages.

        OpenAI rejects messages where tool_calls is present but empty ([]).
        The field must either be omitted or contain at least one item.
        """
        sanitized = []
        for msg in messages:
            if "tool_calls" in msg and not msg["tool_calls"]:
                msg = {k: v for k, v in msg.items() if k != "tool_calls"}
            sanitized.append(msg)
        return sanitized

    @staticmethod
    def _enforce_strict_role_ordering(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rewrite messages so that system/user messages never directly follow tool messages.

        Mistral models (especially via vLLM) enforce strict role ordering:
        after a tool message, only an assistant message is allowed.  This
        pass converts post-tool system messages to user role and inserts a
        bridging assistant message between tool results and the next
        non-assistant message.

        Note: ``seen_tool`` is a one-way latch — once any tool message has
        appeared in the conversation, all subsequent system messages are
        converted to user role for the remainder of the message list.
        """
        result = []
        seen_tool = False
        last_role = None
        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                seen_tool = True
            # Convert system → user after any tool message has appeared
            if role == "system" and seen_tool:
                msg = {**msg, "role": "user"}
                role = "user"
                logger.debug("strict_role_ordering: converted post-tool system message to user")
            # Insert bridging assistant message when a non-assistant role
            # follows a tool role (Mistral requires assistant after tool)
            if last_role == "tool" and role not in ("tool", "assistant"):
                result.append({"role": "assistant", "content": "(continuing)"})
                logger.debug("strict_role_ordering: inserted bridging assistant message")
            result.append(msg)
            last_role = role
        return result

    def _prepare_messages(
        self, model_name: str, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Sanitize messages and apply model-specific transformations."""
        messages = self._sanitize_messages(messages)
        if model_name in self.llm_config.models:
            model_config = self.llm_config.models[model_name]
            if model_config.strict_role_ordering:
                messages = self._enforce_strict_role_ordering(messages)
        return messages

    async def call_plain(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        user_email: Optional[str] = None
    ) -> str:
        """Plain LLM call - no tools, no RAG.

        Args:
            model_name: Name of the model to use
            messages: List of message dicts with 'role' and 'content'
            temperature: Optional temperature override (uses config default if None)
            max_tokens: Optional max_tokens override (uses config default if None)
            user_email: Optional user email for metrics logging
        """
        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

        # Override max_tokens if provided
        if max_tokens is not None:
            model_kwargs["max_tokens"] = max_tokens

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"Plain LLM call: {len(messages)} messages, {total_chars} chars")

            response = await self._acompletion_with_retry(
                model=litellm_model,
                messages=self._prepare_messages(model_name, messages),
                **model_kwargs
            )

            content = response.choices[0].message.content or ""
            # Log response preview only at DEBUG level to avoid logging sensitive data
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"LLM response preview: '{content[:200]}{'...' if len(content) > 200 else ''}'")
            else:
                logger.info(f"LLM response length: {len(content)} chars")

            log_metric("llm_call", user_email, model=model_name, message_count=len(messages))

            return content

        except Exception as exc:
            logger.error("Error calling LLM: %s", exc, exc_info=True)
            self._raise_llm_domain_error(exc)

    async def call_with_rag(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        user_email: str,
        rag_service=None,
        temperature: float = 0.7,
    ) -> str:
        """LLM call with RAG integration."""
        logger.debug(
            "[LLM+RAG] call_with_rag called: model=%s, data_sources=%s, user=%s, message_count=%d",
            model_name,
            data_sources,
            user_email,
            len(messages),
        )

        if not data_sources:
            logger.debug("[LLM+RAG] No data sources provided, falling back to plain LLM call")
            return await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)

        # Use provided service or instance service
        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            logger.error("[LLM+RAG] RAG service not configured")
            raise ValueError("RAG service not configured")

        multi_source = len(data_sources) > 1
        if multi_source:
            logger.warning(
                "[LLM+RAG] Multiple RAG sources selected (%d). All results will be "
                "treated as raw context and sent through LLM, even if some sources "
                "return pre-interpreted completions.",
                len(data_sources),
            )

        logger.info(
            "[LLM+RAG] Querying RAG: sources=%s, user=%s",
            data_sources,
            user_email,
        )

        try:
            # Query all RAG sources in parallel
            source_responses = await self._query_all_rag_sources(
                data_sources, rag_service, user_email, messages,
            )

            if not source_responses:
                logger.warning("[LLM+RAG] All RAG sources failed, falling back to plain LLM call")
                return await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)

            # Single source: preserve existing is_completion shortcut
            if not multi_source:
                display_source, rag_response = source_responses[0]

                logger.debug(
                    "[LLM+RAG] RAG response received: content_length=%d, has_metadata=%s, is_completion=%s",
                    len(rag_response.content) if rag_response.content else 0,
                    rag_response.metadata is not None,
                    rag_response.is_completion,
                )

                if rag_response.is_completion:
                    logger.info(
                        "[LLM+RAG] RAG returned chat completion - returning directly without LLM processing"
                    )
                    final_response = self._build_rag_completion_response(rag_response, display_source)
                    logger.info(
                        "[LLM+RAG] Returning RAG completion directly: response_length=%d",
                        len(final_response),
                    )
                    return final_response

                rag_content = rag_response.content
                rag_metadata = rag_response.metadata
                context_label = f"Retrieved context from {display_source}"
            else:
                # Multiple sources: combine all as raw context
                rag_content, rag_metadata = self._combine_rag_contexts(source_responses)
                context_label = f"Retrieved context from {len(source_responses)} RAG sources"

            # Build citation instructions from metadata (if available)
            citation_block = ""
            if rag_metadata:
                citation_block = self._build_citation_instructions(rag_metadata)

            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system",
                "content": (
                    f"{context_label}:\n\n{rag_content}"
                    f"{citation_block}\n\n"
                    "Use this context to inform your response. "
                    "Cite sources inline using [1], [2], etc. where applicable."
                ),
            }
            messages_with_rag.insert(-1, rag_context_message)

            logger.debug("[LLM+RAG] Calling LLM with RAG-enriched context...")
            llm_response = await self.call_plain(model_name, messages_with_rag, temperature=temperature, user_email=user_email)

            # Only append references if RAG actually provided useful content
            rag_content_useful = bool(
                rag_content
                and rag_content.strip()
                and rag_content not in (
                    "No response from RAG system.",
                    "No response from MCP RAG.",
                    "No matching vehicles found.",
                )
            )

            if rag_content_useful and rag_metadata:
                references_section = self._format_rag_references(rag_metadata)
                if references_section:
                    llm_response += f"\n\n---\n{references_section}"

            logger.info(
                "[LLM+RAG] RAG-integrated query complete: response_length=%d, rag_content_useful=%s",
                len(llm_response),
                rag_content_useful,
            )
            return llm_response

        except (RateLimitError, LLMTimeoutError, LLMAuthenticationError, LLMServiceError, ContextWindowExceededError):
            raise  # Don't mask LLM errors with a fallback retry
        except Exception as exc:
            logger.error("[LLM+RAG] Error in RAG-integrated query: %s", exc, exc_info=True)
            logger.warning("[LLM+RAG] Falling back to plain LLM call due to RAG error")
            return await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)

    async def call_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
        user_email: Optional[str] = None
    ) -> LLMResponse:
        """LLM call with tool support using LiteLLM."""
        if not tools_schema:
            content = await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)
            return LLMResponse(content=content, model_used=model_name)

        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

        # Handle tool_choice parameter - try "required" first, fallback to "auto" if unsupported
        final_tool_choice = tool_choice

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"LLM call with tools: {len(messages)} messages, {total_chars} chars, {len(tools_schema)} tools")

            response = await self._acompletion_with_retry(
                model=litellm_model,
                messages=self._prepare_messages(model_name, messages),
                tools=tools_schema,
                tool_choice=final_tool_choice,
                **model_kwargs
            )

            message = response.choices[0].message

            if tool_choice == "required" and not getattr(message, 'tool_calls', None):
                logger.error(f"LLM failed to return tool calls when tool_choice was 'required'. Full response: {response}")
                raise ValueError("LLM failed to return tool calls when tool_choice was 'required'.")

            tool_calls = getattr(message, 'tool_calls', None)
            tool_count = len(tool_calls) if tool_calls else 0
            log_metric("llm_call", user_email, model=model_name, message_count=len(messages), tool_count=tool_count)

            return LLMResponse(
                content=getattr(message, 'content', None) or "",
                tool_calls=tool_calls,
                model_used=model_name
            )

        except Exception as exc:
            # If we used "required" and it failed, try again with "auto"
            if tool_choice == "required" and final_tool_choice == "required":
                logger.warning(f"Tool choice 'required' failed, retrying with 'auto': {exc}")
                try:
                    response = await self._acompletion_with_retry(
                        model=litellm_model,
                        messages=self._prepare_messages(model_name, messages),
                        tools=tools_schema,
                        tool_choice="auto",
                        **model_kwargs
                    )

                    message = response.choices[0].message
                    return LLMResponse(
                        content=getattr(message, 'content', None) or "",
                        tool_calls=getattr(message, 'tool_calls', None),
                        model_used=model_name
                    )
                except Exception as retry_exc:
                    logger.error("Retry with tool_choice='auto' also failed: %s", retry_exc, exc_info=True)
                    self._raise_llm_domain_error(retry_exc)

            logger.error("Error calling LLM with tools: %s", exc, exc_info=True)
            self._raise_llm_domain_error(exc)

    async def call_with_rag_and_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        tools_schema: List[Dict],
        user_email: str,
        tool_choice: str = "auto",
        rag_service=None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Full integration: RAG + Tools."""
        logger.debug(
            "[LLM+RAG+Tools] call_with_rag_and_tools called: model=%s, data_sources=%s, user=%s, tools_count=%d",
            model_name,
            data_sources,
            user_email,
            len(tools_schema) if tools_schema else 0,
        )

        if not data_sources:
            logger.debug("[LLM+RAG+Tools] No data sources provided, falling back to tools-only call")
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

        # Use provided service or instance service
        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            logger.error("[LLM+RAG+Tools] RAG service not configured")
            raise ValueError("RAG service not configured")

        multi_source = len(data_sources) > 1
        if multi_source:
            logger.warning(
                "[LLM+RAG+Tools] Multiple RAG sources selected (%d). All results will be "
                "treated as raw context and sent through LLM, even if some sources "
                "return pre-interpreted completions.",
                len(data_sources),
            )

        logger.info(
            "[LLM+RAG+Tools] Querying RAG: sources=%s, user=%s",
            data_sources,
            user_email,
        )

        try:
            # Query all RAG sources in parallel
            source_responses = await self._query_all_rag_sources(
                data_sources, rag_service, user_email, messages,
            )

            if not source_responses:
                logger.warning("[LLM+RAG+Tools] All RAG sources failed, falling back to tools-only call")
                return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

            # Single source: preserve existing is_completion shortcut
            if not multi_source:
                display_source, rag_response = source_responses[0]

                logger.debug(
                    "[LLM+RAG+Tools] RAG response received: content_length=%d, has_metadata=%s, is_completion=%s",
                    len(rag_response.content) if rag_response.content else 0,
                    rag_response.metadata is not None,
                    rag_response.is_completion,
                )

                if rag_response.is_completion:
                    logger.info(
                        "[LLM+RAG+Tools] RAG returned completion - injecting as context (tools still available)"
                    )
                    rag_content = self._build_rag_completion_response(rag_response, display_source)
                    context_label = f"Pre-synthesized answer from {display_source}"
                else:
                    rag_content = rag_response.content
                    context_label = f"Retrieved context from {display_source}"
                rag_metadata = rag_response.metadata
            else:
                # Multiple sources: combine all as raw context
                rag_content, rag_metadata = self._combine_rag_contexts(source_responses)
                context_label = f"Retrieved context from {len(source_responses)} RAG sources"

            # Build citation instructions from metadata (if available)
            citation_block = ""
            if rag_metadata:
                citation_block = self._build_citation_instructions(rag_metadata)

            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system",
                "content": (
                    f"{context_label}:\n\n{rag_content}"
                    f"{citation_block}\n\n"
                    "Use this context to inform your response. "
                    "Cite sources inline using [1], [2], etc. where applicable."
                ),
            }
            messages_with_rag.insert(-1, rag_context_message)

            logger.debug("[LLM+RAG+Tools] Calling LLM with RAG-enriched context and tools...")
            llm_response = await self.call_with_tools(model_name, messages_with_rag, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

            # Only append references if RAG actually provided useful content
            rag_content_useful = bool(
                rag_content
                and rag_content.strip()
                and rag_content not in (
                    "No response from RAG system.",
                    "No response from MCP RAG.",
                    "No matching vehicles found.",
                )
            )

            # Always append references when RAG provided useful content,
            # even when tool calls were present — the references are relevant
            # to the RAG context that informed the LLM's decisions.
            if rag_content_useful and rag_metadata:
                references_section = self._format_rag_references(rag_metadata)
                if references_section:
                    llm_response.content += f"\n\n---\n{references_section}"

            logger.info(
                "[LLM+RAG+Tools] RAG+tools query complete: response_length=%d, has_tool_calls=%s, rag_content_useful=%s",
                len(llm_response.content) if llm_response.content else 0,
                llm_response.has_tool_calls(),
                rag_content_useful,
            )
            return llm_response

        except (RateLimitError, LLMTimeoutError, LLMAuthenticationError, LLMServiceError, ContextWindowExceededError):
            raise  # Don't mask LLM errors with a fallback retry
        except Exception as exc:
            logger.error("[LLM+RAG+Tools] Error in RAG+tools integrated query: %s", exc, exc_info=True)
            logger.warning("[LLM+RAG+Tools] Falling back to tools-only call due to RAG error")
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

    @staticmethod
    def _sanitize_label(text: str) -> str:
        """Strip markdown/prompt-injection characters from a metadata label."""
        # Remove characters that could break markdown structure or inject prompts
        cleaned = re.sub(r"[*\[\](){}<>`#\n\r\\]", "", text)
        return cleaned.strip()[:200]

    @staticmethod
    def _build_citation_instructions(metadata) -> str:
        """Build inline-citation instructions for the LLM system prompt.

        Produces a numbered source list and asks the model to cite sources
        using bracketed numbers (e.g. [1], [2]) in its answer — similar to
        the Perplexity AI citation style.

        Returns an empty string when no usable documents are available.
        """
        from atlas.modules.rag.client import RAGMetadata

        if not isinstance(metadata, RAGMetadata) or not metadata.documents_found:
            return ""

        lines = [
            "",
            "## Source documents (for inline citations)",
            "When you use information from these sources, cite them inline using "
            "bracketed numbers like [1], [2], etc. Place citations immediately after "
            "the claim they support. You may cite multiple sources for the same "
            "claim, e.g. [1][3]. Do not fabricate citations — only cite sources "
            "listed below.",
            "",
        ]

        for i, doc in enumerate(metadata.documents_found, start=1):
            raw_label = doc.title or doc.source or f"Document {i}"
            label = LiteLLMCaller._sanitize_label(raw_label)
            if not label:
                label = f"Document {i}"
            parts = [f"[{i}] **{label}**"]
            if doc.url:
                parts.append(f"  URL: {doc.url}")
            if doc.source:
                safe_source = LiteLLMCaller._sanitize_label(doc.source)
                if safe_source and safe_source != label:
                    parts.append(f"  Source: {safe_source}")
            confidence_pct = int(doc.confidence_score * 100)
            parts.append(f"  Relevance: {confidence_pct}%")
            if doc.last_modified:
                parts.append(f"  Updated: {doc.last_modified}")
            lines.append("\n".join(parts))

        return "\n".join(lines)

    @staticmethod
    def _format_rag_references(metadata) -> str:
        """Format RAG metadata into a numbered references section.

        Produces a Perplexity-style references block that pairs with the
        inline [1], [2] citations the LLM was instructed to emit.

        Returns empty string when metadata is unusable.
        """
        from atlas.modules.rag.client import RAGMetadata

        if not isinstance(metadata, RAGMetadata) or not metadata.documents_found:
            return ""

        lines = ["**References**", ""]
        for i, doc in enumerate(metadata.documents_found, start=1):
            raw_label = doc.title or doc.source or f"Document {i}"
            label = LiteLLMCaller._sanitize_label(raw_label)
            if not label:
                label = f"Document {i}"
            confidence_pct = int(doc.confidence_score * 100)

            if doc.url:
                # URL is already validated to http(s) by DocumentMetadata
                # Escape parens in URL to prevent markdown injection
                safe_url = doc.url.replace("(", "%28").replace(")", "%29")
                entry = f"{i}. [{label}]({safe_url})"
            else:
                entry = f"{i}. {label}"

            detail_parts = []
            if doc.source:
                safe_source = LiteLLMCaller._sanitize_label(doc.source)
                if safe_source and safe_source != label:
                    detail_parts.append(safe_source)
            detail_parts.append(f"{confidence_pct}% relevance")
            if doc.last_modified:
                detail_parts.append(f"updated {doc.last_modified}")

            entry += f" — {', '.join(detail_parts)}"
            lines.append(entry)

        lines.append(f"\n*{metadata.data_source_name} · {metadata.retrieval_method} · {metadata.query_processing_time_ms}ms*")
        return "\n".join(lines)

    def _format_rag_metadata(self, metadata) -> str:
        """Format RAG metadata — delegates to _format_rag_references.

        Kept for backward compatibility with call sites that check the return
        value against 'Metadata unavailable'.
        """
        result = self._format_rag_references(metadata)
        return result if result else "Metadata unavailable"
