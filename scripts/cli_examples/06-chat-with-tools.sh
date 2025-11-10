#!/usr/bin/env bash
# Chat example specifying multiple tools
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

echo "Running chat with multiple tools..."
python cli.py chat --model "gpt-4" --user-email "tools-cli@company.com" --agent-mode --tool "filesystem" --tool "web-search" "Please use the filesystem and web-search tools to answer"
