# RAG API v2: Tool-Oriented Query Interface

Last updated: 2026-08-13

Status: **Phase 1 implemented** -- the v2 client, config routing and mock
endpoints are in `main`; Phases 2-4 are still proposals. See
[Implementation status](#implementation-status).

A plan to replace the v1 "dump the current message" RAG contract with a
query-targeted interface that ATLAS-UI can call like a tool: ask a specific
question, get back evidence (or a synthesized answer), and let the LLM reason
over it alongside other tools.

## TL;DR

v1 ships the whole conversation to ATLAS-RAG and lets the server pick the last
user message as the query. v2 sends an explicit `query` string and offers a
retrieval-only mode so RAG behaves like any other tool call returning context,
instead of short-circuiting the LLM.

---

## Implementation status

Phase 1 landed with issue #791. What exists today:

- `AtlasRAGClient.query_v2()` — posts `{query, corpora, mode, top_k, ...}` to
  `POST /api/v2/rag/query` and parses both response shapes. The conversation
  is never sent.
- `RAGSourceConfig.api_version` (`"v1"` default / `"v2"`) picks the contract
  per configured source, and `default_mode` picks the response shape for
  callers that do not ask for one. Endpoint paths default per version.
- `UnifiedRAGService.query_rag` / `query_rag_batch` take optional `query` and
  `mode`; an omitted `query` still falls back to the last user message, so
  every existing caller behaves as before.
- The `atlas_rag_query` tool passes its explicit `query` through and defaults
  to `mode: "raw"`, so on a v2 source the agent gets evidence to reason over
  rather than a backend-written answer.
- The mock serves `GET /api/v2/discover/datasources` (each source declaring
  `api_version`) and `POST /api/v2/rag/query`.

Not yet done: discovery-driven negotiation (config declares the version today,
`api_version` in the discovery response is parsed but not acted on), removing
the `call_with_rag` / `call_with_rag_and_tools` /
`_build_rag_completion_response` special-casing, a v2 MCP transport shape, and
retiring v1.

Open questions #1 and #4 were answered for Phase 1: the default is
`synthesized` per source (v1's user-visible behaviour is preserved) while the
agent tool asks for `raw`. #2, #3, #5, #6, #7 and #8 remain open.

---

## Current v1 Pain Points

Investigated against the live codebase (`atlas/modules/rag/atlas_rag_client.py`,
`atlas/domain/unified_rag_service.py`, `atlas/domain/rag_mcp_service.py`,
`mocks/atlas-rag-api-mock/main.py`).

### 1. Message-dumping, not query-targeted (HTTP)
`POST /api/v1/rag/completions` takes the full `messages` array. The server only
uses the last user message — the mock does `for m in reversed(request.messages):
if m.role == "user": user_query = m.content`. The entire conversation history is
shipped to the RAG API even though only the query matters.

- **Wasteful**: every turn re-sends all prior messages.
- **OPSEC/privacy**: leaks full conversation history to an external service that
  only needs the query. Violates the data-minimization principle.
- **Ambiguous semantics**: "the query" is implicit. If the user's last message
  is "thanks", that becomes the RAG query.

### 2. No explicit `query` on the HTTP path
The HTTP API has no `query` field; the query is *derived*. Meanwhile the MCP
RAG tools already take an explicit `query` string
(`rag_get_raw_results(username, query, sources, ...)`,
`rag_get_synthesized_results(username, query, ...)`). The two RAG subsystems
are **asymmetric** — same product, two different contracts.

### 3. Retrieval and generation are coupled (HTTP)
`/rag/completions` does retrieval + synthesis in one call and returns a
completion. There is no HTTP equivalent of `rag_get_raw_results` — ATLAS-UI
cannot fetch raw retrieved chunks as context for its own LLM to reason over.
The only retrieval shape on HTTP is `is_completion=True`, which
**short-circuits the LLM call entirely** (`litellm_caller._build_rag_completion_response`).

### 4. Completion short-circuit limits composability
When `is_completion=True`, ATLAS-UI returns the RAG answer verbatim and skips
its LLM. So RAG cannot act as a *tool* that returns evidence the LLM reasons
over alongside other tools. This is why RAG+tools has a special, fragile code
path (`call_with_rag_and_tools`) and why the `atlas_rag_query` pseudo-tool
exists as a bridge rather than a first-class capability.

### 5. Two parallel RAG subsystems
- HTTP: `AtlasRAGClient` → `UnifiedRAGService.query_rag` → posts `messages`.
- MCP: `RAGMCPService` → `search_raw` / `synthesize` → takes `query`.

The `atlas_rag_query` pseudo-tool (`mcp_execution.py`) awkwardly splits sources
into `http_groups` and `mcp_groups` and calls different methods on each. One
feature, three code paths, two response shapes.

### 6. Routing internals leak into the contract
`server:source_id` qualification is an ATLAS-UI routing concern but propagates
through the interface and into tool arguments.

---

## Proposed v2 API / Interface Shape

### Guiding principles
- **Query is explicit, not derived.** The caller says what it is asking.
- **Retrieval is separable from synthesis.** Offer `raw` and `synthesized` modes.
- **RAG is a tool, not a short-circuit.** Default to returning context the LLM
  reasons over; synthesized answers are an opt-in mode.
- **One canonical shape across HTTP and MCP.** v2 normalizes both transports.
- **Data minimization.** Send only the query, never the conversation.

### New endpoint: `POST /api/v2/rag/query`

```
POST /api/v2/rag/query?as_user={user}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | yes | - | The specific question to ask. Non-empty. |
| `corpora` | string \| string[] | yes | - | Corpus id(s) to search. |
| `mode` | enum `raw` \| `synthesized` | no | `raw` | `raw` returns chunks/evidence; `synthesized` returns an answer + citations. |
| `top_k` | int | no | server default | Max results per corpus. |
| `filters` | object | no | null | Server-defined metadata filters (e.g. date range, doc type). |
| `synthesis_params` | object | no | null | Mode-specific knobs (e.g. answer length, style) for `synthesized`. |

**Why `mode` instead of two endpoints?** One endpoint, one auth path, one
contract — the caller picks the shape of the answer. Mirrors the MCP split
(`rag_get_raw_results` / `rag_get_synthesized_results`) under one route.

### Response: `mode: "raw"` (default)

Returns retrieved evidence the LLM reasons over. This is the tool-call shape.

```json
{
  "query": "What is our API authentication architecture?",
  "mode": "raw",
  "results": {
    "hits": [
      {
        "document_ref": 1,
        "filename": "api-auth.pdf",
        "title": "API Authentication Guide",
        "citation": "IEEE ...",
        "sections": [
          {"section_ref": 1, "text": "The API Gateway validates JWTs...", "relevance": 0.94}
        ]
      }
    ],
    "stats": {"total_found": 3, "top_k": 8}
  },
  "metadata": {
    "response_time_ms": 142,
    "corpora_searched": ["technical-docs"]
  }
}
```

### Response: `mode: "synthesized"`

Returns an answer plus the citations it was built from. Opt-in when the caller
wants the RAG backend's LLM to synthesize rather than ATLAS-UI's LLM.

```json
{
  "query": "What is our API authentication architecture?",
  "mode": "synthesized",
  "results": {
    "answer": "The API Gateway validates JWTs issued by...",
    "citations": [
      {"document_ref": 1, "filename": "api-auth.pdf", "citation": "IEEE ..."}
    ]
  },
  "metadata": {
    "response_time_ms": 310,
    "corpora_searched": ["technical-docs"],
    "fallback_used": false
  }
}
```

### Discovery: unchanged in shape, versioned in path

```
GET /api/v2/discover/datasources?as_user={user}
```
Same response shape as v1 (`[{id, label, compliance_level, description}]`) —
discovery is not the pain point. Bump the path version so v1 and v2 can coexist
during migration.

### MCP alignment

MCP RAG tools already take `query`. v2 maps cleanly:

| v2 concept | MCP tool |
|------------|----------|
| `mode: "raw"` | `rag_get_raw_results(username, query, sources, top_k, filters)` |
| `mode: "synthesized"` | `rag_get_synthesized_results(username, query, sources, top_k, synthesis_params)` |
| discovery | `rag_discover_resources(_atlas_user)` |

`UnifiedRAGService.query_rag` gains a `query: str` and `mode` param; it stops
taking `messages` and stops calling `_extract_query_text`. HTTP and MCP both
flow through one method with one response shape.

---

## How ATLAS-UI Uses v2 (Tool-Call Flow)

The agent loop already has an `atlas_rag_query` pseudo-tool. v2 makes it first-class:

1. **LLM decides the query.** In agent mode the LLM calls `atlas_rag_query`
   with `{query, data_sources, mode}` — the same way it calls any MCP tool.
2. **ATLAS-UI sends the explicit query** to `/api/v2/rag/query`, not the
   conversation.
3. **`mode: "raw"` (default)** returns evidence chunks as the tool result. The
   LLM reasons over them alongside other tool results — no short-circuit.
4. **`mode: "synthesized"`** returns an answer the LLM can quote or refine.

This removes the `call_with_rag` / `call_with_rag_and_tools` /
`_build_rag_completion_response` special-casing: RAG becomes a tool that
returns a `ToolResult`, and the existing agent loop handles it like any other
tool.

### Non-agent (RAG-only) mode
When the user selects data sources without agent mode, ATLAS-UI calls
`/api/v2/rag/query` with `mode: "synthesized"` using the user's message as
`query`, then either returns the answer (preserving v1 behavior) or streams it
through the LLM with the citations as context. This is a product decision —
see Open Questions.

---

## Request / Response Examples

### Example 1: Agent tool call (raw retrieval)

```http
POST /api/v2/rag/query?as_user=alice%40corp.com
Authorization: Bearer eyJ...
Content-Type: application/json

{
  "query": "What is the maximum PTO carryover per year?",
  "corpora": ["company-policies"],
  "mode": "raw",
  "top_k": 5
}
```

```json
{
  "query": "What is the maximum PTO carryover per year?",
  "mode": "raw",
  "results": {
    "hits": [
      {
        "document_ref": 1,
        "filename": "pto-policy.pdf",
        "title": "PTO Policy",
        "citation": "Corp. (2024). PTO Policy. [1]",
        "sections": [
          {"section_ref": 1, "text": "Employees may carry over up to 240 hours...", "relevance": 0.91}
        ]
      }
    ],
    "stats": {"total_found": 1, "top_k": 5}
  },
  "metadata": {"response_time_ms": 128, "corpora_searched": ["company-policies"]}
}
```

### Example 2: Synthesized answer

```http
POST /api/v2/rag/query?as_user=alice%40corp.com
Authorization: Bearer eyJ...
Content-Type: application/json

{
  "query": "Summarize our deployment process",
  "corpora": ["technical-docs"],
  "mode": "synthesized",
  "top_k": 4
}
```

```json
{
  "query": "Summarize our deployment process",
  "mode": "synthesized",
  "results": {
    "answer": "Deployment uses a blue-green strategy via the CI pipeline...",
    "citations": [
      {"document_ref": 1, "filename": "deployment.md", "citation": "Eng. (2025). Deployment Guide. [1]"}
    ]
  },
  "metadata": {"response_time_ms": 305, "corpora_searched": ["technical-docs"], "fallback_used": false}
}
```

### Example 3: Error — empty query

```http
POST /api/v2/rag/query?as_user=alice%40corp.com
{"query": "", "corpora": ["technical-docs"]}
```

```json
{"detail": "query must be a non-empty string", "status": 400}
```

---

## Migration Considerations

### Backward compatibility
- **Keep v1 endpoints alive** (`/api/v1/rag/completions`,
  `/api/v1/discover/datasources`) during the transition. ATLAS-RAG serves both;
  ATLAS-UI negotiates per source via a config flag.
- **Server-side capability advertisement.** Discovery response gains an optional
  `api_version: "v2"` field per source. ATLAS-UI uses v2 when advertised, v1
  otherwise. This lets mixed backends coexist.

### ATLAS-UI changes (phased)
1. **Phase 1 — Add v2 client. (done)** New `AtlasRAGClientv2` (or extend
   `AtlasRAGClient` with a `query_v2()` method) that hits
   `/api/v2/rag/query` with an explicit `query` + `mode`. `UnifiedRAGService`
   gains a `query` param; `_extract_query_text` is removed from the v2 path.
2. **Phase 2 — Route by capability. (partial)** `app_factory` / config picks v2 when the
   source advertises `api_version: "v2"`.
3. **Phase 3 — Tool-first RAG.** Deprecate `call_with_rag` /
   `call_with_rag_and_tools` special-casing; route RAG through the agent loop's
   tool executor as `atlas_rag_query` with `mode: "raw"` by default. The
   `is_completion` short-circuit is removed (or retained only for v1 fallback).
4. **Phase 4 — Retire v1.** Once all configured sources are v2, remove the v1
   client and the `messages`-based path.

### Mock
`mocks/atlas-rag-api-mock/main.py` gains `POST /api/v2/rag/query` reusing the
existing `search_corpus_for_references` for `raw` mode and
`_compose_assistant_content` for `synthesized` mode. The v1 endpoint stays for
the transition.

### Compliance / authorization
`_ensure_source_query_allowed` and compliance filtering move to the v2 path
unchanged — authorization is per-server, not per-API-version. The
`as_user` impersonation and `strip_domain` behavior carry over verbatim.

### Telemetry
The `rag.query` span already hashes the query text (`hash_short(query_text)`).
v2 keeps this; the explicit `query` makes the span cleaner (`message_count`
goes away, `mode` is added as a span attribute).

### Tests to update
- `test_atlas_rag_client.py` — add v2 query tests; keep v1 tests until Phase 4.
- `test_atlas_rag_integration.py` — `query_rag` signature gains `query`/`mode`.
- `test_rag_mcp_aggregator.py`, `test_rag_mcp_service.py` — already query-based;
  align response shape to v2.
- `test_rag_tools_is_completion.py` — the short-circuit behavior changes.
- PR validation: `test_pr276_rag_completions.sh` references
  `_build_rag_completion_response`; add a v2 validation script.

---

## Open Questions

1. **RAG-only mode behavior.** When the user selects data sources but is not in
   agent mode, should ATLAS-UI return the `synthesized` answer directly (v1
   behavior) or feed `raw` evidence to its LLM? The former is lower latency;
   the latter keeps one answer path. Recommend: default `synthesized` for
   non-agent, `raw` for agent — but confirm with the product direction
   (both in-chat and the Agent Portal are now first-class agent surfaces,
   per `AGENTS.md`).

2. **Streaming.** v1 has a `stream` field (unused by the current client, which
   always sends `false`). Should v2 `synthesized` mode support SSE streaming
   of the answer? `raw` mode has nothing to stream (it returns chunks).

3. **Multi-corpus synthesis.** When `corpora` is a list across different
   backends, who synthesizes — the RAG backend (per-corpus, then merged by
   ATLAS-UI) or ATLAS-UI's LLM? v1 merges with `"\n\n---\n\n"`. v2 should define
   whether cross-corpus synthesis is the server's job or the caller's.

4. **`mode` default.** Proposed default is `raw` (tool-first). But the existing
   non-agent RAG UX returns a synthesized answer. Should the default differ by
   caller (agent vs non-agent), or always be explicit?

5. **Filters schema.** `filters` is server-defined in v2. Should ATLAS-UI
   standardize a minimal filter set (date range, doc type) so the UI can expose
   them, or keep it opaque and let the server decide?

6. **Version negotiation.** Is `api_version` in the discovery response enough,
   or do we need a `/api/v2/version` / capability endpoint? Discovery is
   already called at startup, so piggybacking on it is cheapest.

7. **MCP transport.** Should v2 also define an MCP tool shape, or keep the
   existing `rag_get_*` tools and only standardize the response envelope? The
   MCP tools already take `query`; the main work is normalizing the response to
   the v2 `{results, metadata}` shape.

8. **Retirement timeline for v1.** How long must v1 stay available for external
   RAG backends that ATLAS-UI does not control?

---

## Related

- [External RAG API (v1 contract)](../admin/external-rag-api.md)
- [RAG Completions vs Raw Results](../admin/external-rag-api.md#rag-completions-vs-raw-results)
- `atlas/modules/rag/atlas_rag_client.py` — v1 HTTP client
- `atlas/domain/unified_rag_service.py` — v1 orchestrator (`query_rag`, `_extract_query_text`)
- `atlas/domain/rag_mcp_service.py` — MCP RAG aggregator (already query-based)
- `atlas/modules/mcp_tools/mcp_execution.py` — `atlas_rag_query` pseudo-tool
- `mocks/atlas-rag-api-mock/main.py` — v1 mock server