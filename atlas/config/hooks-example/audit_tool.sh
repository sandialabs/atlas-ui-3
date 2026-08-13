#!/usr/bin/env bash
# Example PostToolUse hook (GH #713): append every tool call to an audit log.
# Fire-and-forget style; on_error: allow so a broken audit hook never kills a turn.
set -eu
envelope="$(cat)"
mkdir -p "${ATLAS_PROJECT_DIR:-.}/logs"
printf '%s\n' "$envelope" >> "${ATLAS_PROJECT_DIR:-.}/logs/tool-audit.jsonl"
exit 0