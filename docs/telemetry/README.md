# Telemetry: OpenTelemetry audit trail

Last updated: 2026-04-19

ATLAS emits OpenTelemetry spans for every high-value event in a chat turn so
operators, T&E analysts, and downstream dashboards can answer questions like:

- Which tools are being called, and how often do they succeed?
- What is p95 LLM latency by model?
- How many retries are happening per turn?
- What documents did RAG retrieve, and which ones made it into the prompt?

All spans are written as one JSON line per span to
``logs/spans.jsonl``. The schema is stable and forms the contract that
downstream tooling (analysis scripts, admin dashboards) relies on.

## Span types

Every user turn emits one root span with child spans parented under it:

```
chat.turn               (root — one per user message)
├── llm.call            (one per LiteLLM call, incl. retries as retry_count)
├── tool.call           (one per tool invocation)
└── rag.query           (one per RAG data-source query, incl. batched queries)
```

Parent/child relationships are populated automatically by OpenTelemetry's
context propagation.

## Span attribute contract

### `chat.turn`

| Attribute | Type | Description |
|---|---|---|
| `turn_id` | string | Fresh UUID generated per user message |
| `session_id` | string | WebSocket session UUID |
| `user_hash` | string | SHA-256[:16] of the user email |
| `prompt_hash` | string | SHA-256[:16] of the user prompt |
| `prompt_chars` | int | Character count of the user prompt |
| `prompt_tokens` | int | Rough estimate (chars / 4); authoritative counts live on `llm.call` |
| `model` | string | Model ID selected for the turn (ATLAS internal name) |
| `agent_mode` | bool | Whether agent mode was active |
| `only_rag` | bool | RAG-only mode flag |
| `tool_choice_required` | bool | Tool choice forcing flag |
| `selected_tools_count` | int | Number of tools enabled for the turn |
| `selected_prompts_count` | int | Number of prompts enabled |
| `selected_data_sources_count` | int | Number of RAG data sources enabled |

### `llm.call`

| Attribute | Type | Description |
|---|---|---|
| `model` | string | LiteLLM-qualified model (e.g. `openai/gpt-4o`) |
| `provider` | string | Provider prefix (e.g. `openai`, `anthropic`) |
| `model_version` | string | Model suffix after the provider prefix |
| `temperature` | float | Sampling temperature |
| `max_tokens` | int | Max tokens requested |
| `streaming` | bool | Streaming vs. blocking call |
| `has_tools` | bool | Tools schema attached |
| `tool_choice` | string | `auto` / `required` / etc. |
| `tools_schema_count` | int | Count of tools in the schema |
| `message_count` | int | Number of messages sent to the model |
| `input_tokens` | int | From litellm usage.prompt_tokens |
| `output_tokens` | int | From litellm usage.completion_tokens |
| `total_tokens` | int | From litellm usage.total_tokens |
| `finish_reason` | string | From the first choice |
| `tool_calls_count` | int | Number of tool calls in the response |
| `retry_count` | int | Transient-error retries within this call (0 = succeeded first try) |
| `latency_ms` | int | Wall-clock duration in ms |
| `chunk_count` | int | Streaming only: number of content chunks received |
| `output_chars` | int | Streaming only: total accumulated output characters |
| `error_type` | string | Exception class name when the call failed |

### `tool.call`

| Attribute | Type | Description |
|---|---|---|
| `tool_name` | string | Full tool name as exposed to the LLM (e.g. `calculator_add`) |
| `tool_source` | string | Heuristic MCP server prefix — text before the first `_` |
| `tool_call_id` | string | LLM-assigned call ID |
| `args_hash` | string | SHA-256[:16] of the raw arguments JSON |
| `args_size` | int | UTF-8 byte size of the raw arguments |
| `success` | bool | Tool returned without error |
| `duration_ms` | int | Wall-clock duration in ms |
| `output_size` | int | UTF-8 byte size of the tool output |
| `output_sha256` | string | Full SHA-256 hex digest of the tool output |
| `output_preview` | string | First 500 chars of the output, sanitized (no CR/LF) |
| `error_message` | string | Error detail when `success=false` |

### `rag.query`

| Attribute | Type | Description |
|---|---|---|
| `data_source` | string | Qualified data source (`server:source_id`), or comma list for batched queries |
| `query_hash` | string | SHA-256[:16] of the query text (last user message) |
| `query_chars` | int | Character count of the query text |
| `user_hash` | string | SHA-256[:16] of the user name |
| `message_count` | int | Number of messages forwarded to RAG |
| `batch` | bool | True for batched multi-source queries |
| `batch_size` | int | Batched queries only: number of sources |
| `is_completion` | bool | RAG returned a pre-synthesized answer |
| `content_size` | int | UTF-8 byte size of the RAG content |
| `num_results` | int | Documents returned by retrieval |
| `total_documents_searched` | int | From RAG metadata |
| `retrieval_method` | string | From RAG metadata (e.g. `hybrid`, `mcp_synthesis`) |
| `query_processing_time_ms` | int | From RAG metadata |
| `doc_ids` | list[string] | Chunk IDs (or titles/sources as fallback) |
| `doc_scores` | list[float] | Confidence scores aligned with `doc_ids` |
| `docs_used_in_context` | list[string] | Subset of `doc_ids` injected into the LLM prompt |
| `top_score` | float | Max confidence score across returned docs |

> In the current ATLAS implementation `docs_used_in_context` equals `doc_ids`
> because every retrieved document is injected into the prompt. The separate
> field is preserved for future reranking/filtering so dashboards don't need a
> schema change later.

## Sensitive-data policy

The audit trail is designed so the span file can be shared across teams
without leaking chat content.

**Never written to span attributes (by design):**

- Raw prompts
- Raw tool arguments
- Raw tool outputs
- Raw RAG document text
- Raw user emails

**Always written (useful for analysis):**

- SHA-256 hashes (16-char for attributes, 64-char for output fingerprinting)
- Sizes, counts, durations, token counts
- Model names, tool names, server names, data source IDs
- Status and error class names

**Optional, opt-in only:**

- Full tool outputs, written to `logs/tool_outputs/{span_id}.txt` when
  `ATLAS_LOG_TOOL_OUTPUTS=true`. Off by default.

## Configuration

Set in `.env`:

```bash
# Optional: forward spans to an OTLP collector in addition to the JSONL file
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Optional: capture full tool outputs alongside the spans file
# ATLAS_LOG_TOOL_OUTPUTS=false

# Log directory (used for app.jsonl, spans.jsonl, and tool_outputs/)
# APP_LOG_DIR=/path/to/logs
```

## Analyzing spans

A reference pandas script is provided:

```bash
python docs/telemetry/analysis_example.py                  # default path
python docs/telemetry/analysis_example.py /path/to/spans.jsonl
```

It computes:

- Tool success rate and p95 duration per tool
- p50 / p95 LLM latency per model + average token usage and retry count
- RAG retrieval-to-use ratio per data source
- LLM call and retry counts per chat turn

## Extending the contract

Adding a new attribute is safe — downstream analysis treats attributes as
optional. Renaming or removing an attribute is a **breaking change**:
coordinate with downstream dashboard owners first and bump the relevant
section of this README.
