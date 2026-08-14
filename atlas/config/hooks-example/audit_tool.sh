#!/usr/bin/env bash
# Example PostToolUse hook (GH #713): append an audit record for every tool call.
# Fire-and-forget style; on_error: allow so a broken audit hook never kills a turn.
#
# NOTE: the envelope is secret-bearing. `tool_args` can contain tokenized
# download URLs (HMAC capability tokens), file paths, and prompt text, and
# `result_content` carries retrieved document text. This example therefore logs
# a *projection* -- tool name, argument key names, and sizes -- rather than the
# raw envelope. If you need full payloads for an investigation, treat the log
# as a secret store (restricted mode, rotation, no shipping to a general index).
set -eu
envelope="$(cat)"
mkdir -p "${ATLAS_PROJECT_DIR:-.}/logs"

python3 -c '
import json, sys

env = json.load(sys.stdin)
payload = env.get("payload") or {}
args = payload.get("tool_args") or {}
content = payload.get("result_content")

json.dump({
    "session_id": env.get("session_id"),
    "user_email": env.get("user_email"),
    "compliance_level": env.get("compliance_level"),
    "event": env.get("event"),
    "tool_name": payload.get("tool_name"),
    "tool_call_id": payload.get("tool_call_id"),
    # Key names only -- values are omitted deliberately (see note above).
    "arg_keys": sorted(args) if isinstance(args, dict) else None,
    "result_success": payload.get("result_success"),
    "result_len": len(content) if isinstance(content, str) else None,
}, sys.stdout)
sys.stdout.write("\n")
' <<<"$envelope" >> "${ATLAS_PROJECT_DIR:-.}/logs/tool-audit.jsonl"

exit 0
