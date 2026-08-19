#!/bin/bash
# Start the zero-trust mock policy server (default port 8099).
export ZERO_TRUST_PORT="${ZERO_TRUST_PORT:-8099}"
cd "$(dirname "$0")" && python main.py
