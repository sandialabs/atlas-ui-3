#!/usr/bin/env python3
"""RagCall example: narrow a retrieval to the sources a compliance level allows.

Event:    RagCall (UnifiedRAGService.query_rag/query_rag_batch and the agentic
          RAG path), before retrieval runs.
Matcher:  the comma-joined qualified data sources.
Payload:  {"query": str, "qualified_data_sources": [str], "username": str}
Can do:   modify ("query" rewrite, "qualified_data_sources" NARROWED) | deny
          (retrieval is skipped and an empty response is returned) | continue.
          require_approval is a no-op at the RAG layer.
on_error: deny (per-event default).

Sources the hook lists that were not in the original request are dropped: the
allow-list can only shrink, never grow beyond what compliance already permitted.

``compliance_level`` is a canonical level *name* ("Public", "Internal", ...) or
null -- see config/compliance-levels.json. It is a named set, not a ranking, so
this compares membership rather than ordering.
"""
import json
import sys

# Sources only these compliance levels may retrieve from.
SENSITIVE_SOURCES = {"hr-records", "incident-reports"}
LEVELS_ALLOWED_SENSITIVE = {"Internal", "HIPAA"}

env = json.load(sys.stdin)
payload = env.get("payload") or {}
sources = payload.get("qualified_data_sources") or []

if env.get("compliance_level") in LEVELS_ALLOWED_SENSITIVE:
    sys.exit(0)

allowed = [s for s in sources if s.split("/")[-1] not in SENSITIVE_SOURCES]
if allowed == sources:
    sys.exit(0)

if not allowed:
    print(json.dumps({
        "decision": "deny",
        "reason": "None of the selected sources are available at your compliance level.",
    }))
else:
    print(json.dumps({
        "decision": "modify",
        "payload": {"qualified_data_sources": allowed},
    }))
sys.exit(0)
