#!/usr/bin/env bash
# Simple chat using CLI args
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

echo "Running a simple chat (CLI args)..."
python cli.py chat --model "gpt-4" --user-email "example-cli@company.com" "Hello from the CLI examples script"
