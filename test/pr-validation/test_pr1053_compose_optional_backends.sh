#!/bin/bash
# PR #1053: bundled MinIO and PostgreSQL are opt-in Compose files (issue #1052).
# The Compose render checks live in test/compose_render_checks.sh so CI can
# run them on the host; this script runs those plus the backend unit tests.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo "PASSED: $2"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate

bash "$PROJECT_ROOT/test/compose_render_checks.sh"
print_result $? "Compose backend combination render checks"

# Final: run backend unit tests.
CAPABILITY_TOKEN_SECRET="${CAPABILITY_TOKEN_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')}" \
    MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')}" \
    ./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
