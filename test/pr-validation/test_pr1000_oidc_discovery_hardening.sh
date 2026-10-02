#!/bin/bash
# PR #1000 Validation Script: OIDC discovery hardening and shared loopback helper (#983)
#
# Drives the real Atlas OIDC login route against a minimal IdP: a malformed
# issuer must fail as discovery_failed (not a 500), plaintext or relative
# endpoints in the discovery document are refused, and the http://localhost
# development path still works. Then runs the affected unit tests.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #1000 Validation: OIDC discovery hardening"
echo "=========================================="

cd "$PROJECT_ROOT" || exit 1

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr1000/discovery_hardening_login.py"
E2E=$?

echo ""
echo "Unit tests (loopback, OIDC, MCP OAuth, Wormhole):"
python -m pytest -q atlas/tests/test_loopback.py atlas/tests/test_oidc_auth.py \
    atlas/tests/test_mcp_oauth.py atlas/tests/test_mcp_wormhole.py
UNIT=$?
if [ $UNIT -eq 0 ]; then echo "PASSED: unit tests"; else echo "FAILED: unit tests"; fi

[ $E2E -eq 0 ] && [ $UNIT -eq 0 ] && exit 0 || exit 1
