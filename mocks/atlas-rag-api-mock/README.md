# External ATLAS RAG API Mock

Mock service for testing the `AtlasRAGClient` integration without requiring access to the real ATLAS RAG API.

## Quick Start

```bash
# From the project root
cd mocks/atlas-rag-api-mock
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
  "user_name": "test@test.com",
  "accessible_data_sources": [
    {"name": "engineering-docs", "compliance_level": "Internal"},
    {"name": "financial-reports", "compliance_level": "CUI"},
    {"name": "company-wiki", "compliance_level": "Public"}
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

## Test Data

### Test Bearer Token
```
test-atlas-rag-token
```

### Test Users and Groups

| User | Groups |
|------|--------|
| alice@example.com | engineering, data-science |
| bob@example.com | sales, marketing |
| charlie@example.com | engineering, devops |
| diana@example.com | finance, executive |
| test@test.com | engineering, finance, admin |
| guest@example.com | public |

### Available Corpora

| Corpus | Required Groups | Compliance |
|--------|----------------|------------|
| engineering-docs | engineering | Internal |
| sales-playbook | sales, marketing | Internal |
| kubernetes-runbooks | engineering, devops | CUI |
| financial-reports | finance, executive | CUI |
| company-wiki | (public) | Public |
| research-papers | data-science, engineering | Internal |

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
