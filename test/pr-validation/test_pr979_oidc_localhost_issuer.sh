#!/bin/bash
# PR #979 Validation Script: accept http OIDC issuers on RFC 6761 *.localhost names
#
# Drives the real Atlas app through an OIDC login against a minimal IdP whose
# issuer is http://keycloak.localhost:<port>/realms/atlas, checks that
# look-alike hosts are still refused, then runs the backend unit tests.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #979 Validation: http OIDC issuer on *.localhost"
echo "=========================================="

cd "$PROJECT_ROOT" || exit 1

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr979/localhost_issuer_login.py"
E2E=$?

echo ""
./test/run_tests.sh backend > /dev/null 2>&1
UNIT=$?
if [ $UNIT -eq 0 ]; then echo "PASSED: Backend unit tests"; else echo "FAILED: Backend unit tests"; fi

[ $E2E -eq 0 ] && [ $UNIT -eq 0 ] && exit 0 || exit 1
