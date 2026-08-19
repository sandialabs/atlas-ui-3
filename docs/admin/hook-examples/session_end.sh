#!/usr/bin/env bash
# SessionEnd example: append a one-line session record for the audit trail.
#
# Event:    SessionEnd (ChatService.end_session).
# Matcher:  none -- this event supplies no matcher value.
# Payload:  {"session_id": str, "user_email": str}
# Can do:   nothing blocking. A deny is logged and treated as continue, so a
#           misconfigured hook cannot wedge session teardown.
# on_error: allow (per-event default).
set -eu

envelope="$(cat)"
mkdir -p "${ATLAS_PROJECT_DIR:-.}/logs"

# Log a projection of the envelope, never the envelope itself: payloads can
# carry prompt text and tokenized URLs.
printf '%s' "$envelope" | python3 -c '
import json, sys
env = json.load(sys.stdin)
payload = env.get("payload") or {}
json.dump({
    "event": "SessionEnd",
    "session_id": payload.get("session_id"),
    "user_email": env.get("user_email"),
    "compliance_level": env.get("compliance_level"),
}, sys.stdout)
sys.stdout.write("\n")
' >> "${ATLAS_PROJECT_DIR:-.}/logs/session-audit.jsonl"

exit 0
