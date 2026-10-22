"""Keeps one agent run's working transcript inside the context window (#877).

Compaction runs at a step boundary -- after every tool call of the previous
step has its result -- and the run continues with its next LLM call, so no
new user message is needed. A context-length error is recovered by a harder
compaction and a bounded retry of the request; tools are never re-executed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas.domain.messages.models import (
    CHECKPOINT_SCOPE_RUN,
    CONTEXT_CHECKPOINT_KEY,
    CONTEXT_CHECKPOINT_MESSAGE_TYPE,
    ConversationHistory,
    Message,
    MessageRole,
    format_checkpoint_block,
)
from atlas.modules.llm.token_estimation import estimate_request_tokens

from .compaction_engine import (
    build_checkpoint_text,
    executed_tool_ledger,
    find_tail_start,
    latest_user_request,
    leading_system_count,
    prune_tool_outputs,
    summarize_messages,
)
from .compaction_policy import (
    MIN_STEPS_BETWEEN_AUTO_COMPACTIONS,
    TRIGGER_AUTO,
    TRIGGER_OVERFLOW,
    CompactionPolicy,
    NothingToCompact,
    checkpoint_row_content,
    failure_text,
)

logger = logging.getLogger(__name__)


class RunCompactionGuard:
    """Keeps one agent run's working transcript inside the context window."""

    def __init__(
        self,
        compactor: Any,
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
            failed = {**base, "status": "failed", "error": failure_text(exc)}
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
