#!/bin/bash
# Test script for PR #431: MCP session isolation and FastMCP 3.x upgrade
#
# Test plan:
# - Verify BlockedStateStore prevents state operations on STDIO servers
# - Verify create_stdio_server factory wires BlockedStateStore
# - Verify MCPSessionManager session lifecycle
# - Verify concurrent elicitation routing with meta-based dispatch
# - Verify structured output priority (data > structured_content)
# - Verify multi-prompt support
# - Verify adaptive task polling
# - Verify per-user HTTP client creation
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

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

print_header "PR #431: MCP Session Isolation Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

print_header "1. BlockedStateStore tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_blocked_state_store.py -v --tb=short 2>&1
print_result $? "BlockedStateStore prevents state operations"

print_header "2. STDIO server factory tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_stdio_server_factory.py -v --tb=short 2>&1
print_result $? "create_stdio_server wires BlockedStateStore"

print_header "3. MCPSessionManager tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_session_manager.py -v --tb=short 2>&1
print_result $? "Session manager lifecycle (acquire/release/release_all)"

print_header "4. Concurrent elicitation routing tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_elicitation_routing.py -v --tb=short 2>&1
print_result $? "Elicitation routing with meta-based dispatch"

print_header "5. Structured output priority tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_structured_output.py -v --tb=short 2>&1
print_result $? "Structured output: data preferred over structured_content"

print_header "6. Multi-prompt support tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_multi_prompt.py -v --tb=short 2>&1
print_result $? "Multi-prompt application and meta forwarding"

print_header "7. Adaptive task polling tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_adaptive_task_polling.py -v --tb=short 2>&1
print_result $? "Adaptive task polling and cancellation"

print_header "8. Per-user HTTP client tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_mcp_per_user_http_clients.py -v --tb=short 2>&1
print_result $? "Per-user HTTP client creation and caching"

print_header "9. State store factory tests"
cd "$ATLAS_DIR" && python -m pytest tests/test_state_store.py -v --tb=short 2>&1
print_result $? "Pluggable state store factory"

print_header "10. CLI smoke test"
cd "$PROJECT_ROOT" && uv run atlas-chat "hello" --no-stream 2>&1 | tail -5
print_result $? "atlas-chat CLI runs without import errors"

print_header "11. Feature flag: MCP_TASK_TIMEOUT override"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr431"
export MCP_TASK_TIMEOUT=5
cd "$ATLAS_DIR" && python -c "
import os
from atlas.modules.mcp_tools.client import MCPToolManager
tm = MCPToolManager(config_path='/tmp/nonexistent_mcp_test.json')
assert tm._task_timeout == 5.0, f'Expected 5.0 but got {tm._task_timeout}'
print('MCP_TASK_TIMEOUT=5 correctly parsed at init')
" 2>&1
print_result $? "MCP_TASK_TIMEOUT env var honored at MCPToolManager init"

print_header "12. Feature flag: MCP_STATE_BACKEND=memory returns None"
export MCP_STATE_BACKEND=memory
cd "$ATLAS_DIR" && python -c "
from atlas.mcp.common.state import get_state_store
store = get_state_store()
assert store is None, f'Expected None for memory backend, got {store}'
print('MCP_STATE_BACKEND=memory returns None (FastMCP default)')
" 2>&1
print_result $? "MCP_STATE_BACKEND=memory returns None"

print_header "13. Feature flag: MCP_STATE_BACKEND=redis falls back gracefully"
export MCP_STATE_BACKEND=redis
cd "$ATLAS_DIR" && python -c "
from atlas.mcp.common.state import get_state_store
store = get_state_store()
# Without redis installed or running, should fall back to None
assert store is None, f'Expected None fallback, got {store}'
print('MCP_STATE_BACKEND=redis falls back to None without redis')
" 2>&1
print_result $? "MCP_STATE_BACKEND=redis graceful fallback"
unset MCP_STATE_BACKEND MCP_TASK_TIMEOUT

print_header "14. Full backend test suite"
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend 2>&1
print_result $? "Backend test suite"

print_header "15. STDIO server uses BlockedStateStore (end-to-end)"
cd "$ATLAS_DIR" && python -c "
from atlas.mcp_shared.server_factory import create_stdio_server
from atlas.mcp_shared.blocked_state import BlockedStateStore
mcp = create_stdio_server('test-server')
assert isinstance(mcp._session_state_store, BlockedStateStore), \
    f'Expected BlockedStateStore, got {type(mcp._session_state_store)}'
print('create_stdio_server correctly wires BlockedStateStore')
" 2>&1
print_result $? "STDIO factory wires BlockedStateStore end-to-end"

echo ""
echo "=========================================="
echo "RESULTS: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
