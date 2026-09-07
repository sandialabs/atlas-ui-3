#!/bin/bash
# PR #892 Validation Script: OIDC login, confidential-client auth, delegated credentials
#
# Drives the real Atlas app against a real (minimal) OIDC provider started on
# localhost: discovery, JWKS, an RS256-signed ID token, and an RFC 8693
# token-exchange endpoint. Exercises the login redirect, the callback, an
# authenticated API call carrying no identity header and no proxy secret,
# a delegated token exchange, revocation, and logout.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #892 Validation: OIDC login and delegated credentials"
echo "=========================================="

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr892/oidc_end_to_end.py"
