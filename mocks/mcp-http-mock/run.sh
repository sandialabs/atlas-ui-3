#!/bin/bash

# MCP HTTP Mock Server Runner
# This script sets up environment variables for the mock server
# and starts it with the appropriate configuration.

set -e

echo "🚀 Starting MCP HTTP Mock Server..."

# Set default tokens if not already set
export MCP_MOCK_TOKEN_1="${MCP_MOCK_TOKEN_1:-test-api-key-123}"
export MCP_MOCK_TOKEN_2="${MCP_MOCK_TOKEN_2:-another-test-key-456}"

echo "Using tokens:"
echo "  Token 1: $MCP_MOCK_TOKEN_1"
echo "  Token 2: $MCP_MOCK_TOKEN_2"

# Change to the script directory
cd "$(dirname "$0")"

# Run the server
python main.py "$@"