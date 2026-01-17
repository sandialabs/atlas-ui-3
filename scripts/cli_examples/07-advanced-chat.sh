#!/usr/bin/env bash
# Advanced chat example demonstrating more options
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

EXAMPLE_CONFIG="$ROOT_DIR/scripts/cli_examples/example-config.yaml"

echo "Advanced chat: using config and additional CLI args"
python cli.py --config "$EXAMPLE_CONFIG" chat --model "gpt-4" --user-email "advanced-cli@company.com" --tool "filesystem" "Advanced prompt: combine config tools with CLI-provided tool"
