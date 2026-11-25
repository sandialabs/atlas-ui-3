#!/bin/bash
# Test script for PR #1254: FastMCP 4 / MCP 2026-07-28 elicitation upgrade (issue #905)
#
# Test plan:
# - fastmcp 4.x is installed, the <4 cap and the Dependabot major-version ignore are gone
# - The real MCPToolManager discovers and runs the bundled demo servers over a
#   2026-07-28 connection: multi round-trip elicitation, background tasks,
#   image content, progress, prompts
# - The HTTP session_state_demo keeps state across tool calls with explicit sessions
# - The CLI lists the elicitation demo's tools
# - The elicitation/task unit and real-server tests pass
# - The backend test suite passes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES="$SCRIPT_DIR/fixtures/pr1254"

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

print_header "PR #1254: FastMCP 4 / MCP 2026-07-28 elicitation"

print_header "1. Dependency state"
FASTMCP_VERSION=$(python -c "import fastmcp; print(fastmcp.__version__)" 2>&1)
echo "Installed fastmcp: $FASTMCP_VERSION"
[ "$(echo "$FASTMCP_VERSION" | cut -d. -f1)" = "4" ]
print_result $? "fastmcp 4.x is installed"

grep -q '"fastmcp\[tasks\]>=4' "$PROJECT_ROOT/pyproject.toml" \
    && ! grep -q 'fastmcp\[tasks\].*<4' "$PROJECT_ROOT/pyproject.toml"
print_result $? "pyproject.toml requires fastmcp 4 and no longer caps below it"

! grep -q 'dependency-name: "fastmcp"' "$PROJECT_ROOT/.github/dependabot.yml"
print_result $? "Dependabot no longer ignores fastmcp major updates"

python - <<'PY'
import fastmcp
assert "sample" not in dir(fastmcp.Context), "Context.sample is back"
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
assert "2026-07-28" in MODERN_PROTOCOL_VERSIONS
PY
print_result $? "fastmcp speaks MCP 2026-07-28"

print_header "2. MCPToolManager against the bundled demo servers (real STDIO servers)"
cd "$ATLAS_DIR" && timeout 300 python "$FIXTURES/manager_e2e.py" "$FIXTURES/mcp.json" 2>/dev/null
print_result $? "Discovery, round-trip elicitation, tasks, images, progress, prompts"

print_header "3. session_state_demo over HTTP (explicit sessions)"
PORT=$(python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
WORK_DIR=$(mktemp -d)
cat > "$WORK_DIR/mcp.json" <<JSON
{"session_state_demo": {"url": "http://127.0.0.1:$PORT/mcp", "transport": "http", "groups": ["users"], "compliance_level": "Public"}}
JSON
cd "$ATLAS_DIR" && MCP_SESSION_STATE_PORT=$PORT python mcp/session_state_demo/main.py > "$WORK_DIR/server.log" 2>&1 &
SERVER_PID=$!
cleanup() {
    kill "$SERVER_PID" 2>/dev/null
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT
for _ in $(seq 1 40); do
    python -c "import socket; socket.create_connection(('127.0.0.1', $PORT), 0.5).close()" 2>/dev/null && break
    sleep 0.5
done
cd "$ATLAS_DIR" && timeout 120 python "$FIXTURES/session_state_e2e.py" "$WORK_DIR/mcp.json" 2>/dev/null
print_result $? "Cart survives across tool calls; unknown session id refused"
kill "$SERVER_PID" 2>/dev/null

print_header "4. CLI lists the elicitation demo tools"
LIST_OUTPUT=$(cd "$ATLAS_DIR" && timeout 120 python atlas_chat_cli.py --list-tools \
    --mcp-config "$ATLAS_DIR/config/mcp-example-configs/mcp-elicitation_demo.json" 2>&1 || true)
echo "$LIST_OUTPUT" | grep -q "elicitation_demo_multi_turn_survey" \
    && echo "$LIST_OUTPUT" | grep -q "elicitation_demo_choose_toppings"
print_result $? "atlas_chat_cli --list-tools shows the demo tools"

print_header "5. Elicitation, routing and task tests"
cd "$ATLAS_DIR" && python -m pytest \
    tests/test_mcp_elicitation_round_trip.py \
    tests/test_elicitation_routing.py \
    tests/test_elicitation_manager.py \
    tests/test_mcp_background_tasks.py \
    tests/test_mcp_authorization.py \
    -q --tb=short 2>&1 | tail -5
print_result "${PIPESTATUS[0]}" "Round-trip, routing, ownership, task and authorization tests"

print_header "6. Backend test suite"
# Under pytest-xdist, as CI's production-mode pass runs it: the serial suite
# does not fit the runner's five-minute cap on a busy development machine.
cd "$PROJECT_ROOT" && PYTEST_ARGS="${PYTEST_ARGS:--n 2}" ./test/run_tests.sh backend > "$WORK_DIR/backend.log" 2>&1
BACKEND_STATUS=$?
tail -5 "$WORK_DIR/backend.log"
print_result $BACKEND_STATUS "Backend test suite"

print_header "RESULTS: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
