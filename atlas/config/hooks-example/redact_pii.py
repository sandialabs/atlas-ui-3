#!/usr/bin/env python3
"""Example UserPromptSubmit hook (GH #713): redact US Social Security numbers
from the user's prompt before it reaches the LLM. Emits a modify decision only
when the prompt actually changed.
"""
import json
import re
import sys

env = json.load(sys.stdin)
prompt = env.get("payload", {}).get("prompt", "")
redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]", prompt)
if redacted != prompt:
    print(json.dumps({"decision": "modify", "payload": {"prompt": redacted}}))
sys.exit(0)
