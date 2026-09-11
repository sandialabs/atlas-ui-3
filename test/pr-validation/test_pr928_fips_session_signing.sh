#!/bin/bash
# Test script for PR #928: Prevent SHA-1 session signing in MCP OAuth tests
#
# Test plan:
# - Verify MCP OAuth route tests pass with the FIPS-safe SessionMiddleware wrapper
# - Verify the FIPS session-signing regression scan passes on a clean tree
# - E2E guard check: seed a repository file with each prohibited SHA-1 pattern,
#   verify the regression scan fails, then verify the tree scans clean again
# - Verify the behavioral test asserts the session signer uses SHA-256 when
#   hashlib.sha1 is unavailable
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VIOLATION_FILE="mocks/_pr928_scan_violation_demo.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

cleanup() {
    rm -f "$PROJECT_ROOT/$VIOLATION_FILE"
    git -C "$PROJECT_ROOT" reset -q >/dev/null 2>&1
}
trap cleanup EXIT

cd "$PROJECT_ROOT" || exit 1

if [ ! -f .venv/bin/activate ]; then
    echo "FATAL: .venv not found. Run: uv venv && uv pip install -e \".[dev,mcp-demos]\""
    exit 1
fi
source .venv/bin/activate

echo "=========================================="
echo "1. FIPS-safe MCP OAuth route tests"
echo "=========================================="
python -m pytest atlas/tests/test_mcp_oauth_routes.py -q 2>&1 | tail -2
print_result $? "test_mcp_oauth_routes.py passes with the SHA-256 wrapper"

echo ""
echo "=========================================="
echo "2. Regression scan on clean tree"
echo "=========================================="
python -m pytest atlas/tests/test_fips_session_signing.py -q 2>&1 | tail -2
print_result $? "test_fips_session_signing.py passes on clean repository"

echo ""
echo "=========================================="
echo "3. Behavioral test: SHA-1 unavailable"
echo "=========================================="
python -m pytest atlas/tests/test_mcp_oauth_routes.py -q \
    -k "test_start_works_when_sha1_is_unavailable" 2>&1 | tail -2
print_result $? "OAuth start succeeds and signs with SHA-256 when hashlib.sha1 is unavailable"

echo ""
echo "=========================================="
echo "4. Guard fires on prohibited patterns"
echo "=========================================="
cat > "$VIOLATION_FILE" <<'EOF'
import hashlib as aliased
import importlib
from hashlib import sha1
from itsdangerous import TimestampSigner
from starlette.middleware import sessions
from starlette.middleware.sessions import SessionMiddleware

sha1(b"x")
aliased.sha1(b"x")
aliased.new("sha1")
TimestampSigner("k")
TimestampSigner("k", digest_method=None)
TimestampSigner("k", digest_method="sha1")
importlib.import_module("starlette.middleware.sessions")
EOF
git add -N "$VIOLATION_FILE" >/dev/null 2>&1

if python -m pytest atlas/tests/test_fips_session_signing.py -q > /tmp/pr928_scan_output_$$ 2>&1; then
    print_result 1 "scan FAILED to fire on seeded violations"
else
    echo "Scan output with seeded violations:"
    grep -E "^E\s+" /tmp/pr928_scan_output_$$ | sed 's/^E\s*//' | sort -u | head -15
    rm -f /tmp/pr928_scan_output_$$
    print_result 0 "scan correctly failed on seeded SHA-1 / starlette-sessions violations"
fi
rm -f "$VIOLATION_FILE"
git reset -q >/dev/null 2>&1

python -m pytest atlas/tests/test_fips_session_signing.py -q 2>&1 | tail -1
print_result $? "scan passes again after removing violations"

echo ""
echo "=========================================="
echo "5. Related browser-auth suites"
echo "=========================================="
python -m pytest atlas/tests/test_oidc_auth.py atlas/tests/test_globus_auth.py -q 2>&1 | tail -2
print_result $? "OIDC and Globus auth route tests still pass"

echo ""
echo "=========================================="
echo "6. Backend unit tests"
echo "=========================================="
./test/run_tests.sh backend > /tmp/pr928_backend_output_$$ 2>&1
BACKEND_STATUS=$?
tail -3 /tmp/pr928_backend_output_$$
rm -f /tmp/pr928_backend_output_$$
print_result $BACKEND_STATUS "backend unit test suite"

echo ""
echo "=========================================="
echo "Summary: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ]