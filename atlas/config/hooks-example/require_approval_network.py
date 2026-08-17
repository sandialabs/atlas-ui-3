#!/usr/bin/env python3
"""Example PreToolUse hook (GH #713): force approval for cross-domain network tools.

Reads the event envelope (JSON) on stdin. Emits a JSON decision on stdout:
  - require_approval -> escalate to the human approval gate even when not
    normally required (e.g. agent mode with skip_approval).
  - continue (no output, exit 0) -> proceed.
"""
import json
import sys

env = json.load(sys.stdin)
tool_name = env.get("payload", {}).get("tool_name", "")
if tool_name.startswith(("http_", "fetch_", "url_")):
    print(json.dumps({
        "decision": "require_approval",
        "reason": "Cross-domain network call requires approval",
    }))
sys.exit(0)
