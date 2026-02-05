#!/bin/bash
# Run the API Key Demo MCP Server
#
# Usage:
#   ./run.sh          - Start on default port 8006
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

PORT="${1:-8006}"

echo "========================================"
echo "API Key Demo MCP Server"
echo "========================================"
echo ""
echo "Add this to your config/overrides/mcp.json:"
echo ""
cat << EOF
{
  "api_key_demo": {
    "url": "http://127.0.0.1:${PORT}/mcp",
    "auth_type": "api_key",
    "auth_header": "X-API-Key",
    "auth_prompt": "Enter your API key for the demo server",
    "groups": ["users"],
    "description": "Demo server with API key authentication",
    "short_description": "API Key Auth Demo",
    "compliance_level": "Public"
  }
}
EOF
echo ""
echo "Valid test API keys:"
echo "  - test123 (developer)"
echo "  - admin123 (admin)"
echo "  - demo-api-key-12345 (viewer)"
echo ""
echo "========================================"
echo "Starting server on port $PORT..."
echo "========================================"
echo ""

"$PYTHON" main.py "$PORT"
