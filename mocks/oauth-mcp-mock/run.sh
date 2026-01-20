#!/bin/bash
# Run the mock OAuth MCP server

# Set defaults
export OAUTH_MOCK_HOST="${OAUTH_MOCK_HOST:-0.0.0.0}"
export OAUTH_MOCK_PORT="${OAUTH_MOCK_PORT:-8001}"
export OAUTH_MOCK_BASE_URL="${OAUTH_MOCK_BASE_URL:-http://localhost:$OAUTH_MOCK_PORT}"

echo "Starting Mock OAuth MCP Server..."
echo "  Host: $OAUTH_MOCK_HOST"
echo "  Port: $OAUTH_MOCK_PORT"
echo "  Base URL: $OAUTH_MOCK_BASE_URL"

python main.py
