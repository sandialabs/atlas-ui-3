#!/bin/bash

# ATLAS RAG API Mock Server Runner
# This script starts the mock ATLAS RAG API service for testing
# the AtlasRAGClient without a real ATLAS RAG API.

set -e

# Change to the script directory
cd "$(dirname "$0")"

# Set defaults if not already set
export ATLAS_RAG_MOCK_PORT="${ATLAS_RAG_MOCK_PORT:-8002}"
export ATLAS_RAG_SHARED_KEY="${ATLAS_RAG_SHARED_KEY:-test-atlas-rag-token}"

echo "Starting ATLAS RAG API Mock Server..."
echo ""
echo "Configuration:"
echo "  Port: $ATLAS_RAG_MOCK_PORT"
echo "  Token: $ATLAS_RAG_SHARED_KEY"
echo ""
echo "Endpoints:"
echo "  GET  http://localhost:$ATLAS_RAG_MOCK_PORT/discover/datasources"
echo "  POST http://localhost:$ATLAS_RAG_MOCK_PORT/rag/completions"
echo "  GET  http://localhost:$ATLAS_RAG_MOCK_PORT/health"
echo "  Docs http://localhost:$ATLAS_RAG_MOCK_PORT/docs"
echo ""

# Run the server
python main.py "$@"
