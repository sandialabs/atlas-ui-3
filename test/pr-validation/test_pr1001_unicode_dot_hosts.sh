#!/bin/bash
# PR #1001 Validation Script: follow-up to #1000 (Unicode-dot hosts, testserver trust, refresh)
#
# Checks MCP OAuth refuses Unicode-dot spellings of internal hosts and that
# testserver gets no loopback trust, re-runs the #1000 end-to-end OIDC login
# driver, then runs the affected unit tests (including the refresh test).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #1001 Validation: Unicode-dot hosts and testserver trust"
echo "=========================================="

cd "$PROJECT_ROOT" || exit 1

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr1001/unicode_dot_hosts.py"
CHECKS=$?

echo ""
echo "PR #1000 end-to-end OIDC login driver:"
python "$SCRIPT_DIR/fixtures/pr1000/discovery_hardening_login.py"
E2E=$?

echo ""
echo "Unit tests (loopback, OIDC incl. refresh, MCP OAuth, Wormhole):"
python -m pytest -q atlas/tests/test_loopback.py atlas/tests/test_oidc_auth.py \
    atlas/tests/test_mcp_oauth.py atlas/tests/test_mcp_wormhole.py
UNIT=$?
if [ $UNIT -eq 0 ]; then echo "PASSED: unit tests"; else echo "FAILED: unit tests"; fi

[ $CHECKS -eq 0 ] && [ $E2E -eq 0 ] && [ $UNIT -eq 0 ] && exit 0 || exit 1
