#!/bin/bash
# Run the API Key Demo MCP Server
#
# Usage:
#   ./run.sh          - Start on default port 8765
#   ./run.sh 9000     - Start on custom port 9000

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

# Verify python exists
if [ ! -f "$PYTHON" ]; then
    echo "Error: Python not found at $PYTHON"
    echo "Run from project root: uv venv && uv pip install -r requirements.txt"
    exit 1
fi

cd "$SCRIPT_DIR"

PORT="${1:-8765}"

echo "Starting API Key Demo MCP Server on port $PORT..."
"$PYTHON" main.py "$PORT"
