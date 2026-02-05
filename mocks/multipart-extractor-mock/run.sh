#!/bin/bash
# Start the mock multipart file extractor on port 8011
#
# Usage:
#   bash mocks/multipart-extractor-mock/run.sh
#   bash mocks/multipart-extractor-mock/run.sh 9000   # custom port

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT="${1:-8011}"

# Activate venv if available
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

echo "Starting mock multipart extractor on port $PORT"
echo "Test with: curl -F 'file=@document.pdf' http://127.0.0.1:$PORT/extract"
echo ""

python "$SCRIPT_DIR/multipart_extractor_mock.py" --port "$PORT"
