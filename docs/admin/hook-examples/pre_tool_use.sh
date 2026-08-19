#!/usr/bin/env bash
# PreToolUse example (bash, exit-code style): block writes outside /workspace.
#
# Event:    PreToolUse (execute_single_tool, before the tool is invoked -- both
#           tools mode and the agentic loop).
# Matcher:  the tool name, e.g. "filesystem__.*".
# Payload:  {"tool_name": str, "tool_args": {...}, "tool_call_id": str}
# Can do:   modify (rewrite tool_args; security params such as _atlas_user are
#           re-injected afterwards) | deny | require_approval | continue.
#           A PreToolUse hook may escalate to the approval gate but can never
#           auto-approve.
# on_error: deny (per-event default).
#
# This is the whole exit-code contract: 0 = continue, 2 = block with stderr as
# the reason shown to the user, anything else = hook error (on_error decides).
set -eu

envelope="$(cat)"
path="$(printf '%s' "$envelope" | python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("payload") or {}).get("tool_args", {}).get("path", ""))' \
  2>/dev/null || true)"

case "$path" in
  ""|/workspace/*) exit 0 ;;
  *)
    echo "Policy: file tools may only touch /workspace (got: $path)" >&2
    exit 2
    ;;
esac
