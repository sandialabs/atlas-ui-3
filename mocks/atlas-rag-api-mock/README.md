# External ATLAS RAG API Mock

Mock service for testing the `AtlasRAGClient` integration without requiring access to the real ATLAS RAG API.

## Quick Start

```bash
# From the project root
cd mocks/atlas-rag-api-mock
./run.sh

# Or directly with Python
python main.py
```

The service runs on `http://localhost:8002` by default.

## Endpoints

### GET /discover/datasources

Discover data sources accessible by a user.

**Parameters:**
- `as_user` (query, required): Username to check access for

**Headers:**
- `Authorization: Bearer <token>` (required)

**Example:**
```bash
curl -H "Authorization: Bearer test-atlas-rag-token" \
     "http://localhost:8002/discover/datasources?as_user=test@test.com"
```

**Response:**
```json
{
  "data_sources": [
    {"id": "company-policies", "label": "Company Policies", "compliance_level": "Internal", "description": "HR policies including remote work, expenses, PTO, and code of conduct"},
    {"id": "technical-docs", "label": "Technical Documentation", "compliance_level": "Internal", "description": "Engineering docs covering API auth, database schema, deployment, and architecture"},
    {"id": "product-knowledge", "label": "Product Knowledge Base", "compliance_level": "Public", "description": "Public product docs with getting started, troubleshooting, features, and API reference"}
  ]
}
```

### POST /rag/completions

Query RAG for completions.

**Parameters:**
- `as_user` (query, required): Username making the request

**Headers:**
- `Authorization: Bearer <token>` (required)
- `Content-Type: application/json` (required)

**Request Body:**
```json
{
  "messages": [{"role": "user", "content": "What is our deployment process?"}],
  "stream": false,
  "model": "openai/gpt-oss-120b",
  "top_k": 4,
  "corpora": ["engineering-docs"],
  "threshold": null,
  "expanded_window": [0, 0]
}
```

**Example:**
```bash
curl -X POST \
     -H "Authorization: Bearer test-atlas-rag-token" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"Tell me about the API"}],"corpora":["engineering-docs"]}' \
     "http://localhost:8002/rag/completions?as_user=test@test.com"
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "openai/gpt-oss-120b",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Based on the retrieved information..."
    },
    "finish_reason": "stop"
  }],
  "rag_metadata": {
    "query_processing_time_ms": 150,
    "documents_found": [{
      "id": "eng-001",
      "corpus_id": "engineering-docs",
      "text": "API Gateway handles authentication...",
      "confidence_score": 0.95,
      "content_type": "markdown"
    }],
    "data_sources": ["engineering-docs"],
    "retrieval_method": "similarity"
  }
}
```

## Authentication

The mock uses a `StaticTokenVerifier` pattern with Bearer token authentication.

### Token Configuration

Set the token via environment variable (any of these):
```bash
export ATLAS_RAG_SHARED_KEY=your-token-here
# or
export atlas_rag_shared_key=your-token-here
```

If no environment variable is set, defaults to: `test-atlas-rag-token`

### Test Bearer Token
```
test-atlas-rag-token
```

### Test Users and Groups

| User | Groups |
|------|--------|
| alice@example.com | employee, engineering |
| bob@example.com | employee, sales |
| charlie@example.com | employee, engineering, devops |
| test@test.com | employee, engineering, devops, admin |
| guest@example.com | (none) |

### Available Corpora

| Corpus | Label | Required Groups | Compliance |
|--------|-------|----------------|------------|
| company-policies | Company Policies | employee | Internal |
| technical-docs | Technical Documentation | engineering, devops | Internal |
| product-knowledge | Product Knowledge Base | (public) | Public |

## Using with Atlas UI

1. Set environment variables in `.env`:
   ```bash
   FEATURE_RAG_ENABLED=true
   EXTERNAL_RAG_ENABLED=true
   EXTERNAL_RAG_URL=http://localhost:8002
   EXTERNAL_RAG_BEARER_TOKEN=test-atlas-rag-token
   EXTERNAL_RAG_DEFAULT_MODEL=openai/gpt-oss-120b
   EXTERNAL_RAG_TOP_K=4
   ```

2. Start the mock service:
   ```bash
   cd mocks/atlas-rag-api-mock
   python main.py
   ```

3. Start Atlas UI:
   ```bash
   bash agent_start.sh
   ```

4. Open http://localhost:8000 and use the RAG panel with a test user.

## API Docs

When the service is running, visit:
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc
