# External Integration Guide: RAG over MCP (short)

How to add a RAG MCP server. 

## TL;DR — implement these tools

Your MCP server must export 2 (or 3) tools with these names and inputs:

- `rag_discover_resources(username: string)` — required
- `rag_get_raw_results(username: string, query: string, sources?: string[], top_k?: number, filters?: object, ranking?: object)` — required
- `rag_get_synthesized_results(username: string, query: string, sources?: string[], top_k?: number, synthesis_params?: object, provided_context?: object)` — optional (recommended)

All responses must follow the MCP v2 envelope: `results` plus optional `meta_data` and `artifacts`.

## Tool contracts (OpenAPI-like)

### 1. `rag_discover_resources`
1) `rag_discover_resources`

Request
```json
{ "username": "user@example.com" }
```

Response
```json
{
  "results": {
    "resources": [
  { "id": "handbook", "name": "Employee Handbook", "authRequired": true, "groups": ["users"], "defaultSelected": true }
    ]
  },
  "meta_data": { "elapsed_ms": 12 }
}
```
### 2) `rag_get_raw_results`

`rag_get_raw_results`

Request
```json
{
  "username": "user@example.com",
  "query": "pto policy",
  "sources": ["handbook"],
  "top_k": 8,
  "filters": {},
  "ranking": {}
}
```

Response
```json
{
  "results": {
    "hits": [
      { "resourceId": "handbook", "title": "Leave policy", "snippet": "…", "score": 0.92, "uri": "https://…" }
    ],
    "stats": { "total_found": 25, "top_k": 8 }
  },
  "meta_data": { "provider": "your-server", "elapsed_ms": 45 }
}
```
### 3) `rag_get_synthesized_results` (optional)

`rag_get_synthesized_results`

Request
```json
{
  "username": "user@example.com",
  "query": "summarize pto policy",
  "sources": ["handbook"],
  "top_k": 5,
  "synthesis_params": { "style": "short" },
  "provided_context": { "hits": [] }
}
```

Response
```json
{
  "results": {
    "answer": "…",
    "citations": [ { "resourceId": "handbook", "uri": "https://…", "snippet": "…" } ],
    "limits": { "truncated": false }
  },
  "meta_data": { "model": "…", "elapsed_ms": 200 }
}
```

```json
{
  "docsRag": {
  "url": "https://mcp.example.com",
  "transport": "http",
    "groups": ["users"],
    "description": "Company docs RAG",
    "enabled": true
  }
}
```

Notes
- `url` + `transport` selects HTTP or SSE hosting; stdio works too (not shown here)
- `groups` can restrict visibility in the app (your server must still enforce access)

Tips
- Keep `results` small; put big payloads in `artifacts` with `mime` and `b64` (v2 contract — see `v2_mcp_note.md`)
- Always include `resourceId` on hits and citations
Tips
- Keep `results` small; put big payloads in `artifacts` with `mime` and `b64` (v2 contract)
- Always include `resourceId` on hits and citations

## Minimal server (complete example)

Python FastMCP example you can copy-paste:

```python
from typing import Any, Dict, List, Optional
from fastmcp import FastMCP

mcp = FastMCP("SimpleRAG")

RESOURCES = [
  {"id": "kb", "name": "Knowledge Base", "authRequired": True, "groups": ["users"], "defaultSelected": True},
]

DOCS = {
  "kb": [
    {"title": "Reset MFA", "snippet": "Steps to reset MFA…", "uri": "https://kb.example/reset-mfa"},
    {"title": "PTO Policy", "snippet": "Company PTO policy…", "uri": "https://kb.example/pto"},
  ]
}

@mcp.tool
def rag_discover_resources(username: str) -> Dict[str, Any]:
  return {"results": {"resources": RESOURCES}, "meta_data": {"elapsed_ms": 1}}

@mcp.tool
def rag_get_raw_results(
  username: str,
  query: str,
  sources: Optional[List[str]] = None,
  top_k: int = 8,
  filters: Optional[Dict[str, Any]] = None,
  ranking: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
  sources = sources or [r["id"] for r in RESOURCES]
  hits: List[Dict[str, Any]] = []
  for sid in sources:
    for d in DOCS.get(sid, []):
      # naive match; score 1.0 if query term in title or snippet
      q = (query or "").lower()
      text = (d.get("title", "") + "\n" + d.get("snippet", "")).lower()
      if not q or q in text:
        hits.append({"resourceId": sid, "score": 1.0, **d})
  hits = hits[: (top_k or len(hits))]
  return {"results": {"hits": hits, "stats": {"total_found": len(hits), "top_k": top_k}}, "meta_data": {"elapsed_ms": 2}}

@mcp.tool
def rag_get_synthesized_results(
  username: str,
  query: str,
  sources: Optional[List[str]] = None,
  top_k: Optional[int] = None,
  synthesis_params: Optional[Dict[str, Any]] = None,
  provided_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
  raw = rag_get_raw_results(username, query, sources, top_k or 5)
  hits = (raw.get("results") or {}).get("hits") or []
  answer = "\n".join(h.get("title", "") for h in hits[: (top_k or 3)]) or "No results."
  cits = [{"resourceId": h.get("resourceId"), "uri": h.get("uri"), "snippet": h.get("snippet")} for h in hits]
  return {"results": {"answer": answer, "citations": cits, "limits": {"truncated": False}}, "meta_data": {"elapsed_ms": 3}}

if __name__ == "__main__":
  mcp.run()
```
## Auth notes. 

* The chat app will inject the `username` of the user making the request. You can assume this correct.
* Discovery must return `authRequired: true` and `groups: string[]` per resource
* Your server should filter discoverable resources by `username` and re-check on search/synth; treat groups as your policy input and enforce access

TLDR: The chat app guarantees that the `username` is correct, so you can use it for access control. You are responsible for enforcing access based on the user's groups and the resource's requirements.

## Quick checklist

- Implement the 2–3 tools above with these shapes
- Include `username` and enforce access in every tool
- Keep `results` small; put larger payloads in `artifacts`
- Return `resourceId`, `score`, and `uri` for hits; `answer` + `citations` for synth
- Register your server in `mcp-rag.json` and run
- In general you should NOT trust user's input. Always sanitize input against injection attacks.
- In RAG systems it is important to also know your own data if it is collected from outside source and guard against data poisoning with specially crafted malicious inputs.

## Minimal examples

Discovery
```json
{
  "results": { "resources": [ { "id": "kb", "name": "Knowledge Base", "authRequired": true, "groups": ["users"] } ] },
  "meta_data": { "elapsed_ms": 7 }
}
```

Raw search
```json
{
  "results": {
    "hits": [ { "resourceId": "kb", "title": "Reset MFA", "snippet": "…", "score": 0.81, "uri": "https://…" } ],
    "stats": { "total_found": 10, "top_k": 8 }
  },
  "meta_data": { "elapsed_ms": 38 }
}
```

Synthesis
```json
{
  "results": { "answer": "…", "citations": [ { "resourceId": "kb", "uri": "https://…" } ] },
  "meta_data": { "elapsed_ms": 190 }
}
```

That’s it. Keep responses small and consistent; the app does the rest.