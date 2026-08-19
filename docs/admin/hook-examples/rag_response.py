#!/usr/bin/env python3
"""RagResponse example: stamp a handling caveat onto retrieved content.

Event:    RagResponse (after retrieval, before the content is injected into the
          prompt).
Matcher:  the comma-joined qualified data sources.
Payload:  {"query": str, "qualified_data_sources": [str], "username": str,
           "content": str, "metadata": {...}|None}
Can do:   modify (only ``payload["content"]`` is read back -- returned metadata
          is ignored) | deny (the response becomes empty) | continue.
on_error: allow (per-event default).

Source narrowing is NOT honored here; retrieval already happened. Use RagCall
to control which sources are queried.
"""
import json
import sys

BANNER = "[INTERNAL USE ONLY -- do not paste into external systems]\n"

env = json.load(sys.stdin)
payload = env.get("payload") or {}
content = payload.get("content")
sources = payload.get("qualified_data_sources") or []

if not isinstance(content, str) or not content.strip():
    sys.exit(0)

if any("internal" in s for s in sources) and not content.startswith(BANNER):
    print(json.dumps({
        "decision": "modify",
        "payload": {"content": BANNER + content},
    }))
sys.exit(0)
