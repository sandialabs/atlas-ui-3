#!/usr/bin/env bash
# tmux-3-agents.sh
# Send the same prompt to dclaude, codex, and cline, each in a new tmux window.
#
# Usage: scripts/tmux-3-agents.sh "your prompt here"

set -euo pipefail

if [[ -z "${TMUX:-}" ]]; then
  echo "Error: not inside a tmux session." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 \"<prompt>\"" >&2
  exit 1
fi

PROMPT="$*"
QUOTED=$(printf '%q' "$PROMPT")

launch() {
  local name=$1
  local cmd=$2
  tmux new-window -n "$name"
  tmux send-keys -t "$name" "$cmd $QUOTED" Enter
}

launch dclaude "claude --dangerously-skip-permissions"
launch codex   "codex"
launch cline   "cline"

echo "Launched dclaude, codex, cline windows with prompt."
