#!/usr/bin/env python3
"""SessionStart example: tag every session with the operator's policy version.

Event:    SessionStart (fires in ChatService.create_session, before the session
          row is persisted).
Matcher:  none -- SessionStart supplies no matcher value. Setting one means the
          hook never fires.
Payload:  {"session_id": "<uuid>"}
Can do:   modify (keys are merged into session.context) | deny (session is
          rejected and never persisted) | continue.
on_error: allow (per-event default).

This example attaches metadata and refuses sessions for a user who is not on
the pilot allow-list. Identity comes from the envelope's ``user_email``, which
is stamped server-side -- a hook cannot spoof or widen it.
"""
import json
import sys

PILOT_DENY_LIST = {"contractor@example.com"}

env = json.load(sys.stdin)
user = env.get("user_email") or ""

if user in PILOT_DENY_LIST:
    print(json.dumps({
        "decision": "deny",
        "reason": "Chat sessions are not enabled for this account.",
    }))
    sys.exit(0)

# Anything returned under "payload" is merged into session.context, so later
# hooks and audit records can see it.
print(json.dumps({
    "decision": "modify",
    "payload": {"policy_version": "2026-08-01", "onboarded_via": "session-start-hook"},
}))
sys.exit(0)
