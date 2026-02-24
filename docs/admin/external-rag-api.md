# RAG Configuration

Last updated: 2026-02-23

This guide explains how to configure RAG (Retrieval-Augmented Generation) in Atlas UI.

## Overview

Atlas UI supports multiple RAG backends through a unified configuration file (`rag-sources.json`). This allows you to configure multiple RAG sources of different types in a single place.

### Feature Flag Semantics

RAG is controlled by the `FEATURE_RAG_ENABLED` feature flag.

- When `FEATURE_RAG_ENABLED=false`, the backend skips RAG service initialization and does not load `rag-sources.json`. The `/api/config` response will show `features.rag=false` and will return empty `rag_servers` and `data_sources`.
- When `FEATURE_RAG_ENABLED=true`, the backend loads `rag-sources.json`, initializes RAG services, and exposes discovered sources to the UI via `/api/config`.

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

## API Contract (HTTP Sources)

HTTP RAG sources must implement these endpoints:

### Discovery Endpoint

```
GET /discover/datasources?as_user={user_email}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "data_sources": [
    {"id": "technical-docs", "label": "Technical Documentation", "compliance_level": "Internal", "description": "Engineering docs covering API auth, database schema, and deployment"},
    {"id": "company-wiki", "label": "Company Wiki", "compliance_level": "Public", "description": "Public company knowledge base"}
  ]
}
```

### Query Endpoint

```
POST /rag/completions?as_user={user_email}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "messages": [{"role": "user", "content": "What is our API architecture?"}],
  "stream": false,
  "model": "openai/gpt-oss-120b",
  "top_k": 4,
  "corpora": ["technical-docs"]
}
```

**Response (OpenAI ChatCompletion format with RAG metadata):**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "openai/gpt-oss-120b",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Based on the documentation..."},
    "finish_reason": "stop"
  }],
  "rag_metadata": {
    "query_processing_time_ms": 150,
    "documents_found": [{
      "id": "doc-001",
      "corpus_id": "technical-docs",
      "text": "API Gateway handles authentication...",
      "confidence_score": 0.95
    }],
    "data_sources": ["technical-docs"],
    "retrieval_method": "similarity"
  }
}
```

## Testing with the Mock Service

A mock ATLAS RAG API is provided in `mocks/atlas-rag-api-mock/` for testing.

### Starting the Mock

```bash
cd mocks/atlas-rag-api-mock
bash run.sh
```

The mock runs on `http://localhost:8002` with token `test-atlas-rag-token`.

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

Last updated: 2026-02-01

Atlas UI supports two types of RAG responses:

1. **Raw Results**: The RAG API returns document chunks or context. Atlas sends this context to the configured LLM for interpretation and response generation. This is the standard flow for most RAG queries.

2. **Completions**: The RAG API returns an already-interpreted response (detected by `"object": "chat.completion"` in the JSON response). Atlas detects this automatically and returns the content directly to the user without additional LLM processing.

When a RAG source returns a completion, Atlas:
- Skips the LLM call entirely, reducing latency and API costs
- Prepends a note indicating the response came from the RAG completions endpoint
- Appends RAG metadata (sources, processing time) if available

The `is_completion` flag on `RAGResponse` tracks whether the response is already LLM-interpreted. This is set automatically by `AtlasRAGClient` when it detects `"object": "chat.completion"` in the API response.

This behavior applies to both `call_with_rag` (RAG-only mode) and `call_with_rag_and_tools` (RAG + tools mode) in the LLM caller.

## Troubleshooting

### RAG panel not showing in UI

- Verify `FEATURE_RAG_ENABLED=true` in `.env`
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
