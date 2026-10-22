# Context Tracking and Compaction

Last updated: 2026-10-21

Issue #877.

Long conversations, and long agent runs in particular, eventually outgrow the
model's context window. Atlas tracks how large the *next* request to the model
will be and compacts older context into a **checkpoint** when it gets close to
the limit, either when the user asks (manual) or on its own (automatic). The
full transcript is never shortened: compaction only changes what is sent to the
model.

## What the user sees

- **Context meter** next to the composer: `Context ~82K / 128K`. The value is
  the server's estimate of the next request -- system prompt, the conversation
  after any checkpoint, tool definitions and results, inline images -- against
  the model's context window. A `?` after the window means the window is
  assumed (see below). The details panel separates three numbers:
  - the **estimate** of the next request (tokenizer or rough estimate);
  - the **last request** as reported by the provider, when it reports usage;
  - **session usage**, the cumulative provider-reported tokens across calls.
- **Compact** button with an optional focus ("keep the auth design decisions in
  most detail"), and the `/compact [focus]` command in the composer. Manual
  compaction runs between turns and then waits for the next message.
- **Compact automatically in this conversation** toggle, remembered per
  conversation in the browser.
- A divider row in the transcript for every compaction, with the before/after
  estimate; the checkpoint text can be expanded under it.

## How compaction works

One implementation serves every trigger
(`atlas/application/chat/context/compaction_controller.py`):

1. **Prune.** Bulky older tool outputs (and older inline images) in an agent
   run's working transcript are replaced by a short stub with a preview. Every
   tool result keeps its `tool_call_id`, so call/result pairing is unchanged.
2. **Checkpoint.** If that is not enough, an older, complete stretch of the
   conversation is summarized into a structured checkpoint (Goal, Constraints,
   Key decisions, Discoveries, Accomplished, Current state, Remaining work,
   Relevant files/resources) and a recent, token-bounded tail is kept verbatim.
   The tail always starts at a user or assistant message, so a kept tool call
   always keeps its result and no kept result loses its call. A list of the
   tool calls already executed is appended by code (not by the model), with an
   instruction not to repeat them.

The system prompt, tool definitions and files manifest are rebuilt for every
request as before and are never summarized.

### Between turns (conversation checkpoints)

Manual compaction, and automatic compaction before a turn, append a
`context_checkpoint` row to the conversation's history. Its metadata holds the
summary and the id of the last message it covers. Every later request sends
the checkpoint in place of the covered messages (folded into the next user
message, so role alternation is unchanged). The covered messages stay in
history and in the stored conversation; a rewind that removes them also
removes the checkpoint.

### Inside an agent run

Before each LLM call of an agent run -- at a step boundary, after every tool
call of the previous step has its result -- the run's working transcript is
checked against the threshold and compacted in place if needed. The run then
continues with its next call; no new user message is required. A run
checkpoint is recorded in history as a display-only row.

A later compaction in the same run builds on the earlier one: it passes the
previous summary to the summarizer and keeps every tool call already listed, so
a side-effecting call never drops out of the "already executed" list. The
user's request carried by every checkpoint is the stored user message, not the
assembled prompt. A stopped or disconnected compaction leaves the run's
transcript as it was and tells the client the compaction ended.

### Context-length errors

If the provider rejects an agent step as too long, the run compacts harder and
retries the call, up to `CONTEXT_COMPACTION_MAX_RETRIES` times. Only the LLM
request is retried; tools are never re-executed. Past the bound, or with
automatic compaction off, the error is shown as before.

### Failures

A failed summary (provider error, empty reply, or a checkpoint that would not
shrink the context) leaves the history and the run exactly as they were, shows
a notice, and the turn continues uncompacted.

## Security and data handling

- Manual compaction is sent as a `compact_context` frame and runs through the
  same path as a chat turn (`ChatService.handle_chat_message` with
  `compact_only`), so the required-level check, the conversation's recorded
  classification, conversation ownership, and model authorization all apply
  before anything is summarized. It is refused while a turn or run is executing
  in the conversation.
- Summaries are produced by the conversation's own model, through the normal
  `call_plain` path, for the same user. No other model ever receives the
  content, and `PreLlmCall` hooks see compaction calls like any other call.
- Checkpoints are stored in the conversation record, so they carry its data
  classification; a conversation restored at another level is refused exactly
  as before.
- A manual compaction reloads the stored conversation first, so it never
  summarizes (or saves over) a stale copy held by the connection.

## Model context windows

The window is resolved per model (`atlas/modules/llm/context_window.py`):

1. `context_window` on the model in `llmconfig.yml` (also allowed in a LiteLLM
   gateway's `model_defaults` or per-model `models` entries). Set it for custom
   endpoints and aliases.
2. LiteLLM's bundled model catalog, looked up by the configured `model_name`
   with and without its provider prefix. No network calls are made.
3. `CONTEXT_FALLBACK_WINDOW_TOKENS`, shown in the UI as assumed.

The usable context is the window minus a reply reserve (the model's
`max_tokens`, capped at a quarter of the window) and a 5% safety margin.

```yaml
models:
  in-house-llm:
    model_name: in-house-llm-v3
    model_url: https://llm.example.internal/v1
    context_window: 65536
    max_tokens: 4096
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `FEATURE_CONTEXT_COMPACTION_ENABLED` | `true` | Context meter, manual and automatic compaction. When off, a `compact_context` request is refused. |
| `CONTEXT_AUTO_COMPACT_DEFAULT` | `true` | Whether automatic compaction starts on for a conversation. |
| `CONTEXT_COMPACTION_THRESHOLD_PERCENT` | `80` | Compact automatically at this percent of the usable context. |
| `CONTEXT_COMPACTION_TURN_INTERVAL` | `0` | Also compact every N user turns since the last checkpoint (0 = off). |
| `CONTEXT_COMPACTION_KEEP_RECENT_PERCENT` | `25` | Share of the usable context kept verbatim after an automatic checkpoint. Manual compaction keeps only the latest turn. |
| `CONTEXT_COMPACTION_MAX_RETRIES` | `2` | Emergency compactions per agent step after a context-length error. |
| `CONTEXT_FALLBACK_WINDOW_TOKENS` | `128000` | Window assumed for a model that is neither configured nor in LiteLLM's catalog. |

## WebSocket frames

- Client `compact_context` -- `{model, conversation_id, focus?, auto_compact?, compliance_level_filter, save_mode}`.
- Client `context_usage_request` -- asks for an estimate of the conversation on
  this connection (sent when a conversation is opened or the model changes).
  Requests less than a second apart are coalesced: the newest is answered once
  the second is up.
- Server `context_usage` -- the estimate, window, window source, usable and
  threshold tokens, provider-reported last prompt size and cumulative usage,
  and the `conversation_id` it measures.
- Server `context_compaction` -- `status` `started` / `completed` / `skipped` /
  `failed`, with `trigger` (`manual`, `auto`, `turn_interval`, `overflow`),
  `scope` (`conversation` or `run`), before/after estimates and the summary.
  A `failed` frame with `silent: true` only ends a pending compaction (refused,
  stopped or disconnected); the reason, if any, arrives as an `error` frame.
- A `chat` frame sent while a manual compaction runs is refused with
  `error_type: compaction_in_progress`.
- Client `chat` frames carry `auto_compact`, the conversation's toggle.

## Limitations

- In local or no-save modes the browser restores a conversation by replaying
  its messages, and checkpoints are not replayed; automatic compaction
  re-creates one when it is needed.
- Tools mode (non-agent) is covered by compaction before the turn, not between
  its tool rounds.
- Provider usage is shown only when the provider reports it (most non-streaming
  calls; streaming only when the provider sends a usage chunk).
