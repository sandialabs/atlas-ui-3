"""Compaction policy, triggers and shared helpers (issue #877)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

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
