#!/bin/bash
# End-to-end test for atlas_chat_cli.py
#
# Tests are split into two groups:
#   - Offline tests: no LLM API key needed (arg parsing, help, error codes)
#   - Online tests: require a valid LLM API key and working backend config
#
# Usage:
#   bash test/test_cli_e2e.sh            # run all tests
#   bash test/test_cli_e2e.sh offline    # run only offline tests
#   bash test/test_cli_e2e.sh online     # run only online tests
#
# NOT included in run_tests.sh -- run manually.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

# Use the project venv python by default
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
else
    PYTHON="${PYTHON:-python}"
fi

MODE="${1:-all}"
PASS=0
FAIL=0
TMPDIR=$(mktemp -d)

cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

TEST_TIMEOUT="${TEST_TIMEOUT:-30}"

run_test() {
    local name="$1"
    shift
    echo -n "  $name ... "
    if timeout "$TEST_TIMEOUT" "$@" ; then
        echo "PASS"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
        echo ""
        echo "ABORTED: test '$name' failed"
        exit 1
    fi
}

echo "================================================================"
echo "Atlas CLI End-to-End Tests (mode: $MODE)"
echo "================================================================"
echo "Using python: $PYTHON"
echo ""

# ======================================================================
# ONLINE TESTS -- require a working LLM API key and MCP servers
# ======================================================================
if [ "$MODE" = "all" ] || [ "$MODE" = "online" ]; then
    echo "--- Online tests (require LLM API key) ---"
    echo ""

    # --list-tools prints discovered tools
    run_test "--list-tools prints available tools" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --list-tools 2>/dev/null | grep -q 'calculator_evaluate'"

    # 3. Basic prompt returns JSON with message
    run_test "Basic prompt returns JSON with message" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py 'Say hello in one word' --json 2>/dev/null | $PYTHON -c 'import sys,json; d=json.load(sys.stdin); assert len(d[\"message\"])>0, \"empty message\"'"

    # 4. JSON output has all required keys
    run_test "JSON output has required keys" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py 'Say hi' --json 2>/dev/null | $PYTHON -c '
import sys, json
d = json.load(sys.stdin)
for k in (\"message\", \"tool_calls\", \"files\", \"session_id\"):
    assert k in d, f\"missing key: {k}\"
'"

    # 5. Output to file
    run_test "Output written to file with -o" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py 'Say hello' -o '$TMPDIR/out.txt' 2>/dev/null && [ -s '$TMPDIR/out.txt' ]"

    # 7. Stdin prompt
    run_test "Read prompt from stdin" \
        bash -c "echo 'Say hi' | (cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py - --json 2>/dev/null) | $PYTHON -c 'import sys,json; d=json.load(sys.stdin); assert len(d[\"message\"])>0'"

    # 8. Session reuse returns same session_id
    SESSION_ID="550e8400-e29b-41d4-a716-446655440000"
    run_test "Session reuse with --session-id" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py 'Say hi' --session-id $SESSION_ID --json 2>/dev/null | $PYTHON -c '
import sys, json
d = json.load(sys.stdin)
assert d[\"session_id\"] == \"$SESSION_ID\", f\"wrong session_id: {d[\"session_id\"]}\"
'"

    echo ""
fi

# ======================================================================
# OFFLINE TESTS -- no LLM API key or running backend required
# ======================================================================
if [ "$MODE" = "all" ] || [ "$MODE" = "offline" ]; then
    echo "--- Offline tests ---"
    echo ""

    # 0. --help exits cleanly and shows usage
    run_test "--help prints usage and exits 0" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q 'usage:'"

    # 1. No prompt gives exit code 2
    run_test "No prompt returns exit code 2" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py 2>/dev/null; [ \$? -eq 2 ]"

    # 2. --help shows all expected flags
    run_test "--help lists --model flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--model'"

    run_test "--help lists --agent flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--agent'"

    run_test "--help lists --tools flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--tools'"

    run_test "--help lists --json flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--json'"

    run_test "--help lists --output flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--output'"

    run_test "--help lists --session-id flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--session-id'"

    run_test "--help lists --max-steps flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--max-steps'"

    run_test "--help lists --user-email flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--user-email'"

    run_test "--help lists --quiet flag" \
        bash -c "cd $BACKEND_DIR && $PYTHON atlas_chat_cli.py --help 2>/dev/null | grep -q -- '--quiet'"

    echo ""
fi

echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"

[ "$FAIL" -eq 0 ]
