# RAG Configuration

Last updated: 2026-08-26

This guide explains how to configure RAG (Retrieval-Augmented Generation) in Atlas UI.

## Overview

Atlas UI supports multiple RAG backends through a unified configuration file (`rag-sources.json`). This allows you to configure multiple RAG sources of different types in a single place.

### Feature Flag Semantics

RAG is controlled by the `FEATURE_RAG_ENABLED` feature flag. The `atlas_rag` pseudo-server/tool exposure is separately controlled by `FEATURE_ATLAS_RAG_TOOLS_ENABLED`.

- When `FEATURE_RAG_ENABLED=false`, the backend skips RAG service initialization and does not load `rag-sources.json`. The `/api/config` response will show `features.rag=false` and will return empty `rag_servers` and `data_sources`.
- When `FEATURE_RAG_ENABLED=true`, the backend loads `rag-sources.json`, initializes HTTP/general RAG services, and exposes discovered sources to the UI via `/api/config`.
- When both `FEATURE_RAG_ENABLED=true` and `FEATURE_ATLAS_RAG_TOOLS_ENABLED=true`, the backend also exposes the `atlas_rag` pseudo-server/tools and MCP-backed RAG discovery paths. The dedicated flag defaults to `false` so production can enable general RAG without automatically surfacing atlas_rag tools.

### Best-Effort Discovery and Retrieval

RAG discovery is best-effort. If one configured RAG source is offline or misconfigured, other sources can still be discovered and used. Expect partial results when some sources fail.

**Supported RAG Source Types:**

| Type | Description |
|------|-------------|
| `http` | HTTP REST API RAG backends (like ATLAS RAG API) |
| `mcp` | MCP-based RAG servers |

## Quick Start

1. Enable RAG in your `.env` file:

```bash
FEATURE_RAG_ENABLED=true
FEATURE_ATLAS_RAG_TOOLS_ENABLED=true
```

2. Configure your RAG sources in `config/rag-sources.json`:

```json
{
  "atlas_rag": {
    "type": "http",
    "display_name": "ATLAS RAG",
    "url": "${ATLAS_RAG_URL}",
    "bearer_token": "${ATLAS_RAG_BEARER_TOKEN}",
    "groups": ["users"],
    "compliance_level": "Internal"
  }
}
```

3. Set environment variables for secrets:

```bash
ATLAS_RAG_URL=https://rag-api.example.com
ATLAS_RAG_BEARER_TOKEN=your-secret-token
```

## Configuration File: rag-sources.json

The `rag-sources.json` file defines all RAG backends. It supports environment variable substitution using `${ENV_VAR}` syntax.

### File Locations

Configuration files are loaded in order of priority:
1. `config/rag-sources.json` (highest priority, user config, not in git)
2. `atlas/config/rag-sources.json` (package defaults)

### HTTP RAG Source Configuration

For external HTTP REST API RAG backends:

```json
{
  "atlas_rag": {
    "type": "http",
    "display_name": "ATLAS RAG",
    "description": "External ATLAS RAG API for document retrieval",
    "icon": "database",
    "url": "${ATLAS_RAG_URL}",
    "bearer_token": "${ATLAS_RAG_BEARER_TOKEN}",
    "default_model": "openai/gpt-oss-120b",
    "top_k": 4,
    "timeout": 60.0,
    "groups": ["users"],
    "compliance_level": "Internal",
    "enabled": true
  }
}
```

**HTTP Source Options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `type` | Yes | - | Must be `"http"` for REST API sources |
| `url` | Yes | - | Base URL of the RAG API (supports `${ENV_VAR}`) |
| `bearer_token` | No | `null` | Bearer token for authentication (supports `${ENV_VAR}`) |
| `display_name` | No | source key | Name shown in UI |
| `description` | No | `null` | Description for the source |
| `icon` | No | `"database"` | Icon name for UI |
| `default_model` | No | `"openai/gpt-oss-120b"` | Model for RAG queries |
| `top_k` | No | `4` | Number of documents to retrieve |
| `timeout` | No | `60.0` | Request timeout in seconds |
| `strip_domain` | No | `false` | Strip `@domain` from usernames before sending to RAG API (e.g. `user@corp.com` → `user`) |
| `api_version` | No | `"v1"` | Which contract the backend speaks: `"v1"` (conversation → completion) or `"v2"` (explicit query → answer + references). See [API v2](#api-v2-query-oriented-interface) |
| `default_mode` | No | `"synthesized"` | v2 only. Client-side knob: how Atlas consumes the response. `"synthesized"` uses the backend's answer verbatim; `"raw"` builds an evidence block from references for Atlas' own LLM. Never sent on the wire. |
| `discovery_endpoint` | No | per `api_version` | Override the discovery path |
| `query_endpoint` | No | per `api_version` | Override the query path |
| `groups` | No | `[]` | Required groups for access |
| `compliance_level` | No | `null` | Compliance level restriction |
| `enabled` | No | `true` | Whether this source is active |

### MCP RAG Source Configuration

For MCP-based RAG servers that expose `rag_discover_resources` tool:

```json
{
  "corporate_cars": {
    "type": "mcp",
    "display_name": "Corporate Cars",
    "description": "Fleet RAG server for corporate vehicle data",
    "icon": "car",
    "command": ["python", "mcp/corporate_cars/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "compliance_level": "SOC2"
  }
}
```

**MCP Source Options:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `type` | Yes | - | Must be `"mcp"` for MCP servers |
| `command` | * | - | Command to run (for stdio transport) |
| `url` | * | - | URL for HTTP/SSE transport |
| `cwd` | No | `null` | Working directory for command |
| `transport` | No | auto | Transport type: `stdio`, `http`, `sse` |
| `auth_token` | No | `null` | Auth token for MCP server |
| `display_name` | No | source key | Name shown in UI |
| `groups` | No | `[]` | Required groups for access |
| `compliance_level` | No | `null` | Compliance level restriction |

\* Either `command` or `url` is required for MCP sources.

### Username Domain Stripping

Some RAG backends expect plain usernames (e.g. `alice`) rather than full email addresses (e.g. `alice@corp.com`). The `strip_domain` option handles this automatically.

When `strip_domain` is set to `true`, Atlas strips the `@domain` portion from the username before sending it in the `as_user` query parameter to the RAG API. This affects both discovery and query requests.

**Example:**

```json
{
  "atlas_rag": {
    "type": "http",
    "url": "${ATLAS_RAG_URL}",
    "bearer_token": "${ATLAS_RAG_BEARER_TOKEN}",
    "strip_domain": true
  }
}
```

With this configuration, if the authenticated user is `alice@corp.com`, the discovery request becomes:

```
GET /discover/datasources?as_user=alice
```

instead of:

```
GET /discover/datasources?as_user=alice@corp.com
```

**Behavior details:**
- Defaults to `false` (full email is sent as-is)
- If the username contains no `@`, it is sent unchanged regardless of this setting
- Only the portion before the first `@` is kept (e.g. `user@sub@corp.com` → `user`)

## API Contract (HTTP Sources)

HTTP RAG sources speak one of two contracts, selected by the `api_version`
field on the source config (`"v1"` default, `"v2"` for the query-oriented
interface). Both share the same authentication (`Bearer` token), `as_user`
impersonation, group/compliance authorization and `strip_domain` behaviour --
the version decides the request/response shape, not who may read what.

### v1 Contract

Used unless a source sets `"api_version": "v2"`. v1 ships the full
conversation to the RAG backend, which derives the query from the last user
message.

#### Discovery (v1)

```
GET /api/v1/discover/datasources?role=read&as_user={user_email}
Authorization: Bearer {token}
```

**Response** (a bare list of `DataSource`):
```json
[
  {"id": "technical-docs", "label": "Technical Documentation", "compliance_level": "Internal", "description": "Engineering docs covering API auth, database schema, and deployment"},
  {"id": "company-wiki", "label": "Company Wiki", "compliance_level": "Public", "description": "Public company knowledge base"}
]
```

(The client also accepts a `{"data_sources": [...]}` envelope for backends
that still wrap the list.)

#### Query (v1)

```
POST /api/v1/rag/completions?as_user={user_email}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "messages": [{"role": "user", "content": "What is our API architecture?"}],
  "stream": false,
  "corpora": ["technical-docs"]
}
```

`corpora` may be a single string or a list of strings.

**Response:**
```json
{
  "message": {"role": "assistant", "content": "Based on the documentation..."},
  "metadata": {
    "response_time": 1,
    "references": [
      {
        "citation": "[1] \"API Auth Guide\", tech-001.txt",
        "reference": "API Auth Guide, tech-001.txt",
        "document_ref": 1,
        "filename": "tech-001.txt",
        "sections": [
          {"section_ref": 1, "text": "Our API uses OAuth 2.0 with JWT tokens...", "relevance": 0.95}
        ]
      }
    ]
  }
}
```

v1 always returns a completion (`is_completion=True`); Atlas renders the
`message.content` directly and shows the `metadata.references` as citations.

## API v2: Query-Oriented Interface

Last updated: 2026-08-26

v2 exists because v1 ships the **entire conversation** to the RAG backend on
every query, even though the backend only uses the last user message. v2 sends
an explicit `query` string and a `search_kwargs` block -- and nothing else.

The v0.8.0 schema defines a single response shape: a synthesized `response`
string plus the `references` behind it. There is **no `mode` field on the
wire**. `mode` is an Atlas client-side knob that decides how to consume the
response (see [What changes in Atlas UI](#what-changes-in-atlas-ui)).

Set `"api_version": "v2"` on an HTTP source to use it. v1 remains the default,
and both contracts share the same authentication, `as_user` impersonation,
group/compliance authorization and `strip_domain` behaviour — the version
decides the request shape, not who may read what.

```json
{
  "atlas_rag": {
    "type": "http",
    "url": "${ATLAS_RAG_URL}",
    "bearer_token": "${ATLAS_RAG_BEARER_TOKEN}",
    "api_version": "v2",
    "default_mode": "synthesized",
    "groups": ["users"]
  }
}
```

### Discovery (v2)

```
GET /api/v2/discover/datasources?role=read&as_user={user_email}
Authorization: Bearer {token}
```

Same `DataSource` response shape as v1 (a bare list):

```json
[
  {"id": "technical-docs", "label": "Technical Documentation", "compliance_level": "Internal", "description": "..."}
]
```

### Query (v2)

```
POST /api/v2/rag/query?as_user={user_email}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | Yes | - | The specific question to ask. Non-empty. |
| `corpora` | string \| string[] | Yes | - | Corpus id(s) to search |
| `search_kwargs` | object | No | server defaults | Search behaviour knobs (see below) |

`search_kwargs` fields (all optional, each has a server default):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rerank` | bool | `true` | Whether to rerank search results |
| `rerank_model_name` | string | `dev/BAAI/bge-reranker-v2-m3` | Reranker model |
| `top_k_vector` | int | `5` | Semantic-search result count |
| `top_k_full_text` | int | `5` | Full-text result count |
| `top_k_final` | int | `5` | Final combined result count |
| `rank_strategy` | `"weighted"` \| `"rrf"` | `"weighted"` | How ranks are combined |
| `threshold` | number | `0.75` | Relevance threshold [0-1] |
| `expanded_window` | [int, int] | `[0, 0]` | Extra chars left/right of each chunk |

```json
{
  "query": "What is the maximum PTO carryover per year?",
  "corpora": ["company-policies"],
  "search_kwargs": {"top_k_final": 5, "rerank": true}
}
```

**Response** (always this shape — a synthesized answer plus references):

```json
{
  "response": "Employees may carry over up to 240 hours. [1]",
  "metadata": {
    "response_time": 2,
    "references": [
      {
        "filename": "pto-policy.pdf",
        "sections": [
          {"text": "Employees may carry over up to 240 hours...", "relevance": 0.91}
        ],
        "reference": "PTO Policy, pto-policy.pdf"
      }
    ]
  }
}
```

| Response field | Type | Description |
|----------------|------|-------------|
| `response` | string | The synthesized RAG answer |
| `metadata.response_time` | int | Time to generate, in **seconds** |
| `metadata.references` | array \| null | References used to generate the response |
| `references[].filename` | string \| null | Filename of the source document |
| `references[].sections` | array | Relevant snippets from the document |
| `references[].sections[].text` | string | Snippet text |
| `references[].sections[].relevance` | number | Cosine similarity score [0-1] |
| `references[].reference` | string | Human-readable source label |

An empty `query` must be rejected with `400`; `403` and `404` mean the same
things they do on v1.

### What changes in Atlas UI

- **`mode` is client-side, not on the wire.** The v0.8.0 backend always
  returns a synthesized `response` string plus `references`. Atlas decides
  how to use it: `mode: "raw"` builds an evidence block from the reference
  snippets (`is_completion=false`, so the configured LLM reasons over them);
  `mode: "synthesized"` uses the `response` verbatim (`is_completion=true`,
  short-circuiting the LLM, matching v1 behaviour).
- **`top_k` maps to `search_kwargs.top_k_final`.** The source config's `top_k`
  is forwarded as `search_kwargs.top_k_final` when the caller does not supply
  an explicit `search_kwargs` dict.
- **`raw` evidence carries `[N]` document markers**, the same citation form
  the UI already parses — a v2 raw answer cites like a v1 one. Because the
  v0.8.0 schema has no `document_ref`, references are numbered sequentially.
- **The `atlas_search` agent tool always asks for `raw`** (it was
  `atlas_rag_query` before #855, which took an optional `mode`). The model
  reasons over the evidence alongside other tool results instead of relaying a
  backend-written answer. On v1 sources this makes no difference — v1 always
  synthesizes.
- **`atlas_search`'s optional `max_results` and `depth` map onto
  `search_kwargs`.** `max_results` becomes `top_k_final` (clamped to 50);
  `depth` is `quick` / `standard` / `deep`, setting `rerank`, `top_k_vector`,
  `top_k_full_text` and `expanded_window`. **v1 sources ignore both** — the v1
  request body has no retrieval knobs — so a v1 source returns its configured
  behaviour rather than an error. When a caller supplies `search_kwargs`, the
  source's configured `top_k` is folded in as the `top_k_final` default first,
  so a `depth`-only call does not silently drop it.
- **Nothing but the query leaves the process.** `messages` is not part of a v2
  request, so conversation history never reaches the RAG backend.

Design notes and the remaining phases (discovery-driven negotiation, removing
the completion short-circuit, retiring v1) are in
[RAG API v2: Query-Oriented Interface](../planning/rag-api-v2-tool-interface.md).

## Testing with the Mock Service

A mock ATLAS RAG API is provided in `mocks/atlas-rag-api-mock/` for testing.

### Starting the Mock

```bash
cd mocks/atlas-rag-api-mock
bash run.sh
```

The mock runs on `http://localhost:8002` with token `test-atlas-rag-token`,
and serves both contracts: `/api/v1/discover/datasources` +
`/api/v1/rag/completions` (v1), and `/api/v2/discover/datasources` +
`/api/v2/rag/query` (v2, OpenAPI v0.8.0). Point a source at v2 by adding
`"api_version": "v2"`.

### Test Users

| User | Groups | Accessible Data Sources |
|------|--------|------------------------|
| `alice@example.com` | employee, engineering | company-policies, technical-docs, product-knowledge |
| `bob@example.com` | employee, sales | company-policies, product-knowledge |
| `charlie@example.com` | employee, engineering, devops | company-policies, technical-docs, product-knowledge |
| `test@test.com` | employee, engineering, devops, admin | All data sources |
| `guest@example.com` | (none) | product-knowledge (public only) |

### Data Sources

| Data Source | Compliance | Required Groups | Content |
|-------------|------------|-----------------|---------|
| `company-policies` | Internal | employee | Remote work, expenses, code of conduct, PTO policies |
| `technical-docs` | Internal | engineering, devops | API auth, database schema, deployment, microservices |
| `product-knowledge` | Public | (none) | Getting started, troubleshooting, pricing, API reference |

## RAG Completions vs Raw Results

Last updated: 2026-08-26

Atlas UI supports two types of RAG responses, tracked by the `is_completion`
flag on `RAGResponse`:

1. **Completions** (`is_completion=true`): The RAG API returns an
   already-synthesized answer. Atlas renders it directly without an additional
   LLM call, reducing latency and API costs. v1 sources always produce
   completions. v2 sources produce completions when `mode: "synthesized"` (the
   default for non-agent RAG).

2. **Raw Results** (`is_completion=false`): The RAG API returns evidence
   (document snippets). Atlas sends this context to the configured LLM for
   interpretation and response generation. v2 sources produce raw results when
   `mode: "raw"` (the default for the `atlas_rag_query` agent tool).

The `is_completion` flag is set by `AtlasRAGClient`: `True` for v1 `query_rag`
(always synthesized) and for v2 `query_v2` with `mode: "synthesized"`; `False`
for v2 `query_v2` with `mode: "raw"`.

This behavior applies to `call_with_rag` (RAG mode) in the LLM caller. In
tools and agent mode there is no pre-injection at all: the model calls the
`atlas_search` tool, and whatever the source returns -- synthesized answer or
raw passages -- comes back as that call's tool result.

## UI Behavior (2026-03-18)

The frontend provides two controls for RAG:

- **Header Sources button** (Database icon): Toggles the Data Sources sidebar open/closed. It turns blue when data sources are selected but does not change selection state on click.
- **Chat input search-glass button**: Controls RAG activation. When green (RAG active or data sources selected), clicking clears all selected data sources and disables RAG. When gray (RAG inactive), clicking opens the Data Sources sidebar so the user can select sources.

Users must explicitly select data sources from the sidebar to enable RAG; there is no "search all sources" toggle from the chat input.

## Troubleshooting

### RAG panel not showing in UI

- Verify `FEATURE_RAG_ENABLED=true` in `.env`
- If you expect atlas_rag pseudo-tools or MCP-backed atlas_rag exposure, also verify `FEATURE_ATLAS_RAG_TOOLS_ENABLED=true`
- Check that `rag-sources.json` has enabled sources
- Restart the backend after changing configuration

### Empty results from RAG

- Verify the URL is correct and reachable
- Check that bearer token is valid
- Confirm the user has access to the requested data sources
- Enable debug logging: `LOG_LEVEL=DEBUG`

### 401 Unauthorized errors

- Verify the bearer token is correctly configured
- Check that the token has not expired
- Ensure `${ENV_VAR}` syntax is used for secrets in config

### 403 Forbidden errors

- The user lacks access to the requested corpus
- Check user group memberships
- Verify compliance level requirements

### 404 Not Found errors

- Check that the corpus name exists in the RAG backend
- Verify the discovery endpoint returns the expected sources

## Architecture

```
User Request
     |
     v
+------------------+
|   Atlas UI       |
|   Backend        |
+--------+---------+
         |
         | rag-sources.json
         | (unified config)
         |
    +----+----+
    |         |
    v         v
  HTTP      MCP
  (atlas)  (stdio/sse)
```

## Environment Variables for Secrets

RAG source secrets should be set as environment variables and referenced in `rag-sources.json` using `${ENV_VAR}` syntax:

| Variable | Description |
|----------|-------------|
| `ATLAS_RAG_URL` | Base URL for ATLAS RAG API |
| `ATLAS_RAG_BEARER_TOKEN` | Bearer token for ATLAS RAG API authentication |

Example usage in `rag-sources.json`:
```json
{
  "atlas_rag": {
    "type": "http",
    "url": "${ATLAS_RAG_URL}",
    "bearer_token": "${ATLAS_RAG_BEARER_TOKEN}"
  }
}
```

## Related Documentation

- [Configuration Architecture](configuration.md) - General configuration overview
- [MCP Server Configuration](mcp-servers.md) - Configuring MCP servers
- [Compliance Levels](compliance.md) - How compliance levels affect RAG access
