#!/usr/bin/env bash
# List available LLM models via the headless CLI
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

echo "Listing available models..."
python cli.py list-models
