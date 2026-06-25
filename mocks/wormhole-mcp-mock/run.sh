#!/bin/bash
# Wormhole MCP Mock Server Runner
set -e
cd "$(dirname "$0")"
echo "Starting Wormhole MCP mock server..."
python main.py "$@"
