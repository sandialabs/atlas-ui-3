# Telemetry: OpenTelemetry audit trail

Last updated: 2026-04-20

ATLAS emits OpenTelemetry spans for every high-value event in a chat turn so
operators, T&E analysts, and downstream dashboards can answer questions like:

- Which tools are being called, and how often do they succeed?
- What is p95 LLM latency by model?
- How many retries are happening per turn?
- What documents did RAG retrieve, and which ones made it into the prompt?

All spans are written as one JSON line per span to
``logs/spans.jsonl``. The schema is stable and forms the contract that
downstream tooling (analysis scripts, admin dashboards) relies on.

## File layout

The **directory** is configurable via `APP_LOG_DIR` in `.env`; the
**filenames are fixed** so downstream tooling (analysis scripts,
dashboards, log-shipping agents) can hard-code them.

```bash
# .env — point all telemetry artifacts at a custom directory
APP_LOG_DIR=/var/log/atlas
```

Resulting layout (whether default `<project_root>/logs/` or your custom
`APP_LOG_DIR`):

```
<APP_LOG_DIR>/
├── spans.jsonl              # one JSON line per span (always this name)
├── app.jsonl                # structured application logs
└── tool_outputs/            # only when ATLAS_LOG_TOOL_OUTPUTS=true
    └── <span_id>.txt        # one file per successful tool call
```

Resolution order: `config_manager.app_settings.app_log_dir` →
`APP_LOG_DIR` env var → `<project_root>/logs/`. Need separate files per
instance on a shared host? Point each instance at a different
`APP_LOG_DIR` (e.g. `/var/log/atlas/instance-a/`) — the filenames stay
`spans.jsonl` / `app.jsonl` inside each directory.

## Span types

Every user turn emits one root span with child spans parented under it:

```
chat.turn               (root — one per user message)
├── llm.call            (one per LiteLLM call, incl. retries as retry_count)
├── tool.call           (one per tool invocation)
│   └── file.upload     (storage spans parent under the producing tool call)
└── rag.query           (one per RAG data-source query, incl. batched queries)

file.upload / file.download / storage.list / storage.delete
    emitted from S3StorageClient and MockS3StorageClient; parent under
    tool.call when triggered by a tool artifact, otherwise stand alone
    (e.g. user uploads via /api/files, admin listing, etc.)
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
| `output_tokens_estimate` | int | Streaming only: rough token estimate (`chars / 4`). Published under a distinct name from the authoritative `output_tokens` so aggregations never silently mix real counts with approximations. |
| `error_type` | string | Exception class name when the call failed |

### `tool.call`

| Attribute | Type | Description |
|---|---|---|
| `tool_name` | string | Full tool name as exposed to the LLM (e.g. `calculator_add`) |
| `tool_source` | string | MCP server name, looked up via `tool_manager.get_server_for_tool(name)`. `null` when the mapping is unavailable. |
| `tool_call_id` | string | LLM-assigned call ID |
| `args_hash` | string | SHA-256[:16] of the raw arguments JSON |
| `args_size` | int | UTF-8 byte size of the raw arguments |
| `args_edited` | bool | User edited the arguments in the approval dialog before execution |
| `success` | bool | Tool returned without error |
| `duration_ms` | int | Wall-clock duration in ms |
| `output_size` | int | UTF-8 byte size of the tool output (pre-edit-note) |
| `output_sha256` | string | Full SHA-256 hex digest of the tool output (pre-edit-note) |
| `output_preview` | string | First 500 chars of the pre-edit-note output, sanitized (no CR/LF) |
| `error_message` | string | Error detail when `success=false` |

> `output_*` attributes always reflect the raw tool output. When
> `args_edited=true`, ATLAS prepends an "edit note" (containing the
> user-edited arguments) to `result.content` for the LLM — but the span
> captures telemetry from the pre-edit content so executed arguments don't
> leak into span attributes.

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

### `file.upload`

| Attribute | Type | Description |
|---|---|---|
| `user_hash` | string | HMAC-SHA256[:16] of the user email |
| `key_hash` | string | HMAC-SHA256[:16] of the generated S3 key |
| `filename` | string | Sanitized + capped via `safe_label` (≤200 chars, no CR/LF) |
| `content_type` | string | MIME type from caller (server-validated upstream) |
| `file_size` | int | Bytes after base64 decode |
| `source_type` | string | `user` \| `tool` |
| `category` | string | `uploads` \| `generated` \| `other` (derived from key) |
| `storage_backend` | string | `s3` \| `mock` |
| `success` | bool | Upload completed without error |
| `duration_ms` | int | Wall-clock from entry to return |
| `error_type` | string | Exception class name when `success=false` |
| `error_message` | string | Sanitized preview via `preview(..., max_chars=300)` when `success=false` |

### `file.download`

| Attribute | Type | Description |
|---|---|---|
| `user_hash` | string | HMAC-SHA256[:16] of the user email |
| `key_hash` | string | HMAC-SHA256[:16] of the requested key |
| `filename` | string | `safe_label` of original filename (from S3 metadata) |
| `content_type` | string | From S3 object |
| `file_size` | int | Bytes read |
| `category` | string | Derived from key |
| `storage_backend` | string | `s3` \| `mock` |
| `success` | bool | Download completed without error |
| `access_denied` | bool | Cross-user key attempt — set true *before raising*, so the span persists the event |
| `not_found` | bool | S3 returned NoSuchKey |
| `duration_ms` | int | Wall-clock |
| `error_type` | string | Exception class name on failure |
| `error_message` | string | Sanitized preview on failure |

### `storage.list`

| Attribute | Type | Description |
|---|---|---|
| `user_hash` | string | HMAC-SHA256[:16] of the user email |
| `file_type` | string | `user` \| `tool` \| `null` (sentinel — no filter applied) |
| `limit` | int | Requested max count |
| `num_results` | int | Files returned |
| `total_bytes` | int | Sum of sizes across returned files |
| `storage_backend` | string | `s3` \| `mock` |
| `success` | bool | List completed without error |
| `duration_ms` | int | Wall-clock |
| `error_type` | string | Exception class name on failure |
| `error_message` | string | Sanitized preview via `preview(..., max_chars=300)` on failure |

### `storage.delete`

| Attribute | Type | Description |
|---|---|---|
| `user_hash` | string | HMAC-SHA256[:16] of the user email |
| `key_hash` | string | HMAC-SHA256[:16] of the requested key |
| `category` | string | Derived from key |
| `storage_backend` | string | `s3` \| `mock` |
| `success` | bool | Delete returned True |
| `access_denied` | bool | Cross-user key attempt — set true *before raising* |
| `not_found` | bool | NoSuchKey (deletes return False, not raise) — also sets `error_type` for consistent failure aggregation |
| `duration_ms` | int | Wall-clock |
| `error_type` | string | Exception class name on failure (`"NoSuchKey"`/`"NotFound"` on non-raising not-found branches) |
| `error_message` | string | Sanitized preview via `preview(..., max_chars=300)` on failure |

> Storage spans propagate the ambient trace context. When a tool produces an
> artifact that gets uploaded via `file_processor.process_tool_artifacts`,
> the `file.upload` span naturally parents under the `tool.call` span, giving
> a single waterfall from user prompt → tool execution → S3 PUT.

## Sensitive-data policy

The audit trail is designed so the span file can be shared across teams
without leaking chat content.

**Never written to span attributes (by design):**

- Raw prompts
- Raw tool arguments
- Raw tool outputs
- Raw RAG document text
- Raw user emails
- Raw S3 keys, raw bucket names, raw filenames beyond the sanitized label,
  raw file contents
- Raw exception messages (sanitized + capped previews only — upstream
  exception strings routinely embed caller args, URLs with tokens, and
  user content)

**Always written (useful for analysis):**

- Keyed HMAC-SHA256 identifiers (16 hex chars — see *Pseudonymization*
  below)
- Full SHA-256 content fingerprints for tool outputs (64 hex chars)
- Sizes, counts, durations, token counts
- Model names, tool names, server names, data source IDs
- Status and error class names, plus a sanitized + capped error message
  preview (max 300 chars, control chars stripped)
- RAG document identifiers: `chunk_id` when available (opaque, low leak
  risk); otherwise a sanitized and length-capped fallback (≤200 chars)
  derived from `title`/`source`

**Optional, opt-in only:**

- Full tool outputs, written to `logs/tool_outputs/{span_id}.txt` when
  `ATLAS_LOG_TOOL_OUTPUTS=true`. Off by default. Files are created with
  mode `0600` and the directory with mode `0700` on POSIX filesystems.

### Pseudonymization vs. anonymization

Identifier hashes (`user_hash`, `prompt_hash`, `query_hash`, `args_hash`)
are **pseudonymized, not anonymized**. They are short (64-bit) keyed
HMAC-SHA256 digests. In a small population (a few thousand known emails,
or short common prompts like "hi"), a plain SHA-256 truncated digest
would be trivially reversible via a rainbow table. ATLAS therefore uses
HMAC with a per-deployment secret so hashes cannot be reversed without
access to the secret:

```bash
# Preferred: dedicated telemetry secret
ATLAS_TELEMETRY_HMAC_SECRET=$(openssl rand -hex 32)

# Fallback: reuses the CAPABILITY_TOKEN_SECRET already set in production
# CAPABILITY_TOKEN_SECRET=...
```

If neither is set, ATLAS uses a per-process random key and emits a
startup warning — hashes in that mode won't match across restarts.

> The on-disk artifacts (`spans.jsonl`, `tool_outputs/*.txt`) are
> audit-trail records of *who did what when*, minus the content. Treat
> them with the same access controls you apply to other security logs.
> `spans.jsonl` and `app.jsonl` are created with mode `0600`.

## Configuration

Set in `.env`:

```bash
# Optional: forward spans to an OTLP collector in addition to the JSONL file
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Optional: capture full tool outputs alongside the spans file
# ATLAS_LOG_TOOL_OUTPUTS=false

# Log directory (used for app.jsonl, spans.jsonl, and tool_outputs/)
# APP_LOG_DIR=/path/to/logs

# HMAC secret for identifier pseudonymization. When unset, ATLAS falls back
# to CAPABILITY_TOKEN_SECRET, then to an ephemeral per-process key (with a
# startup warning). Set this for stable, non-rainbow-reversible hashes.
# ATLAS_TELEMETRY_HMAC_SECRET=$(openssl rand -hex 32)
```

## Analyzing spans

A reference pandas + matplotlib script is provided:

```bash
python docs/telemetry/analysis_example.py                            # default paths
python docs/telemetry/analysis_example.py /path/to/spans.jsonl       # custom input
python docs/telemetry/analysis_example.py --output-dir ./plots       # custom plot dir
```

Requires: `uv pip install pandas matplotlib`.

**Tables printed to stdout:**

- Tool success rate and p95 duration per tool
- Tool call counts (pure volume breakdown, sorted descending)
- p50 / p95 LLM latency per model + average token usage and retry count
- RAG retrieval-to-use ratio per data source
- LLM call and retry counts per chat turn
- Daily span counts by type
- Average daily counts by day-of-week (surfaces weekend-vs-weekday patterns)
- Daily p50 / p95 LLM latency
- Daily tool success rate + call count

**PNG plots saved to `<spans_parent>/analysis/`** (or `--output-dir`):

- `tool_call_counts.png` — horizontal bar chart of calls per tool
- `daily_counts.png` — daily turns/tool calls/LLM calls/RAG queries over time
- `day_of_week.png` — average usage by day of week (weekend dip shows here)
- `hourly_pattern.png` — hour-of-day usage (UTC)
- `llm_latency_trend.png` — p50 / p95 LLM latency trend
- `tool_success_trend.png` — daily tool success rate + call volume

## Optional: visualizing spans with Grafana Tempo + Grafana

The JSONL file is the primary audit artifact, but you can also forward spans to
a full tracing stack for interactive exploration (search by tool name, span
duration heatmaps, waterfall views of a single turn, etc.). A minimal
setup uses three containers: an OpenTelemetry Collector, Grafana Tempo for
trace storage, and Grafana for the UI.

This is entirely optional — ATLAS still works with just `logs/spans.jsonl`.

### 1. Drop this `docker-compose.yml` next to ATLAS

```yaml
version: "3"
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol/config.yaml
    ports:
      - "4317:4317"   # OTLP gRPC (what ATLAS will send to)
      - "4318:4318"   # OTLP HTTP
    depends_on:
      - tempo

  tempo:
    image: grafana/tempo:latest
    command: ["-config.file=/etc/tempo.yaml"]
    volumes:
      - ./tempo.yaml:/etc/tempo.yaml
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"   # Tempo query API

  grafana:
    image: grafana/grafana:latest
    environment:
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
      - GF_AUTH_DISABLE_LOGIN_FORM=true
    volumes:
      - ./grafana-datasources.yaml:/etc/grafana/provisioning/datasources/datasources.yaml
    ports:
      - "3000:3000"
    depends_on:
      - tempo

volumes:
  tempo-data:
```

### 2. Minimal `otel-collector-config.yaml`

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlp/tempo]
```

### 3. Minimal `tempo.yaml`

```yaml
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/blocks
    wal:
      path: /var/tempo/wal
```

### 4. Grafana datasource provisioning (`grafana-datasources.yaml`)

```yaml
apiVersion: 1
datasources:
  - name: Tempo
    type: tempo
    access: proxy
    url: http://tempo:3200
    isDefault: true
```

### 5. Point ATLAS at the collector

```bash
# .env
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

Restart ATLAS. Spans now go to *both* `logs/spans.jsonl` (always) and the
OTLP collector (when configured).

### 6. Explore in Grafana

```bash
docker compose up -d
# Grafana: http://localhost:3000  (anonymous admin — dev only)
```

Open Grafana -> Explore -> Tempo datasource. Useful queries:

- **Search for a turn**: filter by service name `atlas-ui-3-backend` and span
  name `chat.turn`. Click a result to see the full span tree.
- **Slowest tool calls**: search for `tool.call` spans, sort by duration.
- **Failing tools**: filter `tool.call` spans where `success=false`.
- **Per-model latency**: `llm.call` spans grouped by `model` attribute.

### Running the same stack on OpenShift

The Compose recipe above maps cleanly to a single-namespace OpenShift
deployment. The full manifest lives at
[`openshift-telemetry.yaml`](./openshift-telemetry.yaml) — Namespace,
ConfigMaps for the collector / Tempo / Grafana datasources, a PVC for
Tempo, three Deployments (`otel-collector`, `tempo`, `grafana`), their
Services, and a `Route` exposing Grafana.

```bash
oc apply -f docs/telemetry/openshift-telemetry.yaml
```

Assumes OpenShift 4.x with the default `restricted-v2` SCC. `fsGroup`
is left unset so the admission controller assigns one from the
namespace range (hardcoding a value is rejected unless the service
account has the `anyuid` SCC). If the Tempo or Grafana pods crash on
startup with permission errors, grant `anyuid`:

```bash
oc adm policy add-scc-to-user anyuid -z default -n atlas-telemetry
```

Point ATLAS (running in the same cluster) at the collector via the
in-cluster Service DNS name:

```bash
# .env on the ATLAS pod
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.atlas-telemetry.svc:4317
```

If ATLAS runs *outside* the cluster, expose the collector with an
additional `Route` that uses **passthrough** TLS termination (OTLP gRPC
won't traverse an edge-terminated HTTP Route). For most deployments it's
simpler to run ATLAS in the same cluster.

**Before `oc apply`**, replace the placeholder `admin-password` in the
`grafana-admin` Secret with a strong value (e.g. `openssl rand -base64
24`). The manifest ships with anonymous access **disabled** and a
password-protected admin login so trace data is never exposed to an
unauthenticated visitor. A commented `ANONYMOUS_DEV_ONLY` block shows
how to flip to anonymous-admin for a laptop-only preview — do not use
that mode outside a single-user dev machine.

Open the Grafana URL from `oc get route grafana -n atlas-telemetry` and
log in with the credentials from the Secret.

### Alternatives

- **Only want offline analysis?** Skip this section and use
  `docs/telemetry/analysis_example.py` against `logs/spans.jsonl`.
- **Want long-term storage without running Tempo?** Point the collector's
  `otlp/tempo` exporter at a hosted backend (Grafana Cloud Traces,
  Honeycomb, Jaeger, etc.) instead.
- **Don't want Docker at all?** Install Jaeger as a single binary
  (`jaeger-all-in-one`) — it accepts OTLP directly on port 4317.

## Extending the contract

Adding a new attribute is safe — downstream analysis treats attributes as
optional. Renaming or removing an attribute is a **breaking change**:
coordinate with downstream dashboard owners first and bump the relevant
section of this README.
