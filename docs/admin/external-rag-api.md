# RAG Configuration

Last updated: 2026-01-21

This guide explains how to configure RAG (Retrieval-Augmented Generation) in Atlas UI.

## Overview

Atlas UI supports multiple RAG backends controlled by a single `RAG_PROVIDER` setting:

| Provider | Description |
|----------|-------------|
| `none` | RAG disabled (default) |
| `mock` | Built-in mock for testing |
| `atlas` | External ATLAS RAG API |
| `mcp` | MCP-based RAG servers |

## Quick Start

Add one line to your `.env` file:

```bash
# Choose your RAG provider: none, mock, atlas, or mcp
RAG_PROVIDER=atlas
```

## Provider: `atlas` (ATLAS RAG API)

When `RAG_PROVIDER=atlas`, Atlas UI routes RAG queries to an external ATLAS RAG API.

### Configuration

```bash
# Enable ATLAS RAG
RAG_PROVIDER=atlas

# ATLAS RAG API settings
ATLAS_RAG_URL=https://rag-api.example.com
ATLAS_RAG_BEARER_TOKEN=your-secret-token

# Optional settings
ATLAS_RAG_DEFAULT_MODEL=openai/gpt-oss-120b
ATLAS_RAG_TOP_K=4
```

### Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RAG_PROVIDER` | Yes | `none` | Set to `atlas` to use ATLAS RAG API |
| `ATLAS_RAG_URL` | Yes | `http://localhost:8002` | Base URL of the ATLAS RAG API |
| `ATLAS_RAG_BEARER_TOKEN` | Recommended | `None` | Bearer token for API authentication |
| `ATLAS_RAG_DEFAULT_MODEL` | No | `openai/gpt-oss-120b` | Model identifier for RAG queries |
| `ATLAS_RAG_TOP_K` | No | `4` | Number of documents to retrieve |

### API Contract

The ATLAS RAG API must implement two endpoints:

#### Discovery Endpoint

```
GET /discover/datasources?as_user={user_email}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "user_name": "user@example.com",
  "accessible_data_sources": [
    {"name": "engineering-docs", "compliance_level": "Internal"},
    {"name": "company-wiki", "compliance_level": "Public"}
  ]
}
```

#### Query Endpoint

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
  "corpora": ["engineering-docs"]
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
      "corpus_id": "engineering-docs",
      "text": "API Gateway handles authentication...",
      "confidence_score": 0.95
    }],
    "data_sources": ["engineering-docs"],
    "retrieval_method": "similarity"
  }
}
```

## Provider: `mock` (Testing)

For development and testing:

```bash
RAG_PROVIDER=mock
```

This uses the built-in mock RAG client with sample data.

## Provider: `mcp` (MCP Servers)

For MCP-based RAG servers configured in `mcp-rag.json`:

```bash
RAG_PROVIDER=mcp
```

See [MCP Server Configuration](mcp-servers.md) for details on configuring MCP RAG servers.

## Testing with the Mock Service

A mock ATLAS RAG API is provided in `mocks/atlas-rag-api-mock/` for testing the `atlas` provider.

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

## Troubleshooting

### RAG panel not showing in UI

- Verify `RAG_PROVIDER` is set to something other than `none`
- Restart the backend after changing configuration

### Empty results from ATLAS RAG

- Verify `ATLAS_RAG_URL` is correct and reachable
- Check that `ATLAS_RAG_BEARER_TOKEN` is valid
- Confirm the user has access to the requested data sources

### 401 Unauthorized errors

- Verify the bearer token is correctly configured
- Check that the token has not expired

### 403 Forbidden errors

- The impersonated user lacks access to the requested corpus
- Check user group memberships in the RAG API

### Logging

Enable debug logging to see RAG client activity:

```bash
LOG_LEVEL=DEBUG
```

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
         | RAG_PROVIDER decides routing
         |
    +----+----+----+
    |         |    |
    v         v    v
  mock     atlas  mcp
```

## Backward Compatibility

The old environment variables still work as aliases:

| Old Variable | New Variable |
|--------------|--------------|
| `EXTERNAL_RAG_URL` | `ATLAS_RAG_URL` |
| `EXTERNAL_RAG_BEARER_TOKEN` | `ATLAS_RAG_BEARER_TOKEN` |
| `EXTERNAL_RAG_DEFAULT_MODEL` | `ATLAS_RAG_DEFAULT_MODEL` |
| `EXTERNAL_RAG_TOP_K` | `ATLAS_RAG_TOP_K` |

However, `FEATURE_RAG_ENABLED` and `EXTERNAL_RAG_ENABLED` are deprecated. Use `RAG_PROVIDER` instead.

## Related Documentation

- [Configuration Architecture](configuration.md) - General configuration overview
- [MCP Server Configuration](mcp-servers.md) - Configuring MCP servers including RAG
- [Compliance Levels](compliance.md) - How compliance levels affect RAG access
