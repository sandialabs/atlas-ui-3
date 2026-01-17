#!/usr/bin/env bash
# List available MCP tools via the headless CLI
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

echo "Listing available tools..."
python cli.py list-tools
