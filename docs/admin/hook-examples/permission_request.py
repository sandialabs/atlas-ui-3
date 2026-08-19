#!/usr/bin/env python3
"""PermissionRequest example: auto-approve read-only tools, escalate the rest.

Event:    PermissionRequest (execute_single_tool, at the approval gate --
          before PreToolUse runs).
Matcher:  the tool name.
Payload:  {"tool_name": str, "tool_args": {...}, "tool_call_id": str,
           "needs_approval": bool, "allow_edit": bool, "admin_required": bool}
Can do:   modify with {"needs_approval": false} (auto-approve) | modify
          tool_args | require_approval (force the gate) | deny | continue.
on_error: deny (per-event default).

This is the only event that may *lower* the gate, and even then:
  * ``admin_required`` stays authoritative -- escalating here never turns
    "ask the user" into "only an admin may approve";
  * if a later PreToolUse hook rewrites the arguments, this auto-approval is
    revoked, because it covered the arguments this hook inspected.
"""
import json
import sys

READ_ONLY_TOOLS = ("filesystem__read_file", "filesystem__list_directory", "rag__search")

env = json.load(sys.stdin)
payload = env.get("payload") or {}
tool = payload.get("tool_name") or ""

if tool in READ_ONLY_TOOLS:
    print(json.dumps({
        "decision": "modify",
        "payload": {"needs_approval": False},
        "reason": "Read-only tool auto-approved by policy",
    }))
elif tool.startswith("email__") or tool.startswith("http_"):
    print(json.dumps({
        "decision": "require_approval",
        "reason": "Outbound tool: a human must confirm this call",
    }))

sys.exit(0)
