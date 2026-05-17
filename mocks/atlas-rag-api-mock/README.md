# ATLAS RAG API Mock

Mock service for testing the `AtlasRAGClient` integration without requiring access to the real ATLAS RAG API. Matches the newest ATLAS RAG OpenAPI spec (v0.3.0.dev1+), including per-reference `sections` with `text` snippets.

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

### GET /api/v1/discover/datasources

Discover data sources accessible by a user.

**Parameters:**
- `as_user` (query, optional): User ID to impersonate
- `role` (query, optional): `read` (default) or `write`

**Headers:**
- `Authorization: Bearer <token>` (required)

**Example:**
```bash
curl -H "Authorization: Bearer test-atlas-rag-token" \
     "http://localhost:8002/api/v1/discover/datasources?role=read&as_user=test@test.com"
```

**Response:** bare list of `DataSource` per spec:
```json
[
  {"id": "company-policies", "label": "Company Policies", "compliance_level": "Internal", "description": "..."},
  {"id": "technical-docs", "label": "Technical Documentation", "compliance_level": "Internal", "description": "..."},
  {"id": "product-knowledge", "label": "Product Knowledge Base", "compliance_level": "Public", "description": "..."}
]
```

### POST /api/v1/rag/completions

Query RAG for a response with structured references.

**Parameters:**
- `as_user` (query, optional): User ID to impersonate

**Headers:**
- `Authorization: Bearer <token>` (required)
- `Content-Type: application/json` (required)

**Request Body** (`RagRequest`):
```json
{
  "messages": [{"role": "user", "content": "What is the API authentication?"}],
  "stream": false,
  "corpora": ["technical-docs"]
}
```

`corpora` may be a single string or a list of strings.

**Example:**
```bash
curl -X POST \
     -H "Authorization: Bearer test-atlas-rag-token" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"Tell me about API authentication"}],"corpora":["technical-docs"]}' \
     "http://localhost:8002/api/v1/rag/completions?as_user=test@test.com"
```

**Response** (`RagResponse`):
```json
{
  "message": {
    "role": "assistant",
    "content": "Based on searching 1 data source(s), I found 1 relevant document(s)..."
  },
  "metadata": {
    "response_time": 1,
    "references": [
      {
        "citation": "[1] \"API Authentication Guide\", tech-001.txt available: https://docs.company.com/api/authentication",
        "document_ref": 1,
        "filename": "tech-001.txt",
        "sections": [
          {
            "section_ref": 1,
            "text": "API Authentication Guide ... Our API uses OAuth 2.0 with JWT tokens for authentication.",
            "relevance": 1.0
          },
          {
            "section_ref": 2,
            "text": "USING ACCESS TOKENS Include the token in all API requests: Authorization: Bearer ...",
            "relevance": 0.5
          }
        ]
      }
    ]
  }
}
```

The frontend consumes `metadata.references[].sections[].text` to display the underlying evidence snippets in the expanded citation area beneath each reference.

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

2. Build the frontend with the citations feature flag (the snippet
   rendering is gated by the same flag as inline citations):
   ```bash
   VITE_FEATURE_RAG_CITATIONS=true bash agent_start.sh
   ```

3. Start the mock service:
   ```bash
   cd mocks/atlas-rag-api-mock
   python main.py
   ```

4. Open http://localhost:8000 and ask a question against the RAG panel.
   In the expanded **References** section, each entry shows the underlying
   `Section.text` snippet that produced the citation.

## API Docs

When the service is running, visit:
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc
