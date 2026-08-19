#!/usr/bin/env python3
"""PostToolUse example: redact secrets out of a tool result.

Event:    PostToolUse (execute_single_tool, after the tool returns, before the
          result reaches the model or the UI).
Matcher:  the tool name.
Payload:  {"tool_name": str, "tool_args": {...}, "tool_call_id": str,
           "result_content": str, "result_success": bool, "result_error": str|None}
Can do:   modify ("result_content", "result_success") | deny (the result is
          replaced with an error) | continue.
on_error: allow (per-event default -- a broken audit hook must not kill a turn).

Note the envelope is secret-bearing: tool_args can carry tokenized URLs and
result_content carries retrieved document text. Log projections, not envelopes.
"""
import json
import re
import sys

SECRETS = [
    (re.compile(r"(?i)\b(api[_-]?key|password|secret)\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY]"),
]

env = json.load(sys.stdin)
payload = env.get("payload") or {}
content = payload.get("result_content")

if not isinstance(content, str):
    sys.exit(0)

redacted = content
for pattern, replacement in SECRETS:
    redacted = pattern.sub(replacement, redacted)

if redacted != content:
    print(json.dumps({
        "decision": "modify",
        "payload": {"result_content": redacted},
    }))
sys.exit(0)
