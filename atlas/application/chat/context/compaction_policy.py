"""Compaction policy, triggers and shared helpers (issue #877)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from atlas.domain.errors import DomainError
from atlas.domain.messages.models import (
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    DISPLAY_ONLY_MESSAGE_TYPES,
    ConversationHistory,
    MessageRole,
)
from atlas.modules.llm.context_window import ContextWindow

TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"
TRIGGER_TURN_INTERVAL = "turn_interval"
TRIGGER_OVERFLOW = "overflow"

TRIGGER_LABELS = {
    TRIGGER_MANUAL: "manual",
    TRIGGER_AUTO: "automatic",
    TRIGGER_TURN_INTERVAL: "automatic, turn interval",
    TRIGGER_OVERFLOW: "automatic, after a context-length error",
}

USAGE_TOTALS_KEY = "token_usage_totals"
# Steps an agent run waits after one automatic compaction before the next, so
# a run whose newest step alone sits near the threshold does not summarize on
# every step. Overflow recovery is not subject to it.
MIN_STEPS_BETWEEN_AUTO_COMPACTIONS = 2

RequestBuilder = Callable[[], Awaitable[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]]


class NothingToCompact(Exception):
    """The context holds nothing older than the newest unit to summarize."""


# Why a summarizer call produced no checkpoint text (issue #1138).
# The model answered with no content at all -- an empty reply or a response
# with no visible text.
CHECKPOINT_FAILURE_EMPTY_CONTENT = "empty_content"
# The model spent the whole output-token budget before writing any final text,
# which litellm reports as finish_reason "length". A reasoning model can drain
# the budget on reasoning tokens alone.
CHECKPOINT_FAILURE_TOKEN_EXHAUSTION = "output_token_exhaustion"
# The model reasoned but wrote no final text. Reported as a flag only: the
# reasoning content is never read, logged or stored.
CHECKPOINT_FAILURE_REASONING_ONLY = "reasoning_only"
# The call itself failed (provider, transport, authorization). It arrives as a
# raised domain error, not as an empty checkpoint, and is never retried here.
CHECKPOINT_FAILURE_PROVIDER_ERROR = "provider_error"

CHECKPOINT_FAILURE_TEXT = {
    CHECKPOINT_FAILURE_EMPTY_CONTENT: "the summarization model returned no checkpoint text",
    CHECKPOINT_FAILURE_TOKEN_EXHAUSTION: (
        "the summarization model used its whole output-token budget before writing a checkpoint"
    ),
    CHECKPOINT_FAILURE_REASONING_ONLY: (
        "the summarization model returned reasoning but no checkpoint text"
    ),
    CHECKPOINT_FAILURE_PROVIDER_ERROR: "the summarization call failed",
}


class EmptyCheckpointError(DomainError):
    """The summarizer answered, but there was no checkpoint in the reply (#1138).

    Carries the classified ``reason`` and the non-sensitive response
    ``diagnostics`` (finish reason, token counts, content presence) so an
    operator can tell a blank reply from a drained output budget. Neither is
    ever shown raw: :func:`failure_text` renders one short line, and raw
    provider text is kept out of both.
    """

    def __init__(self, reason: str, diagnostics: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(CHECKPOINT_FAILURE_TEXT.get(reason, CHECKPOINT_FAILURE_TEXT[
            CHECKPOINT_FAILURE_EMPTY_CONTENT
        ]), code=reason)
        self.reason = reason
        self.diagnostics = dict(diagnostics or {})


@dataclass(frozen=True)
class CompactionPolicy:
    enabled: bool = True
    auto: bool = True
    threshold_percent: float = 80.0
    turn_interval: int = 0
    keep_recent_percent: float = 25.0
    max_retries: int = 2

    @classmethod
    def from_settings(cls, settings: Any, auto: Optional[bool] = None) -> "CompactionPolicy":
        def read(name: str, default: Any) -> Any:
            value = getattr(settings, name, default) if settings is not None else default
            return default if value is None else value

        enabled = bool(read("feature_context_compaction_enabled", True))
        auto_default = bool(read("context_auto_compact_default", True))
        return cls(
            enabled=enabled,
            auto=enabled and (auto_default if auto is None else bool(auto)),
            threshold_percent=float(read("context_compaction_threshold_percent", 80.0)),
            turn_interval=int(read("context_compaction_turn_interval", 0)),
            keep_recent_percent=float(read("context_compaction_keep_recent_percent", 25.0)),
            max_retries=int(read("context_compaction_max_retries", 2)),
        )

    def threshold_tokens(self, window: ContextWindow) -> int:
        return max(1, int(window.usable * self.threshold_percent / 100))

    def keep_tokens(self, window: ContextWindow) -> int:
        return int(window.usable * self.keep_recent_percent / 100)


def _fmt_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}K".replace(".0K", "K")
    return str(tokens)


def checkpoint_row_content(trigger: str, before: int, after: int) -> str:
    label = TRIGGER_LABELS.get(trigger, trigger)
    return f"Context compacted ({label}): ~{_fmt_tokens(before)} -> ~{_fmt_tokens(after)} tokens"


def count_checkpoints(history: Optional[ConversationHistory]) -> int:
    if history is None:
        return 0
    return sum(
        1 for m in history.messages
        if m.metadata.get("message_type") == CONTEXT_CHECKPOINT_MESSAGE_TYPE
    )


def user_turns_since_checkpoint(history: ConversationHistory) -> int:
    covered, _ = history.active_checkpoint()
    return sum(
        1 for i, m in enumerate(history.messages)
        if i > covered and m.role == MessageRole.USER
        and m.metadata.get("message_type") not in DISPLAY_ONLY_MESSAGE_TYPES
    )


def failure_text(exc: Exception) -> str:
    """The user-facing reason. Only a domain error's own message is shown:
    raw provider or transport text can name internal hosts and URLs."""
    from atlas.domain.errors import DomainError

    if isinstance(exc, DomainError):
        message = getattr(exc, "message", None) or str(exc)
        if message:
            return f"Context compaction failed: {str(message)[:300]}"
    return "Context compaction failed: the model could not produce a checkpoint."


def failure_frame(base: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
    """The ``failed`` frame for one compaction error.

    Adds the classified ``reason`` for an empty checkpoint; the response
    diagnostics stay in the log, because the frame reaches the browser.
    """
    frame = {**base, "status": "failed", "error": failure_text(exc)}
    if isinstance(exc, EmptyCheckpointError):
        frame["reason"] = exc.reason
    return frame
