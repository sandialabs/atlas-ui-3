#!/bin/bash

# MCP HTTP Mock Server Runner
# This script sets up environment variables for the mock server
# and starts it with the appropriate configuration.

set -e

echo "Starting MCP HTTP Mock Server..."

# Set default tokens if not already set. Print only the source (not the value)
# so a real credential is not echoed into the session transcript.
if [ -z "$MCP_MOCK_TOKEN_1" ]; then
    export MCP_MOCK_TOKEN_1="test-api-key-123"
    echo "  Token 1: using default"
else
    echo "  Token 1: from environment"
fi
if [ -z "$MCP_MOCK_TOKEN_2" ]; then
    export MCP_MOCK_TOKEN_2="another-test-key-456"
    echo "  Token 2: using default"
else
    echo "  Token 2: from environment"
fi

# Change to the script directory
cd "$(dirname "$0")"

# Run the server
python main.py "$@"