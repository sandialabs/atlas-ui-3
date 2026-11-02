# Empty Checkpoint Recovery

Date: 2026-11-01

Issue: [#1138](https://github.com/sandialabs/atlas-ui-3/issues/1138) — context
compaction fails when the summarization LLM returns empty or `null` checkpoint
text, with no way to tell why.

Related: [#877](https://github.com/sandialabs/atlas-ui-3/issues/877) — context
compaction implementation.

## The failure

A user hit this trace, with nothing else in the log:

```text
run_compaction_guard.py, in _compact
run_compaction_guard.py, in _summarize_into
compaction_engine.py, in summarize_messages
  raise ValueError("The model returned an empty checkpoint")
```

The guard caught it, restored the original working context, and let the agent
continue uncompacted — so the run kept going but its next request could still
exceed the window.

## Root cause

Three things, in order of how much they hid:

1. **`LiteLLMCaller.call_plain()` collapsed the response to a string**
   (`response.choices[0].message.content or ""`). Everything that would explain
   a blank reply — `finish_reason`, usage including reasoning tokens, whether
   the message had content at all — was dropped at that line. Downstream, an
   empty reply was indistinguishable from a provider failure.

2. **The output budget was fixed at `SUMMARY_MAX_TOKENS` (3000).** A reasoning
   model spends output tokens on reasoning *before* it writes any final text.
   When reasoning drains the cap, litellm reports `finish_reason: "length"`
   with no content — exactly a "model wrote nothing" from the caller's side, but
   with a different fix (more tokens, not a retry of the same request).

3. **No recovery was attempted.** One blank reply failed the whole compaction,
   even when the next identical request would have answered.

## What changed

### Diagnostics (`atlas/modules/llm/litellm_caller.py`)

`describe_plain_response(response, model_name)` extracts the metadata that
explains a blank reply, and `call_plain_detailed()` returns it next to the text
as a `PlainCompletion` (`atlas/modules/llm/models.py`). `call_plain()` keeps
returning just the string.

The metadata is deliberately narrow — counts, structure flags, and the
allow-listed finish reason:

- `model` (the Atlas key) and `litellm_model` / `provider` (resolved target),
- `finish_reason`, mapped to `"other"` when the provider sends something
  outside the OpenAI vocabulary, so provider text is never interpolated,
- `choice_count`, `content_present`, `content_chars`,
- `has_reasoning_content` (a flag: the reasoning text itself is never read),
- `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `total_tokens`,
- `requested_max_tokens`.

Content, reasoning text and the request body stay out. Custom endpoints can
carry an internal host in the model alias, so the resolved model id is stripped
of any URL authority before it is logged.

### Classification and bounded retry (`compaction_engine.py`)

`classify_empty_response()` turns that metadata into a cause:

| Reason | Meaning |
|---|---|
| `empty_content` | The model answered with no text. |
| `output_token_exhaustion` | `finish_reason: length` — the budget was spent first, typically by reasoning. |
| `reasoning_only` | Reasoning was returned, but no final text. |
| `provider_error` | The call itself failed; it arrives as a raised domain error. |

`_summarize_chunk()` retries **once** per transcript chunk with a larger output
budget and deterministic sampling (`temperature` 0). It is one attempt, not a
loop: two calls per chunk, six chunks at most, is the whole recovery budget. A
provider error is an exception, not a blank reply, so it propagates — the retry
never swallows the error class callers depend on (`RateLimitError`,
`ContextWindowExceededError`, ...).

`checkpoint_output_budget()` sizes the cap: reasoning models (those configured
with a `reasoning_effort`, the codebase's marker for one) start from the larger
budget, and the retry always gets more headroom than the first attempt. The
summarizer is reachable only through the conversation's own model via
`call_plain`, so a retry is authorized exactly as the first call is, at the same
data classification, with `PreLlmCall` hooks applied to it unchanged.

### Reporting (`compaction_policy.py`)

`EmptyCheckpointError` is a `DomainError` carrying the `reason` and the
`diagnostics`, so `failure_text()` can render one short user-facing line that
names the cause while raw provider text is never shown. `failure_frame()` adds
the `reason` to the `failed` WebSocket frame for operators; the diagnostics
stay in the log.

## Notes for reviewers

- The budget is a constant, not a model capability lookup. A provider that
  rejects the larger value fails the call in the ordinary way and the error is
  reported with the same diagnostics; 8000 output tokens is inside the cap of
  every chat model Atlas targets today.
- `call_plain_detailed` is optional on `LLMProtocol` and probed with `getattr`
  (plus an awaitable check) so other LLM implementations and test doubles keep
  working — they get the retry with empty diagnostics.
- Nothing in the change touches stored history. A failed summary leaves both the
  conversation history and the run's working messages to the caller untouched.
- No cross-replica state is introduced: the retry is local to one summarizer
  call, and the guard's rollback is unchanged.
