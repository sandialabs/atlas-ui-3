"""Asking the model for a checkpoint, and reading a blank answer (#1138).

``summarize_messages`` (in ``compaction_engine``) decides *what* is sent: the
transcript is rendered, split into bounded chunks, and folded. This module
owns the part that talks to the model and has to cope with the answer being
empty, because that is a normal thing for a provider to do:

* a model that writes no text at all;
* a reasoning model that spends its whole output budget on reasoning before
  it emits any final text (litellm reports ``finish_reason: "length"``);
* a reply that carries reasoning but no final text.

None of those are a request error, and none of them should take the
conversation down: a compaction that cannot summarize leaves the context it
was given untouched and lets the run continue uncompacted.

What is collected about a blank reply is deliberately narrow -- counts,
structure flags and an allow-listed finish reason. No content and no reasoning
text is ever read or logged, so a transcript cannot leak into the log through
a diagnostic.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional, Tuple

from atlas.modules.config.models import lookup_model_config

from .compaction_policy import (
    CHECKPOINT_FAILURE_EMPTY_CONTENT,
    CHECKPOINT_FAILURE_PROVIDER_ERROR,
    CHECKPOINT_FAILURE_REASONING_ONLY,
    CHECKPOINT_FAILURE_TOKEN_EXHAUSTION,
    EmptyCheckpointError,
)

logger = logging.getLogger(__name__)

# Output-token cap for the first summarizer call.
SUMMARY_MAX_TOKENS = 3000
# A reasoning model spends output tokens on reasoning before it writes any
# final text, so a cap that is comfortable for a chat model can come back with
# finish_reason "length" and no checkpoint at all. Models configured with a
# reasoning_effort -- the codebase's marker for a reasoning model -- start
# with the larger budget.
SUMMARY_REASONING_MAX_TOKENS = 6000
# What the single retry asks for. Reasoning is the usual reason the first
# attempt came back blank, so more room is the one change that can help.
SUMMARY_RETRY_MAX_TOKENS = 8000
# An attempt never asks for more than this share of the model's usable
# context: the summarizer's own prompt -- instructions, the previous
# checkpoint and the chunk -- has to fit beside the reply, and an endpoint
# that enforces the combined limit would otherwise reject the larger
# budgets outright instead of letting the retry help.
SUMMARY_FIRST_OUTPUT_SHARE = 0.35
SUMMARY_RETRY_OUTPUT_SHARE = 0.5
SUMMARY_MIN_OUTPUT_TOKENS = 800
# Two calls per chunk is the whole recovery budget: one attempt, one retry
# with more headroom, never a loop.
MAX_SUMMARY_ATTEMPTS = 2
SUMMARY_TEMPERATURE = 0.2
# The retry is deterministic: sampling a second empty reply from the same
# distribution is not recovery.
SUMMARY_RETRY_TEMPERATURE = 0.0


def summarizer_model_config(llm: Any, model: str) -> Optional[Any]:
    """The summarizer model's own config, when the LLM object carries one.

    Only used to size the output budget for a reasoning model; an LLM that
    exposes no config (a test double, or another implementation) gets the plain
    budget. Never raises: a missing config costs an early retry, an exception
    here would cost the compaction.
    """
    config = getattr(llm, "llm_config", None)
    if config is None:
        return None
    try:
        return lookup_model_config(config, model)
    except Exception:
        logger.debug("Could not read the summarizer model config", exc_info=True)
    return None


def is_reasoning_model(model_config: Any) -> bool:
    """Whether a model is configured as a reasoning model (issue #1138).

    ``reasoning_effort`` is the marker this codebase has: it is validated when
    llmconfig loads and sent on every request to that model (GH #756). A model
    without one keeps today's budget, so no working configuration changes.
    """
    return bool(getattr(model_config, "reasoning_effort", None))


def checkpoint_output_budget(
    *, reasoning: bool = False, attempt: int = 0, input_budget_tokens: int = 0,
) -> int:
    """Output-token cap for one summarizer call (issue #1138).

    Reasoning models get a larger nominal budget from the first attempt; the
    retry gets a larger one either way. With a known window, the result is then
    clamped to the share of it that is left for a reply, so a small window
    shrinks both attempts instead of asking for output that cannot fit beside
    its own prompt.
    """
    if attempt <= 0:
        nominal = SUMMARY_REASONING_MAX_TOKENS if reasoning else SUMMARY_MAX_TOKENS
        share = SUMMARY_FIRST_OUTPUT_SHARE
    else:
        nominal = SUMMARY_RETRY_MAX_TOKENS
        share = SUMMARY_RETRY_OUTPUT_SHARE
    if input_budget_tokens > 0:
        return max(1, min(nominal, int(input_budget_tokens * share)))
    return max(SUMMARY_MIN_OUTPUT_TOKENS, nominal)


def classify_empty_response(diagnostics: Optional[Dict[str, Any]]) -> str:
    """Why a summarizer response carried no checkpoint text (issue #1138).

    Reads only the non-sensitive metadata the caller reports: an exhausted
    output budget is the one cause a retry can fix, so it is the one that gets
    its own reason.
    """
    details = diagnostics or {}
    if details.get("finish_reason") == "length":
        return CHECKPOINT_FAILURE_TOKEN_EXHAUSTION
    if details.get("has_reasoning_content"):
        return CHECKPOINT_FAILURE_REASONING_ONLY
    return CHECKPOINT_FAILURE_EMPTY_CONTENT


async def _call_summarizer(
    llm: Any,
    model: str,
    request: List[Dict[str, Any]],
    *,
    user_email: Optional[str],
    temperature: float,
    max_tokens: int,
) -> Tuple[str, Dict[str, Any]]:
    """One summarizer request and the metadata that explains a blank reply.

    ``call_plain_detailed`` is optional on the LLM interface -- only the
    LiteLLM caller implements it -- so a plain ``call_plain`` is the fallback,
    with no diagnostics to report. The probe is deliberately defensive rather
    than typed: a test double can hand back a plain mock for the detailed
    method, and a mock result is not awaitable, which is exactly when the plain
    path is the right one.
    """
    detailed = getattr(llm, "call_plain_detailed", None)
    if callable(detailed):
        pending = detailed(
            model, request, temperature=temperature, max_tokens=max_tokens, user_email=user_email,
        )
        if inspect.isawaitable(pending):
            completion = await pending
            text = getattr(completion, "text", None)
            if text is None and isinstance(completion, str):
                text = completion
            return (text or "").strip(), dict(getattr(completion, "diagnostics", None) or {})
    text = await llm.call_plain(
        model, request, temperature=temperature, max_tokens=max_tokens, user_email=user_email,
    )
    return (text or "").strip(), {}


async def summarize_chunk(
    llm: Any,
    model: str,
    request: List[Dict[str, Any]],
    *,
    user_email: Optional[str],
    reasoning: bool,
    position: int,
    total: int,
    input_budget_tokens: int,
) -> str:
    """One chunk's summary: a single call, retried once, or the reason it failed.

    A model that answers with no text and no reasoning is usually going to do
    the same thing again, so recovery is one bounded attempt -- never a loop --
    with a larger output budget (bounded by the model's window) and
    deterministic sampling. A provider error is an exception, not an empty
    reply, so it propagates: the retry never swallows the error class callers
    depend on.

    Raises :class:`EmptyCheckpointError` -- with the classified cause and the
    response diagnostics -- when neither attempt produced text.
    """
    reason = CHECKPOINT_FAILURE_EMPTY_CONTENT
    diagnostics: Dict[str, Any] = {}
    for attempt in range(MAX_SUMMARY_ATTEMPTS):
        try:
            text, response_meta = await _call_summarizer(
                llm, model, request, user_email=user_email,
                max_tokens=checkpoint_output_budget(
                    reasoning=reasoning, attempt=attempt, input_budget_tokens=input_budget_tokens,
                ),
                temperature=SUMMARY_RETRY_TEMPERATURE if attempt else SUMMARY_TEMPERATURE,
            )
        except Exception:
            # The third cause: the provider, the transport or authorization
            # refused the call. Nothing here can fix it, so it goes on as the
            # domain error it already is; the caller's own log line reports
            # it, and the failed frame says why the compaction ended.
            logger.warning(
                "Checkpoint summarization call failed (chunk %d of %d, reason=%s)",
                position, total, CHECKPOINT_FAILURE_PROVIDER_ERROR,
            )
            raise
        if text:
            return text
        diagnostics = response_meta
        reason = classify_empty_response(diagnostics)
        # Only the coordinates: the response metadata is already logged once by
        # the plain-call warning that produced this empty completion.
        logger.warning(
            "Empty checkpoint text (chunk %d of %d, attempt %d of %d)",
            position, total, attempt + 1, MAX_SUMMARY_ATTEMPTS,
        )
    raise EmptyCheckpointError(reason, diagnostics)
