#!/usr/bin/env bash
# Chat example that enables agent mode
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

echo "Running agent-mode chat..."
python cli.py chat --model "gpt-4" --user-email "agent-cli@company.com" --agent-mode "True" "Please act as an agent and use tools if helpful"
