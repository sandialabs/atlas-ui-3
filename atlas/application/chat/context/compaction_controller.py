"""Context tracking and compaction for chat turns and agent runs (issue #877).

One implementation serves every trigger:

* **manual** -- the user pressed Compact (or typed ``/compact``) between turns;
* **auto** -- before a turn, the estimated next request crossed the
  configured share of the model's usable context;
* **turn_interval** -- the optional every-N-turns trigger;
* **overflow** -- the provider rejected a request as too long, and the agent
  compacts harder and retries a bounded number of times.

Between turns, compaction writes a conversation checkpoint into history (a
``context_checkpoint`` row; see ``ConversationHistory.active_checkpoint``).
Inside an agent run, it compacts the run's working message list in place at a
step boundary -- after every tool call of the previous step has its result --
and the run continues with its next LLM call, with no new user message. No
original message is ever removed or rewritten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    CHECKPOINT_SCOPE_CONVERSATION,
    CHECKPOINT_SCOPE_RUN,
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    DISPLAY_ONLY_MESSAGE_TYPES,
    ConversationHistory,
    Message,
    MessageRole,
    format_checkpoint_block,
)
from atlas.modules.llm.context_window import ContextWindow, resolve_context_window
from atlas.modules.llm.token_estimation import TokenEstimate, estimate_request_tokens
from atlas.modules.llm.usage_tracking import UsageSink

from .compaction_engine import (
    approx_tokens,
    build_checkpoint_text,
    executed_tool_ledger,
    find_tail_start,
    latest_user_request,
    leading_system_count,
    prune_tool_outputs,
    summarize_messages,
)

logger = logging.getLogger(__name__)

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


def coerce_auto_flag(value: Any) -> Optional[bool]:
    """Read the client's per-conversation toggle; anything but a bool is unset."""
    return value if isinstance(value, bool) else None


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


class ContextCompactor:
    """Shared entry point: usage reports, history checkpoints, run guards."""

    def __init__(self, *, llm: Any, config_manager: Any, event_publisher: Any) -> None:
        self.llm = llm
        self.config_manager = config_manager
        self.event_publisher = event_publisher

    # -- configuration -------------------------------------------------
    @property
    def app_settings(self) -> Any:
        return getattr(self.config_manager, "app_settings", None)

    def policy(self, auto: Optional[bool] = None) -> CompactionPolicy:
        return CompactionPolicy.from_settings(self.app_settings, auto)

    def window(self, model: str) -> ContextWindow:
        llm_config = getattr(self.config_manager, "llm_config", None)
        return resolve_context_window(llm_config, model, self.app_settings)

    # -- events --------------------------------------------------------
    async def _send(self, payload: Dict[str, Any]) -> None:
        send = getattr(self.event_publisher, "send_json", None)
        if send is None:
            return
        try:
            await send(payload)
        except Exception:  # a dead transport must not fail compaction
            logger.debug("Could not deliver a context event", exc_info=True)

    def usage_payload(
        self,
        *,
        model: str,
        estimate: TokenEstimate,
        policy: CompactionPolicy,
        history: Optional[ConversationHistory] = None,
        sink: Optional[UsageSink] = None,
        totals: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        window = self.window(model)
        return {
            "type": "context_usage",
            "model": model,
            "estimated_tokens": estimate.tokens,
            "estimate_method": estimate.method,
            "context_window": window.total,
            "window_source": window.source,
            "output_reserve": window.output_reserve,
            "usable_tokens": window.usable,
            "threshold_tokens": policy.threshold_tokens(window),
            "compaction_enabled": policy.enabled,
            "auto_compact": policy.auto,
            "checkpoints": count_checkpoints(history),
            "provider_last_prompt_tokens": sink.last_prompt_tokens if sink else None,
            "cumulative_prompt_tokens": (totals or {}).get("prompt_tokens", 0),
            "cumulative_completion_tokens": (totals or {}).get("completion_tokens", 0),
            "reported_calls": (totals or {}).get("calls", 0),
        }

    async def publish_usage(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        policy: CompactionPolicy,
        history: Optional[ConversationHistory] = None,
        sink: Optional[UsageSink] = None,
        session_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Estimate the next request and tell the client. Returns the payload."""
        totals = None
        if session_context is not None:
            totals = dict(session_context.get(USAGE_TOTALS_KEY) or {})
            if sink is not None and sink.calls:
                totals["prompt_tokens"] = int(totals.get("prompt_tokens", 0)) + sink.prompt_tokens
                totals["completion_tokens"] = int(totals.get("completion_tokens", 0)) + sink.completion_tokens
                totals["calls"] = int(totals.get("calls", 0)) + sink.calls
                session_context[USAGE_TOTALS_KEY] = totals
        estimate = estimate_request_tokens(messages, tools_schema, model)
        payload = self.usage_payload(
            model=model, estimate=estimate, policy=policy, history=history, sink=sink, totals=totals,
        )
        await self._send(payload)
        return payload

    # -- conversation checkpoints --------------------------------------
    def should_compact_before_turn(
        self, policy: CompactionPolicy, window: ContextWindow, estimate: TokenEstimate,
        history: ConversationHistory,
    ) -> Optional[str]:
        """Return the trigger that applies before a turn, or None."""
        if not policy.auto:
            return None
        if estimate.tokens >= policy.threshold_tokens(window):
            return TRIGGER_AUTO
        if policy.turn_interval and user_turns_since_checkpoint(history) > policy.turn_interval:
            return TRIGGER_TURN_INTERVAL
        return None

    async def compact_history(
        self,
        *,
        history: ConversationHistory,
        model: str,
        user_email: Optional[str],
        build_request: RequestBuilder,
        trigger: str,
        focus: Optional[str] = None,
        keep_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Write a conversation checkpoint covering all but the recent turns.

        Publishes ``context_compaction`` started/completed/skipped/failed
        frames and returns the final one. Raises nothing: a failed compaction
        leaves history as it was and the caller carries on uncompacted.
        """
        window = self.window(model)
        policy = self.policy()
        if keep_tokens is None:
            keep_tokens = policy.keep_tokens(window)
        base = {"type": "context_compaction", "scope": CHECKPOINT_SCOPE_CONVERSATION, "trigger": trigger}
        await self._send({**base, "status": "started"})
        added: Optional[Message] = None
        try:
            messages, tools = await build_request()
            before = estimate_request_tokens(messages, tools, model)
            through_id, segment, previous = self._history_segment(history, keep_tokens)
            summary = await summarize_messages(
                self.llm, model, segment,
                user_email=user_email,
                input_budget_tokens=window.usable,
                previous_summary=previous,
                focus=focus,
            )
            record = {
                "scope": CHECKPOINT_SCOPE_CONVERSATION,
                "summary": summary,
                "through_message_id": through_id,
                "trigger": trigger,
                "focus": (focus or "").strip()[:1000] or None,
                "model": model,
                "summarized_messages": len(segment),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "before_tokens": before.tokens,
            }
            added = Message(
                role=MessageRole.SYSTEM,
                content="",
                metadata={"message_type": CONTEXT_CHECKPOINT_MESSAGE_TYPE, CONTEXT_CHECKPOINT_KEY: record},
            )
            history.add_message(added)
            record["through_offset"] = len(history.messages) - 1 - _index_of(history, through_id)
            messages, tools = await build_request()
            after = estimate_request_tokens(messages, tools, model)
            if after.tokens >= before.tokens:
                raise NothingToCompact("the checkpoint would not make the context any smaller")
            record["after_tokens"] = after.tokens
            record["estimate_method"] = after.method
            added.content = checkpoint_row_content(trigger, before.tokens, after.tokens)
            done = {
                **base,
                "status": "completed",
                "checkpoint_id": str(added.id),
                "content": added.content,
                "summary": summary,
                "before_tokens": before.tokens,
                "after_tokens": after.tokens,
                "summarized_messages": len(segment),
            }
            await self._send(done)
            return done
        except NothingToCompact as reason:
            self._discard(history, added)
            skipped = {**base, "status": "skipped", "reason": f"Nothing to compact: {reason}."}
            await self._send(skipped)
            return skipped
        except Exception as exc:
            self._discard(history, added)
            logger.warning("Context compaction failed (%s)", type(exc).__name__, exc_info=True)
            failed = {**base, "status": "failed", "error": _failure_text(exc)}
            await self._send(failed)
            return failed

    @staticmethod
    def _discard(history: ConversationHistory, added: Optional[Message]) -> None:
        # Only ever the row this call added itself; original messages stay.
        if added is not None and added in history.messages:
            history.messages.remove(added)

    @staticmethod
    def _history_segment(
        history: ConversationHistory, keep_tokens: int,
    ) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
        """Pick the stretch to summarize: model-visible messages after the
        current checkpoint, up to (not including) the oldest user turn the
        keep budget allows. The newest user turn is always kept."""
        covered, record = history.active_checkpoint_record()
        previous = record.get("summary") if record else None
        rows = history.messages
        visible = [
            i for i in range(covered + 1, len(rows))
            if rows[i].metadata.get("message_type") not in DISPLAY_ONLY_MESSAGE_TYPES
        ]
        as_dicts = {i: _llm_view(rows[i]) for i in visible}
        user_positions = [p for p, i in enumerate(visible) if rows[i].role == MessageRole.USER]
        if not user_positions:
            raise NothingToCompact("the conversation has no completed turn yet")
        # Walk back whole user turns while they fit the keep budget; the
        # newest turn is kept however large it is.
        costs = [approx_tokens(as_dicts[i]) for i in visible]
        chosen = user_positions[-1]
        running = sum(costs[chosen:])
        for position in reversed(user_positions[:-1]):
            cost = sum(costs[position:chosen])
            if running + cost > keep_tokens:
                break
            running += cost
            chosen = position
        tail_index = visible[chosen]
        segment = [as_dicts[i] for i in visible if i < tail_index]
        if not segment:
            raise NothingToCompact("only the latest turn is in context")
        return str(rows[tail_index - 1].id), segment, previous

    # -- agent runs ----------------------------------------------------
    def run_guard(
        self,
        *,
        model: str,
        user_email: Optional[str],
        history: Optional[ConversationHistory],
        policy: CompactionPolicy,
    ) -> "RunCompactionGuard":
        return RunCompactionGuard(self, model=model, user_email=user_email, history=history, policy=policy)


def _index_of(history: ConversationHistory, message_id: str) -> int:
    for index, msg in enumerate(history.messages):
        if str(msg.id) == message_id:
            return index
    raise ValueError("checkpoint boundary is not in history")


def _llm_view(message: Message) -> Dict[str, Any]:
    content = message.content or ""
    digest = message.metadata.get(AGENT_TOOL_DIGEST_KEY)
    if digest:
        content = f"{content}\n\n{digest}" if content else digest
    return {"role": message.role.value, "content": content}


def _failure_text(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return f"Context compaction failed: {str(message)[:300]}"


class RunCompactionGuard:
    """Keeps one agent run's working transcript inside the context window."""

    def __init__(
        self,
        compactor: ContextCompactor,
        *,
        model: str,
        user_email: Optional[str],
        history: Optional[ConversationHistory],
        policy: CompactionPolicy,
    ) -> None:
        self.compactor = compactor
        self.model = model
        self.user_email = user_email
        self.history = history
        self.policy = policy
        self.window = compactor.window(model)
        self._step = 0
        self._last_auto_step: Optional[int] = None

    @property
    def threshold(self) -> int:
        return self.policy.threshold_tokens(self.window)

    async def before_llm_call(
        self, messages: List[Dict[str, Any]], tools_schema: Optional[List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """Compact at a step boundary when the next request is over threshold."""
        self._step += 1
        if not self.policy.auto:
            return None
        if (
            self._last_auto_step is not None
            and self._step - self._last_auto_step < MIN_STEPS_BETWEEN_AUTO_COMPACTIONS
        ):
            return None
        estimate = estimate_request_tokens(messages, tools_schema, self.model)
        if estimate.tokens < self.threshold:
            return None
        self._last_auto_step = self._step
        return await self._compact(
            messages, tools_schema, trigger=TRIGGER_AUTO,
            keep_tokens=self.policy.keep_tokens(self.window), force_summary=False,
        )

    async def recover_from_overflow(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        attempt: int,
    ) -> bool:
        """Compact harder after a context-length error; True means retry.

        Bounded by ``max_retries`` and only with auto-compaction on: with it
        off the user chose to compact by hand, and the error is surfaced.
        """
        if not self.policy.auto or attempt >= self.policy.max_retries:
            return False
        keep = int(self.policy.keep_tokens(self.window) * (0.5 ** (attempt + 1)))
        outcome = await self._compact(
            messages, tools_schema, trigger=TRIGGER_OVERFLOW, keep_tokens=keep, force_summary=True,
        )
        return bool(outcome and outcome.get("status") == "completed")

    async def _compact(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        *,
        trigger: str,
        keep_tokens: int,
        force_summary: bool,
    ) -> Dict[str, Any]:
        base = {"type": "context_compaction", "scope": CHECKPOINT_SCOPE_RUN, "trigger": trigger}
        await self.compactor._send({**base, "status": "started"})
        snapshot = list(messages)
        try:
            before = estimate_request_tokens(messages, tools_schema, self.model)
            floor = leading_system_count(messages)
            tail_start = find_tail_start(messages, keep_tokens, floor)
            pruned = prune_tool_outputs(messages, tail_start)
            summary = None
            middle = estimate_request_tokens(messages, tools_schema, self.model)
            if force_summary or middle.tokens >= self.threshold:
                summary = await self._summarize_into(messages, floor, tail_start)
            after = estimate_request_tokens(messages, tools_schema, self.model)
            if after.tokens >= before.tokens:
                messages[:] = snapshot
                raise NothingToCompact("the agent's context holds nothing older than its latest step")
            content = checkpoint_row_content(trigger, before.tokens, after.tokens)
            checkpoint_id = self._record(trigger, content, summary, before.tokens, after.tokens, pruned)
            done = {
                **base,
                "status": "completed",
                "checkpoint_id": checkpoint_id,
                "content": content,
                "summary": summary,
                "pruned_tool_outputs": pruned,
                "before_tokens": before.tokens,
                "after_tokens": after.tokens,
            }
            await self.compactor._send(done)
            await self.compactor.publish_usage(
                model=self.model, messages=messages, tools_schema=tools_schema,
                policy=self.policy, history=self.history,
            )
            return done
        except NothingToCompact as reason:
            skipped = {**base, "status": "skipped", "reason": f"Nothing to compact: {reason}."}
            await self.compactor._send(skipped)
            return skipped
        except Exception as exc:
            # Leave the run exactly as it was; it continues uncompacted and,
            # if the request really is too long, the provider's error ends it.
            messages[:] = snapshot
            logger.warning("Agent context compaction failed (%s)", type(exc).__name__, exc_info=True)
            failed = {**base, "status": "failed", "error": _failure_text(exc)}
            await self.compactor._send(failed)
            return failed

    async def _summarize_into(self, messages: List[Dict[str, Any]], floor: int, tail_start: int) -> Optional[str]:
        stretch = messages[floor:tail_start]
        kept_system = [m for m in stretch if m.get("role") == "system"]
        segment = [m for m in stretch if m.get("role") != "system"]
        if not segment:
            return None
        tail = messages[tail_start:]
        summary = await summarize_messages(
            self.compactor.llm, self.model, segment,
            user_email=self.user_email,
            input_budget_tokens=self.window.usable,
        )
        tail_has_request = any(m.get("role") == "user" for m in tail)
        text = build_checkpoint_text(
            summary,
            executed_tool_ledger(segment),
            None if tail_has_request else latest_user_request(segment),
        )
        block = format_checkpoint_block(text)
        if tail and tail[0].get("role") == "user" and isinstance(tail[0].get("content"), str):
            tail = [{**tail[0], "content": f"{block}\n\n{tail[0]['content']}"}] + tail[1:]
        else:
            tail = [{"role": "user", "content": f"{block}\n\nContinue the current task from the state above."}] + tail
        messages[:] = messages[:floor] + kept_system + tail
        return text

    def _record(
        self, trigger: str, content: str, summary: Optional[str], before: int, after: int, pruned: int,
    ) -> Optional[str]:
        """Persist a run checkpoint as a display-only record in history."""
        if self.history is None:
            return None
        row = Message(
            role=MessageRole.SYSTEM,
            content=content,
            metadata={
                "message_type": CONTEXT_CHECKPOINT_MESSAGE_TYPE,
                CONTEXT_CHECKPOINT_KEY: {
                    "scope": CHECKPOINT_SCOPE_RUN,
                    "summary": summary,
                    "trigger": trigger,
                    "model": self.model,
                    "before_tokens": before,
                    "after_tokens": after,
                    "pruned_tool_outputs": pruned,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        )
        self.history.add_message(row)
        return str(row.id)
