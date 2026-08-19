#!/usr/bin/env python3
"""UserPromptSubmit example: redact secrets and drop tools from a risky turn.

Event:    UserPromptSubmit (ChatOrchestrator.execute, after the user message is
          stored, before file ingestion and mode dispatch).
Matcher:  none -- this event supplies no matcher value.
Payload:  {"prompt": str, "selected_tools": [str], "selected_data_sources": [str],
           "agent_mode": bool}
Can do:   modify (rewrite prompt, NARROW tools/sources, turn off agent_mode) |
          deny (turn is blocked and the reason is shown to the user) | continue.
on_error: allow (per-event default).

Narrowing only: the orchestrator intersects the returned lists with what the
user actually selected, so a hook can never grant access to a tool or source
the user did not choose. An empty list means "none" and is preserved.
"""
import json
import re
import sys

AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

env = json.load(sys.stdin)
payload = env.get("payload") or {}
prompt = payload.get("prompt") or ""

redacted = SSN.sub("[REDACTED-SSN]", AWS_KEY.sub("[REDACTED-AWS-KEY]", prompt))

if redacted == prompt:
    sys.exit(0)  # exit 0 with empty stdout == "continue"

# The prompt carried a credential: scrub it and strip tools from this turn so
# the secret cannot be forwarded outward before a human has looked at it.
print(json.dumps({
    "decision": "modify",
    "payload": {"prompt": redacted, "selected_tools": [], "agent_mode": False},
}))
sys.exit(0)
