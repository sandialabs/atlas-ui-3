# RAG → MCP Migration Plan

This document outlines how to migrate the current RAG approach to MCP-based RAG servers, aligned with the MCP v2 tool contract in `v2_mcp_note.md` and current backend/UI patterns. No code changes here—this is the plan and interface contracts plus a concrete file-by-file update plan.

## Goals and scope

- Replace the existing RAG client/service with MCP RAG servers (one server per provider/domain) while preserving current UX.
- Enforce per-user and per-data-source authorization via groups and server-side checks.
- Standardize three RAG tools: discover resources, raw search, synthesized results (optional).
- Always include a `username` parameter; backend injects the authenticated user, not the model.
- UI shows servers and their data sources with easy toggles; admin can configure and live-reload servers.
- Support zero/low-trust auth: users can pass tokens directly without trusting the chat app with long-term secrets.

Non-goals (for first phase):
- Cross-server unified ranking beyond simple merge-and-rerank is out-of-scope for phase 1.
- Full OAuth/PKCE broker is optional; we’ll begin with capability tokens and API keys.

## Requirements recap

- RAG MCP config similar to existing MCP servers with authorized groups; only visible to allowed users.
- Required tools per RAG server:
  - discover resources
  - get raw search results
- Optional tool per RAG server:
  - get synthesized results (recommended)
- Every tool takes `username` and the backend injects it (v2 note §4).
- Each RAG server can expose many data sources with different auth/visibility.
- UI shows servers and their data sources with toggles.
- Users may supply custom authorization material so they don’t have to trust the chat app.
- Admin can add/edit servers in dashboard and live-reload.

## Tool contracts (MCP v2-aligned)

All tools must accept `username: string` (injected by backend) and return v2-style results/artifacts/display (see `v2_mcp_note.md`). Sensitive params (see Security) must never be injected into prompts or logs.

1) rag_discover_resources
- Inputs:
  - username: string (required)
  - filters?: { types?: string[], tags?: string[], search?: string, page?: int, page_size?: int }
- Output `results`:
  - resources: Array<{
      id: string,
      name: string,
      sourceType: string,      // e.g., "notion", "s3", "confluence"
      sourceId?: string,       // provider-native ID
      authRequired: boolean,
      authMode: "username" | "api_key" | "bearer" | "oauth" | "capability" | "none",
      groups?: string[],       // visibility groups for this resource
      scopes?: string[],
      lastIndexed?: string,    // ISO8601
      counts?: { docs?: number, chunks?: number }
    }>
  - paging?: { page: number, page_size: number, total?: number }
- meta_data: provider, elapsed_ms, version
- artifacts: optional JSON dump for debugging

2) rag_get_raw_results
- Inputs:
  - username: string (required)
  - query: string (required)
  - sources: string[] (resource IDs; required)
  - top_k?: number (default 8)
  - filters?: { date_from?: string, date_to?: string, tags?: string[], owners?: string[] }
  - ranking?: { rerank?: boolean, model?: string }
- Output `results`:
  - hits: Array<{
      id: string,
      score: number,
      snippet?: string,
      chunk?: string,
      title?: string,
      uri?: string,
      resourceId: string,
      sourceId?: string,
      provenance?: object,
      timestamp?: string
    }>
  - stats?: { total_found?: number, top_k: number, elapsed_ms: number }
- meta_data: retrieval stats, provider
- artifacts: optional per-source raw payloads

3) rag_get_synthesized_results (optional but recommended)
- Inputs:
  - username: string (required)
  - query: string (required)
  - sources: string[] (resource IDs)
  - top_k?: number
  - synthesis_params?: { model?: string, style?: string, max_chars?: number }
  - provided_context?: { hits?: any[] } // to allow client-provided selection
- Output `results`:
  - answer: string
  - citations?: Array<{ uri?: string, title?: string, resourceId?: string, offsets?: any }>
  - limits?: { truncated?: boolean, reason?: string }
- meta_data: model, tokens, latency
- artifacts: html report, full citation bundle (optional)

Common error codes in `results.error` or meta_data:
- unauthorized_user, unauthorized_source, rate_limited, invalid_source, backend_unavailable, token_expired

## Configuration design

Extend existing `mcp.json` to register RAG servers with additional optional UI hints and default selection. Example:

```json
{
  "docsRag": {
    "groups": ["users", "mcp_basic"],
    "is_exclusive": false,
    "description": "Company docs RAG",
    "enabled": true,
    "ui": { "icon": "book", "order": 10, "defaultSelected": true },
    "dataSources": [
      {
        "id": "handbook",
        "name": "Employee Handbook",
        "groups": ["users"],
        "authMode": "none",
        "scopes": ["read"],
        "defaultSelected": true
      },
      {
        "id": "legal",
        "name": "Legal Docs",
        "groups": ["legal", "admin"],
        "authMode": "capability",
        "scopes": ["read"],
        "defaultSelected": false
      }
    ]
  }
}
```

Notes:
- `groups` controls visibility of the server; `dataSources[].groups` can further restrict individual sources.
- Actual authoritative resource list should still come from `rag_discover_resources` at runtime.

## Backend design and changes

High-level: Replace the current `rag_client` path with an MCP-backed aggregator service that queries all authorized RAG servers for discovery/search.

New/updated components:

1) RAG MCP Aggregator Service (new)
- File: `backend/domain/rag_mcp_service.py` (new)
- Responsibilities:
  - Given a `username`, get authorized MCP servers (via MCP manager + `groups`).
  - Introspect those servers to see if they implement `rag_discover_resources`/`rag_get_raw_results`/`rag_get_synthesized_results`.
  - Call `rag_discover_resources` across servers; merge and normalize resource records; tag with server ID.
  - For search/synthesis, route calls to selected servers based on resource IDs; merge results and optionally rerank (simple merge phase 1).
  - Enforce per-source authorization using `groups` and any server-side deny (bubble up `unauthorized_source`).
  - Provide structured errors and metrics in `meta_data`.

2) App Factory wiring
- File: `backend/infrastructure/app_factory.py`
- Add: `get_rag_mcp_service()` that returns the aggregator; deprecate `get_rag_client()` usage for discovery in config route.
- Ensure MCP manager is available and supports listing tools per server.

3) Config route updates
- File: `backend/routes/config_routes.py`
- Replace `rag_client.discover_data_sources(current_user)` with `rag_mcp_service.discover_data_sources(current_user)` which aggregates across MCP RAG servers.
- Response shape: either keep the existing `data_sources` array shape or enhance to `{ servers: [{server, sources: [...]}] }`. Phase 1: keep the existing `data_sources` key but include `server` on each item to minimize UI churn; add a new `rag_servers` field for richer UI if needed.

4) Tool invocation pipeline
- Confirm existing username injection from `v2_mcp_note.md §4` in `ChatService._handle_tools_with_updates`.
- Add support for “sensitive” parameters channel:
  - If tool schema declares `user_token|api_key|bearer|capability_token`, mark them as sensitive.
  - Do not include sensitive values in model prompts or logs.
  - Store only ephemeral in session (memory) for the call; never persist.

5) Admin: server registry and live reload
- Files: config manager + MCP manager and admin API routes.
- Add a small in-memory/DB-backed registry for MCP servers (already via `mcp.json`).
- Add file watcher or explicit POST endpoint `POST /api/admin/mcp/reload` to restart/reload servers.
- Broadcast `server_registry_updated` over WS; frontend re-runs discovery.

6) User connection tokens (optional auth)
- New endpoints (phase 2): `POST /api/rag/connections/{server}` to store ephemeral session-scoped tokens; or rely on tool calls that accept tokens directly per invocation.
- Prefer capability tokens the server can validate; avoid storing long-term secrets.

7) Logging / audit
- Ensure per-call logs include `username`, `server`, `resources`, and auth outcome, but redact tokens.

## Frontend design and changes

Panel updates:
- Add a "RAG Providers" panel listing authorized servers (from `/api/config` → `authorized_servers` filtered) with expand/collapse.
- For each server, call `rag_discover_resources` and display resources with toggles and connection status badges (connected/action required/locked).
- Persist selection per conversation (and optionally as user defaults).

API shaping:
- `/api/config` continues to return `data_sources`; enhance to include `rag_servers: [{ server, displayName, sources: [...] }]` for the new UI.
- Search calls: include `selectedServers` + `selectedResourceIds` in the message metadata so the backend routes tool calls accordingly.

Canvas:
- Use MCP v2 `artifacts` and `display` to auto-open HTML reports or bundles returned by synthesis tools.

## Security model (reduced trust)

- Username injection: backend overwrites any `username` parameter with the authenticated user (already planned/implemented per v2 note).
- Sensitive params: allow tools to declare `user_token`, `api_key`, `bearer`, `oauth_token`, or `capability_token`. The backend:
  - Never injects these into model prompts.
  - Never logs or persists them; only passed runtime to the tool.
  - Optionally issues short-lived capability tokens for file downloads or iframe dashboards.
- Per-source authorization: combine `groups` filtering (backend) with server-side checks (e.g., provider ACLs).

## Live reload

- MCP manager exposes a reload capability that:
  - Re-reads `mcp.json` and differences servers (start/stop as needed).
  - For stdio servers: graceful stop/start.
  - Emits a WS event so the UI refreshes discovery.
- Admin dashboard: "Reload MCP Servers" button; show last reload time and server health.

## Migration plan and milestones

Phase 0: Prep
- Confirm username injection and v2 artifacts/display behavior.
- Add feature flag `FEATURE_RAG_MCP_ENABLED` (config/app settings) to gate UI/route behavior.

Phase 1: Discovery
- Implement aggregator service and swap `/api/config` discovery to MCP-backed when flag on.
- Provide minimal UI to show servers and toggle resources.

Phase 2: Search
- Wire chat pipeline to call `rag_get_raw_results` with selected resources.
- Merge results across servers (simple merge + limit by `top_k`).

Phase 3: Synthesis
- Prefer server-side `rag_get_synthesized_results` if available; otherwise client-side synthesis using raw results.
- Add canvas rendering for reports/citations.

Phase 4: Admin + Live Reload
- Admin CRUD for MCP servers, data source hints, groups; add reload endpoint and UI.
- Health status and error surfaces.

Phase 5: Hardening
- Sensitive param handling, capability tokens, audit logs, rate limits.

## Code changes by file (plan)

- New: `backend/domain/rag_mcp_service.py`
  - `discover_data_sources(username: str) -> list[dict] | { servers: [...] }`
  - `search_raw(username: str, query: str, sources: list[str], top_k: int, filters: dict) -> dict`
  - `synthesize(username: str, query: str, sources: list[str], params: dict, provided_context?: dict) -> dict`

- Update: `backend/infrastructure/app_factory.py`
  - Provide `get_rag_mcp_service()`; ensure MCP manager injection.
  - Keep `get_rag_client()` temporarily for fallback; deprecate in config route under flag.

- Update: `backend/routes/config_routes.py`
  - Swap to `rag_mcp_service.discover_data_sources(current_user)`.
  - Optionally add `rag_servers` key to the returned config payload.

- Update: Chat service/tool handler (where v2 username injection lives)
  - Confirm overwrite of `username` and add sensitive param shielding.
  - Ensure file URL rewrite (`filename|file_names`) remains intact.

- New (optional sample): `backend/mcp/rag_example/main.py`
  - Implements the three tools; uses `artifacts` and `display` per v2; supports `capability_token`.

- Update: Admin API/routes (new endpoint)
  - `POST /api/admin/mcp/reload` to trigger reload; return status and server list.

- Update: Config docs
  - `docs/configuration.md` → add RAG MCP notes and `FEATURE_RAG_MCP_ENABLED` flag.
  - `docs/mcp-development.md` → add RAG server guidance and the three-tool contract.

## Data contracts for UI

`/api/config` additions (proposed):

```jsonc
{
  "rag_servers": [
    {
      "server": "docsRag",
      "displayName": "Docs RAG",
      "icon": "book",
      "sources": [
        { "id": "handbook", "name": "Employee Handbook", "authRequired": false, "selected": true },
        { "id": "legal", "name": "Legal Docs", "authRequired": true, "selected": false, "status": "action_required" }
      ]
    }
  ]
}
```

Message metadata (example) when user runs a query:

```jsonc
{
  "rag": {
    "selectedServers": ["docsRag"],
    "selectedResourceIds": ["handbook", "legal"],
    "top_k": 8
  }
}
```

## Security and privacy details

- Do not trust LLM-provided identities; always overwrite with injected `username`.
- Sensitive auth material path:
  - Accept `user_token`, `api_key`, `bearer`, `oauth_token`, or `capability_token` as tool params.
  - Backend marks them sensitive; not included in prompts or logs; only forwarded to the tool process.
  - Prefer short-lived capability tokens issued by the provider/server.
- Audit: record who accessed what resource and when (no secrets), per-source allow/deny decisions, and error codes.

## Risks and mitigations

- Server heterogeneity: enforce the uniform three-tool contract; feature-detect at runtime (ListTools) and degrade gracefully.
- Latency across multiple servers: add timeouts and parallel fan-out; return partial results with per-source errors in `meta_data`.
- Token lifecycle: add clear UX for expired/invalid tokens with reconnect flow.

## Open questions

- Should discovery return hierarchical resources (e.g., spaces/projects) vs flat? Start flat; allow optional hierarchy hints.
- Should we support per-server default synthesis models? Yes, via `synthesis_params` defaults in discovery meta.
- How do we map legacy `data_sources` shape to per-server results? Phase 1: include `server` on items; Phase 2: move UI fully to `rag_servers`.

---

References:
- `v2_mcp_note.md` – MCP v2 artifacts/display + username injection
- `docs/mcp-development.md` – server creation and configuration patterns
- `docs/configuration.md` – `mcp.json` server registry and group authorization

## Code sharing strategy (pragmatic and decoupled)

What to reuse (stable infra):
- MCP server registry/manager, group-based visibility, and filtering already used in `config_routes.py`.
- Tool invocation pipeline: username injection and file URL rewrites (v2 note §4), v2 output normalization (artifacts/display vs legacy arrays).
- Admin + lifecycle: server health/start/stop/reload and dashboard plumbing.
- Frontend canvas + artifact viewers (for RAG HTML reports/citations).
- Logging/metrics patterns (structured logs with username + simple timing stats).

Share with caution (behind thin interfaces):
- Minimal RAG DTOs/validators for the three tools in `backend/interfaces/rag_contract.py` with a `contract_version: "rag-tools-v1"`.
- Sensitive-parameter handling (generic): central logic to prevent secrets entering prompts/logs; ephemeral pass-through to tools.
- Small aggregation utilities (merge + limit + optional rerank) without provider-specific coupling.

Keep separate (avoid tight coupling):
- RAG aggregator orchestration in `backend/domain/rag_mcp_service.py` (fan-out, mapping resourceId→server, merge, per-source error handling).
- Provider/server implementations under `backend/mcp/<provider>`; no imports back into app internals (process boundary anyway).
- Reranking/advanced synthesis policies as optional plug-ins behind the aggregator.

Foldering suggestions:
- Shared contracts/types: `backend/interfaces/rag_contract.py`
- Aggregator (domain logic): `backend/domain/rag_mcp_service.py`
- Optional shared bits for our MCP servers: `backend/mcp/_common/` (internal only)

Versioning & compatibility:
- Include `contract_version` in outputs and `meta_data`.
- Aggregator tolerates missing `rag_get_synthesized_results` and falls back.
- Keep v2 artifact/display normalization in one place so servers can evolve without UI churn.

Tests at the boundary:
- Contract tests for discover/raw/synthesize with username injection and sensitive params.
- Aggregator tests: multi-server discovery, per-source ACL enforcement, partial failure handling, result merging.
- Admin reload smoke tests.

## Separate configuration file for RAG MCP servers

To keep generic tool MCP servers separate from RAG providers, introduce a distinct configuration file: `rag-mcp.json`.

- Purpose: List only RAG-oriented MCP servers and optional UI/data-source hints.
- Location and search order (proposed):
  1. `config/overrides/rag-mcp.json`
  2. `config/defaults/rag-mcp.json`
  3. fallback: embed minimal defaults or rely entirely on runtime discovery from servers
- Admin dashboard manages both `mcp.json` (generic tools) and `rag-mcp.json` (RAG providers).
- Live reload reloads both registries; clients receive `server_registry_updated` and refresh discovery.

Example `rag-mcp.json`:

```json
{
  "docsRag": {
    "groups": ["users", "mcp_basic"],
    "description": "Company docs RAG",
    "enabled": true,
    "ui": { "icon": "book", "order": 10, "defaultSelected": true },
    "dataSources": [
      { "id": "handbook", "name": "Employee Handbook", "groups": ["users"], "authMode": "none", "defaultSelected": true },
      { "id": "legal", "name": "Legal Docs", "groups": ["legal", "admin"], "authMode": "capability", "defaultSelected": false }
    ]
  },
  "notionRag": {
    "groups": ["users"],
    "description": "Notion workspace RAG",
    "enabled": false
  }
}
```

Notes:
- `mcp.json` continues to define generic tool servers; `rag-mcp.json` constrains the set of RAG providers shown in the UI and used by the aggregator.
- At runtime, the authoritative resource list still comes from each server’s `rag_discover_resources` to reflect per-user ACLs and current indexing state.
