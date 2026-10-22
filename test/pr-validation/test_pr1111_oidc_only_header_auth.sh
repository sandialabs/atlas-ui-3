#!/bin/bash
# PR #1111 Validation Script: OIDC-only mode (FEATURE_HEADER_AUTH_ENABLED=false)
#
# Drives the real Atlas app, with header auth disabled, against the minimal
# OIDC provider from the PR #892 fixture. A valid proxy secret plus an
# arbitrary identity header must not authenticate on HTTP or /ws; a real OIDC
# login must; signed download links must keep working.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #1111 Validation: OIDC-only mode cannot be bypassed with identity headers"
echo "=========================================="

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr1111/oidc_only_end_to_end.py"
