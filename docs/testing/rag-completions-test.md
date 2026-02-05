# Manual Test: RAG Completions Direct Output

## Overview
This document describes how to test the new feature where RAG completions (LLM-interpreted results) are returned directly without further LLM processing.

## What Changed
- **Before**: When ATLAS RAG API returned chat completions (already interpreted by an LLM), Atlas UI would treat that as "raw context" and send it to the LLM again for processing.
- **After**: Atlas UI now detects when RAG returns chat completions (`object: "chat.completion"`) and returns them directly to the user with a note indicating the response is from the RAG completions endpoint.

## Prerequisites
1. Atlas UI backend and frontend running
2. ATLAS RAG API mock service running on http://localhost:8002
3. RAG feature enabled in `.env`:
   ```bash
   FEATURE_RAG_ENABLED=true
   ```

## Configuration

### 1. Configure rag-sources.json
Create or update `config/defaults/rag-sources.json` (or an overrides directory set via `APP_CONFIG_OVERRIDES`):
```json
{
  "atlas_rag": {
    "type": "http",
    "display_name": "ATLAS RAG",
    "url": "http://localhost:8002",
    "bearer_token": "test-atlas-rag-token",
    "default_model": "openai/gpt-oss-120b",
    "top_k": 4,
    "groups": [],
    "enabled": true
  }
}
```

### 2. Start Services

#### Start RAG Mock Service
```bash
cd mocks/atlas-rag-api-mock
python main.py
```
The service should start on http://localhost:8002

#### Start Atlas UI
```bash
# From project root
bash agent_start.sh
```

## Test Scenarios

### Scenario 1: RAG Completion (Direct Output)
This scenario tests that chat completions from RAG are returned directly.

**Steps:**
1. Open Atlas UI at http://localhost:8000
2. Log in as `test@test.com` (or any test user)
3. Open the RAG panel on the right
4. Select ATLAS RAG source
5. Select a data source (e.g., "company-policies" or "technical-docs")
6. Enter a query like: "What is our remote work policy?"
7. Submit the query

**Expected Result:**
- The response should start with: `*Response from <source> (RAG completions endpoint):*`
- The response should contain the RAG-interpreted answer directly
- Below the response, there should be RAG metadata showing sources used
- **No additional LLM processing should occur** - the response from the RAG API should be returned as-is

**What to Check:**
- Look for the note at the top indicating it's from RAG completions endpoint
- Check backend logs for: `[LLM+RAG] RAG returned chat completion - returning directly without LLM processing`
- Verify no additional LLM API call was made (check logs for absence of litellm completion after RAG response)

### Scenario 2: Verify Metadata Display
**Steps:**
1. Use the same setup as Scenario 1
2. Submit a query that will return results
3. Scroll to the bottom of the response

**Expected Result:**
- Should see a section titled "RAG Sources & Processing Info:"
- Should show query processing time
- Should show data sources used
- Should show retrieval method
- Should show number of documents found

### Scenario 3: Multiple Queries
**Steps:**
1. Make several different queries with RAG enabled
2. Verify each response includes the RAG completions note
3. Try queries that find results and queries that don't

**Expected Result:**
- All responses should be direct RAG outputs
- No queries should trigger additional LLM processing when RAG returns completions

## Verification Checklist

- [ ] RAG completions are detected (check `is_completion=True` in logs)
- [ ] Responses include note about RAG completions endpoint
- [ ] No additional LLM processing occurs for completions
- [ ] RAG metadata is properly displayed
- [ ] Multiple data sources work correctly
- [ ] The UI displays the response properly formatted

## Backend Logs to Monitor

Look for these log messages:

### Success Indicators
```
[HTTP-RAG] Extracted content: length=XXX, is_completion=True, preview=...
[HTTP-RAG] query_rag complete: user=..., source=..., is_completion=True
[LLM+RAG] RAG response received: content_length=XXX, has_metadata=True, is_completion=True
[LLM+RAG] RAG returned chat completion - returning directly without LLM processing
[LLM+RAG] Returning RAG completion directly: response_length=XXX
```

### What You Should NOT See
If RAG returns a completion, you should NOT see:
```
[LLM+RAG] Calling LLM with RAG-enriched context...
```

## Troubleshooting

### RAG Panel Not Showing
- Verify `FEATURE_RAG_ENABLED=true` in `.env`
- Check that `rag-sources.json` has enabled sources
- Restart the backend

### RAG Mock Not Working
- Verify it's running: `curl http://localhost:8002/health`
- Check the bearer token matches in both config and mock
- Check mock logs for authentication errors

### Still Seeing LLM Processing
- Check backend logs to see if `is_completion` is being detected
- Verify the mock is returning `"object": "chat.completion"` in response
- Check for any errors in the RAG response parsing

## Test Users (Mock Data)

The ATLAS RAG API mock has these test users:

| User | Groups | Accessible Data Sources |
|------|--------|------------------------|
| alice@example.com | employee, engineering | company-policies, technical-docs, product-knowledge |
| bob@example.com | employee, sales | company-policies, product-knowledge |
| test@test.com | employee, engineering, devops, admin | All data sources |

## Expected Mock Response Format

The ATLAS RAG API mock returns responses in this format:
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "openai/gpt-oss-120b",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Based on searching 1 data source(s), I found 3 relevant result(s):..."
    },
    "finish_reason": "stop"
  }],
  "rag_metadata": {
    "query_processing_time_ms": 150,
    "documents_found": [...],
    "data_sources": ["company-policies"],
    "retrieval_method": "keyword-search"
  }
}
```

The key field is `"object": "chat.completion"` which signals that this is an LLM-interpreted result.
