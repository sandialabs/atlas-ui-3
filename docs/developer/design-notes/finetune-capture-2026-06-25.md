# Opt-in Fine-tune Capture

Date: 2026-06-25

Point-in-time record of how the opt-in chat-capture feature (issue #622) was
built and the judgment calls made along the way. For how the feature works
today and how to operate it, see
[Fine-tune Capture](../finetune-capture.md).

## Goal

Let users *voluntarily* opt in to recording the full content of their chats —
prompts, model completions, tool calls, tool results, and the available-tool
list — so the traffic can be exported as training data for fine-tuning a small,
customized model. The high-value signal is a **rollback-with-forced-tool**
flow: when the model picks the wrong tool, the user re-runs the turn forcing the
correct tool, and both versions are saved as a DPO-style `(rejected, chosen)`
pair. Tool-calling on bespoke agentic workflows is exactly the data that sits
outside most base models' training distribution.

## Guiding principles (from the design sketch)

- **Two flags, not one.** Off by default at both the system level
  (`FEATURE_FINETUNE_CAPTURE_ENABLED`) and the per-user consent level. Capture
  happens iff both are true at turn time.
- **Reuse existing infrastructure.** The feedback subsystem is the storage
  template; the existing `rewind_to_user_index` edit/resubmit mechanic is the
  replay engine; `selected_tools` is the tool-forcing lever.
- **Silence is not success.** Implicit "no feedback" signals default to
  `unknown` at low confidence, never to positive.

## Judgment calls where the design sketch was out of date

The original gist assumed two things that were no longer true in the codebase.
Both were resolved by reusing mechanics that already exist rather than
introducing the ones the sketch imagined:

1. **`ChatRequest.tool_choice_required` does not exist, and forced
   `tool_choice="required"` was deliberately removed** (PR #664) because several
   providers reject it and the control-tool parsing was fragile. The sketch's
   central mechanic — replay with `selected_tools=[X], tool_choice_required=True`
   — is therefore not available. **Resolution:** the provider-safe way to force a
   tool is to *narrow the available tool list to exactly one tool*
   (`selected_tools=["the_right_tool"]`). With a single tool offered, the model
   reliably calls it, and no unsupported `tool_choice` value is sent.

2. **There is no per-user backend settings store.** Frontend settings live in
   `localStorage`. Consent must be durable and server-authoritative, so capture
   ships its own small consent store (one JSON file per salted user hash under
   the capture directory).

A third reality shaped the capture point: **telemetry spans are deliberately
content-scrubbed** (`_SAFE_ATTRIBUTE_KEYS` allows only hashes/counts/model
names — never raw prompts or tool arguments). Capture therefore cannot piggyback
on spans for content; it is a separate, opt-in store.

## Architecture

```
atlas/
├── domain/capture/models.py              CapturedTurn, Trajectory, Label, ConsentRecord
├── application/chat/capture/
│   ├── capture_context.py                ContextVar + per-turn accumulator + tool-call normalizer
│   ├── capture_store.py                  consent CRUD, JSONL append/read, stats, self-delete
│   └── capture_service.py                two-flag policy + record derivation
├── routes/capture_routes.py              consent + admin stats/export + self-delete
└── finetune_export_cli.py                dpo / sft / raw exporter (atlas-finetune-export)
```

### Where capture hooks in

The decisive LLM I/O (the messages the model saw, the available tools, and the
tool calls it produced) is only fully present deep inside the LLM caller. Rather
than thread a capture sink through every mode runner and streaming generator,
the recorder uses a **ContextVar**:

1. `ChatService.handle_chat_message` checks both flags. When capture is on it
   builds a `CaptureTurnContext` and activates it for the turn via
   `capture_turn(ctx)`.
2. `LiteLLMStreamingMixin.stream_with_tools` — the single chokepoint for both
   tools mode and agent mode — calls `record_llm_call(...)` at the point where
   it has assembled the full response (content + accumulated tool_calls). When
   no context is active (the common case) this is one cheap branch with zero
   behaviour change.
3. After the turn, `CaptureService.finish_turn` derives a `CapturedTurn` from
   the accumulated calls and appends it to the JSONL store.

The ContextVar propagates automatically because the LLM call is awaited within
the same asyncio task that activated the context, and turns on other
connections never see each other's context.

### Rollback correction reuses rewind

There is no separate replay engine. A correction is just a normal chat turn sent
over the existing WebSocket with three extra ingredients:

- `rewind_to_user_index` — truncate history back to the user message that
  produced the wrong turn (existing mechanic).
- `selected_tools=["the_right_tool"]` — narrow the tool list to force the choice.
- `capture_correction={rejected: {...}, note}` — the wrong trajectory the user
  is correcting (the frontend already rendered it).

`finish_turn` sees the correction marker, pairs the freshly captured `chosen`
trajectory with the client-supplied `rejected` one, and writes a `kind: "pair"`
record labelled `rollback` at high confidence.

## Data model

One JSONL line per labelled turn under
`runtime/finetune_capture/data/<YYYY-MM-DD>/<user_hash>.jsonl`. `kind: "turn"` is
an SFT example (no `rejected`); `kind: "pair"` is a DPO example. The full raw
per-call transcript is retained under `transcript` so downstream pipelines can
rebuild any view even as the derived `chosen`/`rejected`/`available_tools` fields
evolve. `schema_version` gates exporter compatibility. The available-tool
schemas are pinned at capture time to combat tool drift.

`user_hash` is a salted SHA-256 (`CAPTURE_USER_SALT`) so raw emails never appear
in filenames or records; self-delete walks the store and rewrites without that
hash.

## Privacy and safety

- Off by default at both levels; opting in is rejected if the system flag is off.
- Full capture means full PII capture (tool results can contain DB rows, file
  contents, secrets). This is documented; PII scrubbing is intentionally left as
  a future opt-in-within-opt-in (scrubbing degrades training data).
- Every capture path is fail-soft: an exception in capture can never break a
  chat turn.
- Admin stats/export gated by the same `admin_group` membership as feedback.

## Deliberately out of scope for v1

Cross-user dataset pooling, tool-output editing, per-conversation rating
records, and any live training loop. The rollback pair is the minimum useful
slice and forces the consent + capture machinery to be designed once for the
hardest case.
