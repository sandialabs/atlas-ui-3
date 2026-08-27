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
import random
import re
import time
import warnings
from collections import defaultdict
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from atlas.hooks import HookBlockedError, HookEvent, get_hook_manager

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

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.metrics_logger import log_metric
from atlas.core.model_access import ModelAccessDecision, check_model_access
from atlas.core.telemetry import set_attrs, start_span
from atlas.domain.errors import (
    CONTEXT_WINDOW_KEYWORDS,
    ContextWindowExceededError,
    DataSourcePermissionError,
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMError,
    LLMServiceError,
    LLMTimeoutError,
    RateLimitError,
)
from atlas.modules.config.config_manager import resolve_env_var

from .litellm_streaming import LiteLLMStreamingMixin
from .models import LLMResponse, split_provider
from .tool_call_guard import (
    partition_tool_calls_by_json_validity,
    response_was_cut_off,
)

logger = logging.getLogger(__name__)

# Configure LiteLLM settings
litellm.drop_params = True  # Drop unsupported params instead of erroring
# Allow litellm to inject a dummy tool schema for Anthropic requests when the
# message history contains tool_call blocks but the current call doesn't pass
# tools= (e.g. title generation or plain replies on a conversation that earlier
# used tools). Without this, Anthropic's transformer raises UnsupportedParamsError.
litellm.modify_params = True
# Never let litellm reach out to huggingface.co for a tokenizer.  When a
# streamed response carries no usage block, litellm reconstructs token counts
# locally at end-of-stream (stream_chunk_builder), and for models whose name
# contains "llama-3"/"llama-2"/"command-r" that path calls a *blocking*
# Tokenizer.from_pretrained() download from inside the async event loop.  In a
# network-restricted deployment that call stalls the entire single-threaded
# server -- every user's stream, the websocket keepalives, and the /api/health
# probe -- until it finally fails and falls back to tiktoken anyway.  Opting out
# skips straight to the fallback, so token counts are unchanged.
litellm.disable_hf_tokenizer_download = True

# Retry configuration for transient LLM errors
MAX_LLM_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0

# Substrings that mark a provider rejection as being about the tool payload
# rather than the rest of the request. Only when one of these appears (and the
# provider named no specific tool) is it fair to point the user at the whole
# tool selection.  `tool_call`/`tool_calls`/`function_call` are deliberately
# absent: those name the assistant's call blocks in the message history, not
# the tool definitions the user can deselect.
TOOL_REJECTION_MARKERS = re.compile(
    r"\b(tool|tools|tool_choice|function|functions)\b",
    re.IGNORECASE,
)

# A rejection of the conversation itself -- an assistant `tool_calls` block
# with no matching tool response, a stray `role: "tool"` message. The tool
# words in that text are about message history, and deselecting tools does not
# fix it, so it must not put the whole tool selection on trial.
MESSAGE_HISTORY_MARKERS = re.compile(
    r"\b(messages|tool_call_id|role)\b",
    re.IGNORECASE,
)


def _llm_response_attrs(response: Any, attempt: int) -> Dict[str, Any]:
    """Extract per-response attributes from a litellm ModelResponse.

    Returns empty dict when usage/finish_reason are unavailable.
    """
    attrs: Dict[str, Any] = {"retry_count": attempt}
    usage = getattr(response, "usage", None)
    if usage is not None:
        attrs["input_tokens"] = getattr(usage, "prompt_tokens", None)
        attrs["output_tokens"] = getattr(usage, "completion_tokens", None)
        attrs["total_tokens"] = getattr(usage, "total_tokens", None)
    choices = getattr(response, "choices", None) or []
    if choices:
        attrs["finish_reason"] = getattr(choices[0], "finish_reason", None)
        msg = getattr(choices[0], "message", None)
        tool_calls = getattr(msg, "tool_calls", None) if msg else None
        if tool_calls:
            attrs["tool_calls_count"] = len(tool_calls)
    return attrs


class LiteLLMCaller(LiteLLMStreamingMixin):
    """Clean interface for all LLM calling patterns using LiteLLM.

    Note: API keys are passed to LiteLLM per request via the ``api_key``
    kwarg. Provider-specific environment variables are left under admin
    control so custom gateways and aliases keep their configured key source.
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

    async def _fire_pre_llm_hook(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        user_email: Optional[str] = None,
        tools_schema: Optional[List[Dict]] = None,
    ) -> Optional[Dict[str, Any]]:
        """PreLlmCall hook (GH #713).

        Returns a dict of modified fields (``model``, ``messages``, ``tools``)
        when a hook made a ``modify`` decision; returns ``None`` when no hooks
        fired or the verdict was continue/require_approval (no change). Raises
        ``HookBlockedError`` on ``deny`` so the call surfaces the reason rather
        than silently proceeding. Opt-in; zero overhead without hooks.json.

        ``require_approval`` has no approval gate at the LLM layer, so it is
        treated as continue (audited only) -- a hook that needs to block an LLM
        call should use ``deny``.
        """
        mgr = get_hook_manager()
        if mgr is None or not mgr.has_hooks(HookEvent.PRE_LLM_CALL):
            return None
        compliance_level = None
        try:
            from atlas.core.compliance import get_active_compliance_context
            compliance_level, _ = get_active_compliance_context()
        except Exception:
            # Compliance context is informational in the hook envelope; when it is
            # unavailable (no active request context) the hook still runs with
            # compliance_level=None rather than failing the LLM call.
            logger.debug("hooks: no active compliance context for PreLlmCall", exc_info=True)
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "user_email": user_email,
        }
        if tools_schema is not None:
            payload["tools"] = tools_schema
        outcome = await mgr.run_event(
            HookEvent.PRE_LLM_CALL,
            payload,
            session_context={"user_email": user_email, "compliance_level": compliance_level},
        )
        if outcome.verdict == "deny":
            raise HookBlockedError(HookEvent.PRE_LLM_CALL, outcome.reason or "LLM call blocked by policy hook")
        if outcome.modified:
            return outcome.payload
        return None

    async def _apply_pre_llm_modify(
        self,
        mod: Optional[Dict[str, Any]],
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: Optional[List[Dict]] = None,
        *,
        user_email: Optional[str] = None,
    ):
        """Apply a PreLlmCall modify payload.

        Returns ``(model_name, messages, tools_schema)`` after a hook may have
        swapped the model or rewritten messages/tools.

        A model swap is re-authorized against the same per-model group check
        ``ChatOrchestrator`` ran for the originally selected model. Without that,
        a hook could route a turn to a model the user is not cleared for, since
        the orchestrator's check only ever saw the original name. An unauthorized
        swap is refused and the original model is kept.
        """
        if mod is None:
            return model_name, messages, tools_schema
        new_model = mod.get("model")
        if isinstance(new_model, str) and new_model and new_model != model_name:
            if await self._hook_model_swap_allowed(new_model, user_email):
                model_name = new_model
            else:
                logger.warning(
                    "hooks: PreLlmCall model swap to %s refused (user not authorized); "
                    "keeping %s",
                    sanitize_for_logging(new_model),
                    sanitize_for_logging(model_name),
                )
        new_messages = mod.get("messages")
        if isinstance(new_messages, list):
            messages = new_messages
        if tools_schema is not None:
            new_tools = mod.get("tools")
            if isinstance(new_tools, list):
                tools_schema = new_tools
        return model_name, messages, tools_schema

    async def _hook_model_swap_allowed(self, model_name: str, user_email: Optional[str]) -> bool:
        """Re-run the per-model group check for a hook-supplied model name.

        Mirrors ``ChatOrchestrator``'s gate. An unknown model is left to the
        downstream caller (same policy as chat), but a model the user's groups do
        not cover is refused here.
        """
        try:
            models = self.llm_config.models
        except Exception:
            # Without a model registry there is nothing to check against; refuse
            # the swap rather than assume the replacement is authorized.
            logger.warning("hooks: cannot verify PreLlmCall model swap (no llm_config); refusing")
            return False
        decision = await check_model_access(models, model_name, user_email, context="PreLlmCall hook")
        return decision is not ModelAccessDecision.DENIED

    @staticmethod
    def _tools_implicated_by(error_str: str, tools_schema: Optional[List[Dict]]) -> List[str]:
        """Return the tool names a provider rejection points at.

        Attribution requires positive evidence, because most 400s have nothing
        to do with tools (invalid parameters, malformed messages, unsupported
        options) and blaming the tool selection for those sends users off to
        disable tools that were never at fault.

        Two levels of evidence, in order:

        1. The error text names a tool from the request — report only that tool.
        2. The error text points at the tool payload but names no tool — every
           tool in the request is a candidate, so list them all to bisect.
           Rejections of the message history are excluded here: they mention
           tool words, but no tool definition is at fault.

        Anything else attributes nothing.
        """
        names = [
            name
            for tool in (tools_schema or [])
            if (name := (tool.get("function") or {}).get("name"))
        ]
        # Whole-token match: a tool called "calc" must not be blamed for text
        # that merely happens to contain "calculate".
        named = [
            name
            for name in names
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", error_str)
        ]
        if named:
            return named
        if (
            names
            and TOOL_REJECTION_MARKERS.search(error_str)
            and not MESSAGE_HISTORY_MARKERS.search(error_str)
        ):
            return names
        return []

    @staticmethod
    def _raise_llm_domain_error(exc: Exception, tools_schema: Optional[List[Dict]] = None) -> NoReturn:
        """Classify a litellm exception and raise the corresponding domain error.

        This ensures the WebSocket handler receives specific error types
        (RateLimitError, LLMTimeoutError, etc.) instead of a generic Exception,
        which allows it to send meaningful error messages to the frontend.
        """
        # An error we already classified (e.g. a malformed tool call detected
        # while accumulating the stream) carries a precise, user-safe message.
        # Re-running it through the keyword matching below would demote it to a
        # generic service error and match on its own wording, not the failure.
        if isinstance(exc, LLMError):
            raise exc

        error_str = str(exc)
        error_type = type(exc).__name__
        lowered = error_str.lower()

        # Concrete exception types are checked before any message keywords.
        # The keyword tests below match against the provider's text, and a
        # rejection can quote a tool or property named "timeout_checker" or
        # "vault_api_key_lookup" -- exactly the case this attribution exists
        # for. Typing it off the exception class keeps the provider's wording
        # from rerouting it to the wrong branch.
        if isinstance(exc, litellm.RateLimitError):
            raise RateLimitError(
                "The LLM service is experiencing high traffic. Please try again in a moment."
            ) from exc
        if isinstance(exc, litellm.Timeout):
            raise LLMTimeoutError(
                "The LLM service request timed out. Please try again."
            ) from exc
        if isinstance(exc, litellm.AuthenticationError):
            raise LLMAuthenticationError(
                "There was an authentication issue with the LLM service. "
                "Please check your API key or contact your administrator."
            ) from exc
        # Before the BadRequestError branch: this litellm error subclasses it,
        # and providers that report an overflow as a plain 400 are recognised
        # by phrase. The phrases are specific enough not to collide with a
        # tool name, unlike the single words matched further down.
        if isinstance(exc, litellm.ContextWindowExceededError) or any(
            kw in lowered for kw in CONTEXT_WINDOW_KEYWORDS
        ):
            raise ContextWindowExceededError(
                "Your conversation is too long for this model's context window. "
                "Please start a new conversation or switch to a model with a larger context window."
            ) from exc
        # A rejected request is the caller's problem to fix, not a transient
        # service fault.
        if isinstance(exc, litellm.BadRequestError):
            logger.error("LLM rejected the request (%s): %s", error_type, error_str)
            implicated = LiteLLMCaller._tools_implicated_by(error_str, tools_schema)
            if len(implicated) == 1:
                user_msg = (
                    f"The model provider rejected this request because of the tool "
                    f"'{implicated[0]}'. Turn that tool off and try again."
                )
            elif implicated:
                listed = ", ".join(f"'{name}'" for name in implicated)
                user_msg = (
                    f"The model provider rejected this request because of one of the "
                    f"selected tools ({listed}). Turn them off and re-enable them one "
                    f"at a time to find the one at fault."
                )
            else:
                # Deterministic: the same request will be refused again, so do
                # not send the user back to the retry button.
                user_msg = (
                    "The model provider rejected this request as invalid. Retrying "
                    "will not help -- try starting a new conversation, or contact "
                    "support if the issue persists."
                )
            raise LLMBadRequestError(user_msg, tool_names=implicated) from exc

        # Untyped errors (raw provider text, wrapped transports) fall back to
        # keyword matching.
        if "rate limit" in lowered:
            raise RateLimitError(
                "The LLM service is experiencing high traffic. Please try again in a moment."
            ) from exc
        if "timeout" in lowered:
            raise LLMTimeoutError(
                "The LLM service request timed out. Please try again."
            ) from exc
        if any(
            kw in lowered
            for kw in ("unauthorized", "authentication", "invalid api key", "invalid_api_key")
        ):
            raise LLMAuthenticationError(
                "There was an authentication issue with the LLM service. "
                "Please check your API key or contact your administrator."
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

        Auth errors, rejected requests, and context-window overflows are never
        retryable. Rate limits, timeouts, and generic service errors (5xx) are
        retried with backoff.
        """
        error_str = str(exc).lower()

        # A rejected request is deterministic: the identical payload will be
        # refused again. Checked before the transient keyword tests, because a
        # 400 quoting a tool or property named "timeout" would otherwise be
        # retried three times with backoff before failing.
        if isinstance(exc, litellm.BadRequestError):
            return False

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
        litellm_model = kwargs.get("model", "")
        provider, model_suffix = split_provider(litellm_model)
        initial_attrs = {
            "model": litellm_model,
            "provider": provider,
            "model_version": model_suffix,
            "temperature": kwargs.get("temperature"),
            "max_tokens": kwargs.get("max_tokens"),
            "streaming": False,
            "has_tools": bool(kwargs.get("tools")),
            "tool_choice": kwargs.get("tool_choice"),
            "message_count": len(kwargs.get("messages") or []),
        }

        last_exc = None
        with start_span("llm.call", initial_attrs) as span:
            start_ns = time.monotonic_ns()
            for attempt in range(MAX_LLM_RETRIES + 1):
                try:
                    response = await acompletion(**kwargs)
                    set_attrs(span, _llm_response_attrs(response, attempt))
                    set_attrs(span, {
                        "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    })
                    return response
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
                        set_attrs(span, {
                            "retry_count": attempt,
                            "latency_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                            "error_type": type(exc).__name__,
                        })
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
    ) -> Tuple[List[Tuple[str, Any]], List[str], List[str]]:
        """Query all RAG data sources in parallel, batching by server.

        Sources sharing the same server are sent as a single batched request
        (one HTTP call with multiple corpora) instead of N separate calls.
        Different servers are queried in parallel.

        A source the user is not allowed to query is dropped rather than
        failing the whole turn: one out-of-boundary selection must not discard
        results already retrieved from other server groups. The hard error is
        reserved for the case where *every* group was rejected, since there is
        then nothing to answer from and silently degrading to a non-RAG answer
        is exactly the failure mode this path exists to prevent.

        A source whose query failed with a non-permission error (e.g. the RAG
        service returned a 500) is also dropped rather than failing the whole
        turn, but -- unlike a permission denial -- the failure is *reported*
        to the caller via ``failures`` so the LLM can be told the query broke
        and instructed to tell the user, instead of answering as if nothing
        happened (GH #844).

        Args:
            data_sources: Qualified data source identifiers (server:source_id).
            rag_service: UnifiedRAGService instance.
            user_email: User email for access control.
            messages: Conversation messages for RAG context.

        Returns:
            ``(successful, exclusions, failures)`` -- ``successful`` is a list
            of (display_source, rag_response) tuples, one per surviving
            server batch; ``exclusions`` holds one user-facing message per
            permission-denied group, each naming the source and the remedy;
            ``failures`` holds one user-facing message per server batch that
            errored for any other reason, each naming the source so the LLM
            can tell the user which data sources could not be queried.

        Raises:
            DataSourcePermissionError: Every selected group was rejected.
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
        denials: List[DataSourcePermissionError] = []
        failures: List[str] = []
        for (server_name, sources), result in zip(server_groups.items(), results):
            if isinstance(result, Exception):
                if isinstance(result, DataSourcePermissionError):
                    denials.append(result)
                    continue
                logger.error("[RAG] Failed to query server %s: %s", server_name, result)
                # Report the failure to the LLM rather than dropping it
                # silently (GH #844). Name the corpus(es) the user selected so
                # they recognise the source; keep the reason generic so the
                # raw error text (which may carry internal details) never
                # reaches the model or the user.
                display_parts = [self._parse_qualified_data_source(s) for s in sources]
                display = ", ".join(display_parts)
                failures.append(
                    f"The data source '{display}' could not be queried "
                    f"(the RAG service returned an error or could not be reached)."
                )
            else:
                successful.append(result)

        if denials and not successful:
            # Nothing survived -- surface the denial instead of degrading to a
            # silent non-RAG answer.
            raise denials[0]

        exclusions = [str(denial) for denial in denials]
        if exclusions:
            logger.warning(
                "[RAG] Excluded %d of %d server group(s) the user may not query; "
                "answering from the remainder",
                len(exclusions),
                len(server_groups),
            )
        if failures:
            logger.warning(
                "[RAG] %d of %d server group(s) failed to query; reporting to LLM",
                len(failures),
                len(server_groups),
            )
        return successful, exclusions, failures

    @staticmethod
    def _build_rag_exclusion_notice(exclusions: List[str]) -> str:
        """Render dropped-source messages for inclusion in the RAG context block.

        The notice goes into the RAG system message rather than being appended
        to the finished answer so it reaches the user identically on the
        streaming and non-streaming paths.
        """
        if not exclusions:
            return ""
        bullets = "\n".join(f"- {message}" for message in exclusions)
        return (
            "\n\nThe following selected data sources were NOT searched and are "
            f"absent from the context above:\n{bullets}\n"
            "Open your reply by stating in one sentence that these sources were "
            "excluded, then answer from the context that is present."
        )

    @staticmethod
    def _build_rag_failure_notice(failures: List[str]) -> str:
        """Render failed-query messages for inclusion in the RAG context block.

        A *failure* (unlike an :func:`_build_rag_exclusion_notice` exclusion)
        means the RAG service returned an error, so no context was retrieved
        from that source at all. The notice rides in the RAG system message so
        it reaches the user identically on the streaming and non-streaming
        paths, and instructs the model to tell the user rather than answering
        as if nothing happened (GH #844).
        """
        if not failures:
            return ""
        bullets = "\n".join(f"- {message}" for message in failures)
        return (
            "\n\nThe following selected data sources could NOT be queried (the "
            "RAG service returned an error or could not be reached), and no "
            f"context was retrieved from them:\n"
            f"{bullets}\n"
            "You MUST tell the user in your first sentence that these data sources "
            "could not be reached. Then answer the user's question from any context "
            "that is present, or from your general knowledge if none is, but make "
            "clear that the RAG retrieval failed."
        )

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

    @staticmethod
    def _strip_customer_id_suffix(value: str, suffix: Optional[str]) -> str:
        """Strip a configured email-domain suffix from a customer-id value.

        Turns e.g. ``user@mydomain.com`` into ``user`` before it is sent as the
        ``x-litellm-customer-id`` header. The suffix is matched
        case-insensitively (email domains are case-insensitive). The value is
        returned unchanged when no suffix is configured, when it does not end
        with the suffix, or when stripping would leave an empty string.
        """
        if not suffix:
            return value
        if value.lower().endswith(suffix.lower()):
            stripped = value[: len(value) - len(suffix)]
            if stripped:
                return stripped
        return value

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
            # Always pass api_key to LiteLLM for all providers. LiteLLM accepts
            # this per-call key directly, so avoid mutating provider-specific
            # environment variables based on model or URL heuristics.
            kwargs["api_key"] = api_key

        # Set custom API base for non-standard endpoints
        if hasattr(model_config, 'model_url') and model_config.model_url:
            if not any(provider in model_config.model_url for provider in ["openrouter", "api.openai.com", "api.anthropic.com", "api.cerebras.ai"]):
                kwargs["api_base"] = model_config.model_url

        # Handle extra headers with environment variable expansion
        extra_headers_resolved: Dict[str, str] = {}
        if model_config.extra_headers:
            for header_key, header_value in model_config.extra_headers.items():
                try:
                    resolved_value = resolve_env_var(header_value)
                    extra_headers_resolved[header_key] = resolved_value
                except ValueError as e:
                    logger.error(f"Failed to resolve extra header '{header_key}' for model {model_name}: {e}")
                    raise

        # Optionally attribute the request to the logged-in user via the
        # LiteLLM customer-id header so a LiteLLM proxy can track per-user
        # (per-customer) spend/usage. Skipped when no user_email is available
        # (e.g. background/system calls) since the header is for tracking, not
        # authentication.
        if getattr(model_config, "pass_user_as_customer_id", False):
            # Explicit extra_headers are authoritative: if the operator has
            # already pinned a customer id (e.g. a static id for a service
            # account or for testing), leave it in place and do not overwrite
            # it. HTTP header names are case-insensitive, so compare accordingly.
            has_explicit_customer_id = any(
                key.lower() == "x-litellm-customer-id" for key in extra_headers_resolved
            )
            if has_explicit_customer_id:
                logger.debug(
                    "Model '%s' already sets x-litellm-customer-id via extra_headers; "
                    "keeping the configured value instead of the logged-in user.",
                    model_name,
                )
            elif user_email:
                customer_id = self._strip_customer_id_suffix(
                    user_email, getattr(model_config, "customer_id_strip_suffix", None)
                )
                extra_headers_resolved["x-litellm-customer-id"] = customer_id
            else:
                logger.debug(
                    "Model '%s' has pass_user_as_customer_id enabled but no user_email "
                    "was provided; skipping x-litellm-customer-id header.",
                    model_name,
                )

        if extra_headers_resolved:
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
    def _rag_insert_index(messages: List[Dict[str, Any]]) -> int:
        """Index at which the RAG context message can be safely inserted.

        The RAG context belongs just before the user turn it was retrieved for.
        Inserting at ``-1`` (before the last message) is only equivalent when the
        conversation ends on that user message. In a tool-calling continuation
        round the tail is ``assistant(tool_calls=[...]), tool, tool, ...``, and
        splitting that block makes OpenAI/Azure reject the request with
        "assistant message with 'tool_calls' must be followed by tool messages
        responding to each 'tool_call_id'".

        Returns the index of the last user message, or ``len(messages)`` when
        there is none (append at the end, which is never inside a tool block).
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                return i
        return len(messages)

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

            # PreLlmCall hook (GH #713): inspect/redact messages, swap/veto model,
            # or block the call. Zero-overhead when no hooks registered.
            mod = await self._fire_pre_llm_hook(model_name, messages, user_email=user_email)
            if mod is not None:
                model_name, messages, _ = await self._apply_pre_llm_modify(mod, model_name, messages, user_email=user_email)
                litellm_model = self._get_litellm_model_name(model_name)
                model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)
                if max_tokens is not None:
                    model_kwargs["max_tokens"] = max_tokens

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

        except HookBlockedError:
            # A PreLlmCall hook blocked the call: surface the reason, do not
            # wrap as a generic LLMServiceError.
            raise
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
            source_responses, rag_exclusions, rag_failures = await self._query_all_rag_sources(
                data_sources, rag_service, user_email, messages,
            )

            if not source_responses:
                if rag_failures:
                    # Every RAG query failed: tell the LLM (and thus the user)
                    # instead of silently answering as if nothing happened
                    # (GH #844). Inject a system message and call the LLM so
                    # the user gets a response that names the failure.
                    logger.warning(
                        "[LLM+RAG] All RAG sources failed (%d); informing LLM and user",
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
                    return await self.call_plain(
                        model_name, messages_with_rag, temperature=temperature, user_email=user_email
                    )
                logger.warning("[LLM+RAG] No RAG responses and no failures recorded; falling back to plain LLM call")
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
                    f"{self._build_rag_exclusion_notice(rag_exclusions)}"
                    f"{self._build_rag_failure_notice(rag_failures)}"
                    f"{citation_block}\n\n"
                    "Use this context to inform your response. "
                    "Cite sources inline using [1], [2], etc. where applicable."
                ),
            }
            messages_with_rag.insert(self._rag_insert_index(messages_with_rag), rag_context_message)

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

        except (
            # ``LLMError`` covers the whole family (rate limit, timeout, service,
            # bad request, context window, malformed tool call) so a newly added
            # member is never silently downgraded into the fallback retry below.
            # The other two sit outside that hierarchy and are listed explicitly.
            LLMError,
            LLMAuthenticationError,
            DataSourcePermissionError,
        ):
            raise  # Don't mask LLM errors with a fallback retry
        except HookBlockedError:
            # A PreLlmCall hook blocked the call: do NOT fall back to plain LLM
            # (the hook's decision applies to the LLM step regardless of RAG).
            raise
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

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"LLM call with tools: {len(messages)} messages, {total_chars} chars, {len(tools_schema)} tools")

            # PreLlmCall hook (GH #713)
            mod = await self._fire_pre_llm_hook(model_name, messages, user_email=user_email, tools_schema=tools_schema)
            if mod is not None:
                model_name, messages, tools_schema = await self._apply_pre_llm_modify(
                    mod, model_name, messages, tools_schema, user_email=user_email
                )
                litellm_model = self._get_litellm_model_name(model_name)
                model_kwargs = self._get_model_kwargs(model_name, temperature, user_email=user_email)

            response = await self._acompletion_with_retry(
                model=litellm_model,
                messages=self._prepare_messages(model_name, messages),
                tools=tools_schema,
                tool_choice=tool_choice,
                **model_kwargs
            )

            message = response.choices[0].message

            tool_calls = getattr(message, 'tool_calls', None)
            dropped_tool_calls: List[str] = []
            dropped_were_truncated = False
            # Same guard as the streaming path: a tool call whose arguments are
            # not parseable JSON (a model that hit its output limit mid-call)
            # must not be executed or written into the conversation, or every
            # later request in that conversation is rejected with a 400.
            if tool_calls:
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                tool_calls, malformed = partition_tool_calls_by_json_validity(
                    tool_calls, truncated=response_was_cut_off(finish_reason),
                )
                if malformed:
                    dropped_tool_calls = self._handle_malformed_tool_calls(
                        malformed,
                        kept=tool_calls,
                        finish_reason=finish_reason,
                        model_name=model_name,
                        user_email=user_email,
                    )
                    # Copy is keyed on the output limit; see the streaming path.
                    dropped_were_truncated = finish_reason == "length"
                # See the streaming path: an empty array is a shape providers
                # reject on the follow-up request.
                tool_calls = tool_calls or None
            tool_count = len(tool_calls) if tool_calls else 0
            log_metric("llm_call", user_email, model=model_name, message_count=len(messages), tool_count=tool_count)

            return LLMResponse(
                content=getattr(message, 'content', None) or "",
                tool_calls=tool_calls,
                model_used=model_name,
                dropped_tool_calls=dropped_tool_calls or None,
                dropped_tool_calls_truncated=dropped_were_truncated,
            )

        except HookBlockedError:
            raise
        except Exception as exc:
            logger.error("Error calling LLM with tools: %s", exc, exc_info=True)
            self._raise_llm_domain_error(exc, tools_schema=tools_schema)

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
            source_responses, rag_exclusions, rag_failures = await self._query_all_rag_sources(
                data_sources, rag_service, user_email, messages,
            )

            if not source_responses:
                if rag_failures:
                    # Every RAG query failed: tell the LLM (and thus the user)
                    # instead of silently answering as if nothing happened
                    # (GH #844). Inject a system message and call the LLM with
                    # tools so the user gets a response that names the failure.
                    logger.warning(
                        "[LLM+RAG+Tools] All RAG sources failed (%d); informing LLM and user",
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
                                "Answer the user's question (you may still use the available "
                                "tools), but begin by telling them you were unable to retrieve "
                                "any context from their selected data sources and that they may "
                                "need to retry or contact an administrator."
                            ),
                        },
                    )
                    return await self.call_with_tools(
                        model_name, messages_with_rag, tools_schema, tool_choice,
                        temperature=temperature, user_email=user_email,
                    )
                logger.warning("[LLM+RAG+Tools] No RAG responses and no failures recorded; falling back to tools-only call")
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
                    f"{self._build_rag_exclusion_notice(rag_exclusions)}"
                    f"{self._build_rag_failure_notice(rag_failures)}"
                    f"{citation_block}\n\n"
                    "Use this context to inform your response. "
                    "Cite sources inline using [1], [2], etc. where applicable."
                ),
            }
            messages_with_rag.insert(self._rag_insert_index(messages_with_rag), rag_context_message)

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

        except (
            # ``LLMError`` covers the whole family (rate limit, timeout, service,
            # bad request, context window, malformed tool call) so a newly added
            # member is never silently downgraded into the fallback retry below.
            # The other two sit outside that hierarchy and are listed explicitly.
            LLMError,
            LLMAuthenticationError,
            DataSourcePermissionError,
        ):
            raise  # Don't mask LLM errors with a fallback retry
        except HookBlockedError:
            raise
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
            (
                "When you use information from these sources, cite them inline using "
                + "bracketed numbers like [1], [2], etc. Place citations immediately after "
                + "the claim they support. You may cite multiple sources for the same "
                + "claim, e.g. [1][3]. Do not fabricate citations — only cite sources "
                + "listed below."
            ),
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
    def _sanitize_snippet(text: str, max_chars: int = 600) -> str:
        """Make a section snippet safe for inclusion as nested markdown.

        Strips control characters, neutralizes leading reference-list
        patterns that would otherwise confuse the frontend extractor
        (which scans for ``N.`` and ``<li>`` at the start of a line),
        and truncates to ``max_chars``.
        """
        if not text:
            return ""
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # The frontend reference extractor anchors on ``N.\s`` at the start
        # of a line; neutralize that pattern inside snippets so attacker-/
        # data-controlled text can't masquerade as a new reference entry.
        # We drop the trailing whitespace after the dot, so ``1. text``
        # becomes ``1.text`` — visible but no longer a reference anchor.
        cleaned = re.sub(r"(^|\n)\s*(\d{1,2})\.\s", r"\1\2.", cleaned)
        cleaned = cleaned.strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[: max_chars - 1].rstrip() + "…"
        return cleaned

    @staticmethod
    def _format_rag_references(metadata) -> str:
        """Format RAG metadata into a numbered references section.

        Produces a Perplexity-style references block that pairs with the
        inline [1], [2] citations the LLM was instructed to emit. When
        documents carry section snippets (newest ATLAS-RAG spec), the
        snippets are rendered as a nested ``rag-ref-snippets`` list under
        each reference so the frontend's expanded citation area shows
        the underlying evidence text.

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

            if doc.citation:
                safe_citation = LiteLLMCaller._sanitize_label(doc.citation[:500])
                if safe_citation:
                    lines.append(f"   *{safe_citation}*")

            for sec_idx, sec in enumerate(doc.sections, start=1):
                snippet = LiteLLMCaller._sanitize_snippet(sec.text)
                if not snippet:
                    continue
                snippet_relevance = int(sec.relevance * 100)
                # Render snippets as a blockquote with explicit class hook so
                # the frontend can style them inside the expanded references
                # <details> element. ``§N`` carries the section_ref through
                # to the UI without needing extra schema. v0.8.0 sections have
                # no section_ref, so fall back to a per-document sequential
                # index to avoid rendering ``§None``.
                sec_ref = sec.section_ref if sec.section_ref is not None else sec_idx
                snippet_oneline = snippet.replace("\n", " ")
                lines.append(
                    "   > "
                    f'<span class="rag-ref-snippet" data-section-ref="{sec_ref}">'
                    f"§{sec_ref} ({snippet_relevance}%): {snippet_oneline}"
                    "</span>"
                )

        lines.append(f"\n*{metadata.data_source_name} · {metadata.retrieval_method} · {metadata.query_processing_time_ms}ms*")
        return "\n".join(lines)

    def _format_rag_metadata(self, metadata) -> str:
        """Format RAG metadata — delegates to _format_rag_references.

        Kept for backward compatibility with call sites that check the return
        value against 'Metadata unavailable'.
        """
        result = self._format_rag_references(metadata)
        return result if result else "Metadata unavailable"
