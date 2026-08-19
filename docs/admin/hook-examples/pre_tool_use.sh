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
# The exit-code contract in full: 0 = continue, 2 = block with stderr as the
# reason shown to the user, anything else = hook error (on_error decides, which
# for PreToolUse means deny).
#
# Two things this example does on purpose, because a prefix check that skips
# them is a control that only looks like one:
#   * the path is normalized before comparison, so /workspace/../../etc/passwd
#     is not "inside /workspace";
#   * a payload this script cannot parse exits 1 (hook error -> deny), never 0.
#     Malformed input is exactly the case a policy hook must not wave through.
set -eu

envelope="$(cat)"

path="$(printf '%s' "$envelope" | python3 -c '
import json, os, sys
args = (json.load(sys.stdin).get("payload") or {}).get("tool_args")
if args is None:
    args = {}
if not isinstance(args, dict):
    raise SystemExit(1)
path = args.get("path")
if path is None:
    print("")            # no path argument: nothing for this rule to judge
else:
    print(os.path.normpath(str(path)))
' 2>/dev/null)" || {
    echo "Policy hook could not parse the tool arguments" >&2
    exit 1
}

case "$path" in
  "") exit 0 ;;
  /workspace|/workspace/*) exit 0 ;;
  *)
    echo "Policy: file tools may only touch /workspace (got: $path)" >&2
    exit 2
    ;;
esac
