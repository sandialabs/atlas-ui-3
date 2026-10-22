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

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from atlas.domain.messages.models import (
    AGENT_TOOL_DIGEST_KEY,
    CHECKPOINT_SCOPE_CONVERSATION,
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    DISPLAY_ONLY_MESSAGE_TYPES,
    ConversationHistory,
    Message,
    MessageRole,
)
from atlas.modules.llm.context_window import ContextWindow, resolve_context_window
from atlas.modules.llm.token_estimation import TokenEstimate, estimate_request_tokens_async
from atlas.modules.llm.usage_tracking import UsageSink

from .compaction_engine import approx_tokens, summarize_messages
from .compaction_policy import (
    TRIGGER_AUTO,
    TRIGGER_TURN_INTERVAL,
    USAGE_TOTALS_KEY,
    CompactionPolicy,
    NothingToCompact,
    RequestBuilder,
    checkpoint_row_content,
    count_checkpoints,
    failure_text,
    user_turns_since_checkpoint,
)
from .run_compaction_guard import RunCompactionGuard

logger = logging.getLogger(__name__)


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

    async def send_quietly(self, payload: Dict[str, Any]) -> None:
        """Best-effort send from a cancellation path, which re-raises next."""
        try:
            await asyncio.wait_for(self._send(payload), timeout=1.0)
        except (Exception, asyncio.CancelledError):
            logger.debug("Could not deliver a context event while cancelling", exc_info=True)

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
        estimate = await estimate_request_tokens_async(messages, tools_schema, model)
        payload = self.usage_payload(
            model=model, estimate=estimate, policy=policy, history=history, sink=sink, totals=totals,
        )
        # Lets the client drop an estimate for a conversation it has left.
        conversation_id = (session_context or {}).get("conversation_id")
        if conversation_id:
            payload["conversation_id"] = str(conversation_id)
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
            before = await estimate_request_tokens_async(messages, tools, model)
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
            after = await estimate_request_tokens_async(messages, tools, model)
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
            failed = {**base, "status": "failed", "error": failure_text(exc)}
            await self._send(failed)
            return failed
        except BaseException:
            # Cancelled mid-compaction (Stop, disconnect, reset): the cancel
            # path saves the session, and a half-built checkpoint row with no
            # summary checked must never be saved with it. The client is told
            # the compaction ended, if it is still there to hear it.
            self._discard(history, added)
            await self.send_quietly({**base, "status": "failed", "silent": True})
            raise

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
