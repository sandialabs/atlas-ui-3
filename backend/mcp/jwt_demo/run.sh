#!/bin/bash
# Run JWT Demo MCP server as HTTP on port 8001
cd "$(dirname "$0")"
source ../../../.venv/bin/activate 2>/dev/null || source ../../.venv/bin/activate 2>/dev/null
echo "Starting JWT Demo MCP server on http://localhost:8001/mcp"
uvicorn main:mcp.http_app --factory --host 0.0.0.0 --port 8001
