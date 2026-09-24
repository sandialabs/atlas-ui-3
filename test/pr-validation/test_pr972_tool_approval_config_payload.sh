#!/bin/bash
# Test script for PR #972: Fix /api/config tool approval filtering (prefixed keys)
#
# Issue #965: tool_approvals.tools in the /api/config payload was always empty
# because approval config keys are fully-qualified (<server>_<tool>) while the
# filter compared against bare MCP tool names.
#
# Test plan:
# - Start the real backend with a fixture mcp.json whose server marks
#   require_approval: ["delete_file"]
# - Call GET /api/config as a real HTTP request and assert the prefixed
#   approval entry (approval-demo_delete_file) is returned with
#   require_approval=true
# - Assert the ungated sibling (approval-demo_read_file) is absent
# - Assert unrelated/unauthorized keys (other-mcp_delete_file, bare
#   delete_file) are absent
# - Run the backend unit test suite

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr972"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Build an isolated config dir with the fixture MCP server -------------
WORK_DIR=$(mktemp -d -t atlas-pr972-XXXXXX)
CONFIG_DIR="$WORK_DIR/config"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
trap 'kill $BACKEND_PID 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"

cat > "$CONFIG_DIR/mcp.json" <<EOF
{
  "approval-demo": {
    "description": "PR 972 approval payload validation server",
    "command": ["$VENV_PY", "$FIXTURES_DIR/approval_demo_server.py"],
    "groups": ["users"],
    "require_approval": ["delete_file"]
  }
}
EOF

# --- Start the real backend -----------------------------------------------
PORT=8198
if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "FAILED: Port $PORT is already in use by another process"
    exit 1
fi

print_header "PR #972: /api/config tool approval payload"
echo "  Config dir: $CONFIG_DIR"
echo "  Backend port: $PORT"

export PORT=$PORT
export ATLAS_HOST="127.0.0.1"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$LOG_DIR"
export USE_MOCK_S3=true
export DEBUG_MODE=true
export SKIP_AUTHORIZATION_CHECKS=true
export FEATURE_TOOLS_ENABLED=true

cd "$ATLAS_DIR"
python main.py > "$WORK_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd "$PROJECT_ROOT"

echo "  Waiting for backend to start (PID $BACKEND_PID)..."
READY=0
for i in $(seq 1 45); do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "FAILED: Backend process exited unexpectedly; log tail:"
        tail -30 "$WORK_DIR/backend.log"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        echo "  Backend ready after ${i}s"
        READY=1
        break
    fi
    sleep 1
done
if [ "$READY" -ne 1 ]; then
    echo "FAILED: Backend did not become ready in time; log tail:"
    tail -30 "$WORK_DIR/backend.log"
    exit 1
fi

# --- Poll /api/config until the fixture server's tools are discovered ------
echo "  Calling GET /api/config (waiting for tool discovery)..."
PAYLOAD=""
for i in $(seq 1 45); do
    PAYLOAD=$(curl -s -H "X-User-Email: test@test.com" "http://127.0.0.1:$PORT/api/config")
    if echo "$PAYLOAD" | python3 -c "
import sys, json
d = json.load(sys.stdin)
servers = [g.get('server') for g in d.get('tools', [])]
sys.exit(0 if 'approval-demo' in servers else 1)
" 2>/dev/null; then
        break
    fi
    if [ "$i" -eq 45 ]; then
        echo "FAILED: approval-demo never appeared in /api/config tools"
        echo "$PAYLOAD" | python3 -m json.tool 2>/dev/null | head -40
        exit 1
    fi
    sleep 2
done

# --- Check 1: prefixed approval entry present ------------------------------
echo ""
echo "--- Check 1: approval-demo_delete_file present with require_approval ---"
echo "$PAYLOAD" | python3 -c "
import sys, json
d = json.load(sys.stdin)
approvals = d['tool_approvals']['tools']
entry = approvals.get('approval-demo_delete_file')
assert entry is not None, f'missing approval-demo_delete_file; got keys: {sorted(approvals)}'
assert entry['require_approval'] is True, f'unexpected entry: {entry}'
print('approval-demo_delete_file:', entry)
"
print_result $? "Prefixed approval entry present in /api/config payload"

# --- Check 2: ungated sibling absent ---------------------------------------
echo "--- Check 2: approval-demo_read_file absent (not in require_approval) ---"
echo "$PAYLOAD" | python3 -c "
import sys, json
d = json.load(sys.stdin)
approvals = d['tool_approvals']['tools']
assert 'approval-demo_read_file' not in approvals, f'unexpected key: {sorted(approvals)}'
print('keys:', sorted(approvals))
"
print_result $? "Ungated sibling excluded from payload"

# --- Check 3: unrelated / bare keys never leak -----------------------------
echo "--- Check 3: other-mcp_delete_file and bare delete_file absent ---"
echo "$PAYLOAD" | python3 -c "
import sys, json
d = json.load(sys.stdin)
approvals = d['tool_approvals']['tools']
for bad in ('other-mcp_delete_file', 'delete_file'):
    assert bad not in approvals, f'unexpected key {bad}; got: {sorted(approvals)}'
print('keys:', sorted(approvals))
"
print_result $? "Unrelated and bare keys excluded from payload"

# --- Check 4: require_approval_by_default reflects config default ----------
echo "--- Check 4: require_approval_by_default present in payload ---"
echo "$PAYLOAD" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert isinstance(d['tool_approvals']['require_approval_by_default'], bool)
print('require_approval_by_default:', d['tool_approvals']['require_approval_by_default'])
"
print_result $? "require_approval_by_default exposed"

# --- Final: run the backend unit test suite --------------------------------
print_header "Backend unit test suite"
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests (./test/run_tests.sh backend)"

# --- Summary ---------------------------------------------------------------
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
fi
echo -e "${GREEN}ALL TESTS PASSED${NC}"
exit 0