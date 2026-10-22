"""Provider-reported token usage for the LLM calls of one chat turn (issue #877).

A turn opens a :class:`UsageSink` with :func:`usage_scope`; every LLM call made
inside it (the turn's own calls, agent steps, compaction summaries) adds what
the provider reported. Calls outside a scope record nothing, and a provider
that reports no usage simply leaves the sink empty -- the UI then shows the
estimate alone, labelled as such.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Optional


@dataclass
class UsageSink:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    # The prompt size of the most recent reported call: the provider's own
    # count of the latest request, the best check on the running estimate.
    last_prompt_tokens: Optional[int] = None

    def add(self, prompt_tokens: Any, completion_tokens: Any) -> None:
        prompt = prompt_tokens if isinstance(prompt_tokens, int) and prompt_tokens >= 0 else None
        completion = completion_tokens if isinstance(completion_tokens, int) and completion_tokens >= 0 else None
        if prompt is None and completion is None:
            return
        self.calls += 1
        if prompt is not None:
            self.prompt_tokens += prompt
            self.last_prompt_tokens = prompt
        if completion is not None:
            self.completion_tokens += completion


_current_sink: ContextVar[Optional[UsageSink]] = ContextVar("atlas_llm_usage_sink", default=None)


@contextmanager
def usage_scope(sink: Optional[UsageSink] = None) -> Iterator[UsageSink]:
    sink = sink or UsageSink()
    token = _current_sink.set(sink)
    try:
        yield sink
    finally:
        _current_sink.reset(token)


def record_usage(usage: Any) -> None:
    """Add one provider ``usage`` block (object or dict) to the active sink."""
    sink = _current_sink.get()
    if sink is None or usage is None:
        return
    if isinstance(usage, dict):
        sink.add(usage.get("prompt_tokens"), usage.get("completion_tokens"))
    else:
        sink.add(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


def record_response_usage(response: Any) -> None:
    record_usage(getattr(response, "usage", None))
