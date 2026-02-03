#!/bin/bash

# Banyan Extractor Mock Server Runner
# This script starts the banyan-ingest based extraction service.
# Requires: banyan-ingest installed, endpoint_config.json configured.

set -e

echo "Starting Banyan Extractor Mock Server..."

# Change to the script directory
cd "$(dirname "$0")"

# Check for endpoint config
if [ ! -f "endpoint_config.json" ]; then
    echo "WARNING: endpoint_config.json not found."
    echo "Copy endpoint_config.json.example and configure your Nemotron endpoint."
    echo "Falling back to pypdf-only extraction."
fi

# Run the server
python banyan_extractor_service.py "$@"
