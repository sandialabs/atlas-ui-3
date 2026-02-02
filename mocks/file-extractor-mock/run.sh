#!/usr/bin/env bash
# Start the Mock File Extractor Service on port 8010
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Install dependencies if needed
if ! python -c "import fastapi, uvicorn, pypdf" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

echo "Starting Mock File Extractor Service on http://127.0.0.1:8010"
python main.py
