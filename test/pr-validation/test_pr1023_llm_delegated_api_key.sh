#!/bin/bash
# PR #1023 Validation Script: delegated API keys for static LLM models
#
# Drives the real Atlas app against a real (minimal) OIDC provider and an
# OpenAI-compatible model endpoint, both started on localhost. A model with
# api_key_source: "delegated" must be called with a token exchanged for the
# signed-in user, never with the user's own token or a server key, and not at
# all once the user has signed out.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #1023 Validation: delegated API keys for LLM models"
echo "=========================================="

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr1023/delegated_model.py"
