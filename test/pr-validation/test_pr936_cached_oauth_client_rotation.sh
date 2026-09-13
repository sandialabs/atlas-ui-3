#!/bin/bash
# Test script for PR #936: cached per-user MCP client keeps a rotated OAuth
# token, causing persistent 401s (issue #935).
#
# Covers the test plan:
#   1. A cached per-user client is rebuilt when the stored token rotates
#      (fingerprint check) -- a rotated credential is never replayed.
#   2. refresh_stored_token invalidates every cached client for the
#      (user, server) pair across all conversations.
#   3. A 401 from a provider that retired the credential server-side triggers
#      exactly one forced-refresh + retry and then succeeds.
#   4. When the refresh itself refuses, the exhausted retry surfaces a friendly
#      AuthenticationRequiredException (no raw upstream URL) with the OAuth
#      reconnect URL for oauth servers.
#
# The scenarios run against a REAL FastMCP streamable-HTTP server behind a
# bearer gate and a REAL localhost OAuth provider that rotates refresh tokens
# and retires the previous access token on every refresh, driven through the
# production MCPToolManager and mcp_oauth_service code paths. Backend unit
# tests run as the final step.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

python -c "import atlas.modules.mcp_tools.client" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "FAILED: atlas is not importable on this interpreter ($(which python))"
    exit 1
fi
print_result $? "atlas importable"

export MCP_TOKEN_ENCRYPTION_KEY="pr936-validation-key-0123456789abcdef"
export MCP_OAUTH_REDIRECT_BASE_URL="http://127.0.0.1:59999"
# Keep the e2e scenario off the dev server's DuckDB file (a second process
# holding the lock would fail the app-factory init with a confusing error).
export CHAT_HISTORY_DB_URL="duckdb:///data/chat_history_pr936_validation.db"

python test/pr-validation/fixtures/pr936/rotating_oauth_e2e.py
E2E_RC=$?
print_result $E2E_RC "rotation/401-recovery scenarios against a live MCP+OAuth pair"

# Final: run the focused unit tests and the backend suite.
python -m pytest atlas/tests/test_mcp_tool_401_retry.py atlas/tests/test_mcp_client_auth.py -q > /dev/null 2>&1
print_result $? "Focused unit tests (#935 regression suite)"

./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1