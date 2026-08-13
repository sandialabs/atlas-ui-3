#!/usr/bin/env bash
# Example PreToolUse hook (GH #713): block writes to sensitive OS paths.
# Reads the event envelope (JSON) on stdin; exit 2 = block with stderr as reason.
set -eu
envelope="$(cat)"
path="$(printf '%s' "$envelope" | python3 -c 'import json,sys; print(json.load(sys.stdin)["payload"]["tool_args"].get("path",""))' 2>/dev/null || true)"
case "$path" in
  /etc/*|/var/*|/usr/*|/root/*|/boot/*|/sys/*|/proc/*)
    echo "Writes to $path are blocked by policy" >&2
    exit 2
    ;;
esac
exit 0