#!/usr/bin/env bash
set -euo pipefail

# Simple helper to run the mock security check server with uvicorn.
# Assumes you are in the repo root with the uv venv already created/activated,
# or that `uvicorn` is otherwise available on PATH.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default API key for convenience if none is set
: "${SECURITY_CHECK_API_KEY:=test-key}"
export SECURITY_CHECK_API_KEY

exec uvicorn app:app --host 0.0.0.0 --port 8089
