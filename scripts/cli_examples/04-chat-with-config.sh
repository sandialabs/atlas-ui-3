#!/usr/bin/env bash
# Chat example using a YAML config file
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT_DIR/backend"

EXAMPLE_CONFIG="$ROOT_DIR/scripts/cli_examples/example-config.yaml"

echo "Running chat using config: $EXAMPLE_CONFIG"
python cli.py --config "$EXAMPLE_CONFIG" chat "Hello using example config"
