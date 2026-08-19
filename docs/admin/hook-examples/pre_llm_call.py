#!/usr/bin/env python3
"""PreLlmCall example: keep restricted turns on an on-premises model.

Event:    PreLlmCall (LiteLLM caller, for plain calls, tool calls, and streaming).
Matcher:  none in practice -- this event supplies no matcher value.
Payload:  {"model": str, "messages": [...], "user_email": str, "tools": [...]?}
Can do:   modify (swap "model", rewrite "messages"/"tools") | deny (raises
          HookBlockedError; the reason surfaces to the user) | continue.
          require_approval is treated as continue: there is no approval gate at
          the LLM layer, so use deny to stop a call.
on_error: deny (per-event default -- this event guards a boundary).

``compliance_level`` in the envelope is a canonical level *name* from
``config/compliance-levels.json`` ("Public", "Internal", "HIPAA", ...) or null
-- not a number. Levels are a set with an allowed-with graph, not a ranking, so
compare against a named set; ``level >= 3`` would never match anything.

A model swap is re-authorized against the same per-model group ACL the
orchestrator applied to the user's original choice. An unauthorized swap is
refused and the original model is kept, so this can tighten routing but never
widen it.
"""
import json
import sys

ONPREM_MODEL = "onprem-llama"
RESTRICTED_LEVELS = {"HIPAA", "FedRAMP"}

env = json.load(sys.stdin)
payload = env.get("payload") or {}

if env.get("compliance_level") in RESTRICTED_LEVELS and payload.get("model") != ONPREM_MODEL:
    print(json.dumps({
        "decision": "modify",
        "payload": {"model": ONPREM_MODEL},
    }))

sys.exit(0)
