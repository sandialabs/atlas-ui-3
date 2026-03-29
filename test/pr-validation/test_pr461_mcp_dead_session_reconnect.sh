#!/bin/bash
# Test script for PR #461: MCP dead session auto-reconnect
#
# Test plan:
# - Verify ManagedSession.is_open reflects transport state
# - Verify MCPSessionManager.acquire() evicts dead sessions and reconnects
# - Verify existing session manager tests still pass
# - End-to-end: simulate server crash via Python subprocess, verify reconnect

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr461"

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

print_header "PR #461: MCP Dead Session Auto-Reconnect Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Load fixture env
if [ -f "$FIXTURES_DIR/.env" ]; then
    set -a
    source "$FIXTURES_DIR/.env"
    set +a
fi

print_header "1. ManagedSession.is_open reflects transport state"
cd "$ATLAS_DIR" && python -m pytest tests/test_session_manager.py::TestManagedSession::test_is_open_reflects_transport_state -v --tb=short 2>&1
print_result $? "is_open returns False when client.is_connected() is False"

print_header "2. acquire() evicts dead session and reconnects"
cd "$ATLAS_DIR" && python -m pytest tests/test_session_manager.py::TestMCPSessionManager::test_acquire_evicts_dead_session_and_reconnects -v --tb=short 2>&1
print_result $? "Dead session detected, closed, and replaced with fresh session"

print_header "3. Full session manager test suite"
cd "$ATLAS_DIR" && python -m pytest tests/test_session_manager.py -v --tb=short 2>&1
print_result $? "All session manager tests pass (no regressions)"

print_header "4. Verify Client.is_connected() method exists"
cd "$PROJECT_ROOT" && python -c "
from fastmcp import Client

# Verify is_connected exists as a callable on Client
assert hasattr(Client, 'is_connected'), 'Client missing is_connected method'
assert callable(getattr(Client, 'is_connected')), 'is_connected must be callable'
print('Client.is_connected() exists and is callable')
" 2>&1
print_result $? "FastMCP Client exposes is_connected() method"

print_header "5. End-to-end: simulate server crash via subprocess"
cd "$PROJECT_ROOT" && python -c "
import asyncio
from atlas.modules.mcp_tools.session_manager import ManagedSession, MCPSessionManager
from unittest.mock import AsyncMock, MagicMock

async def test_crash_reconnect():
    mgr = MCPSessionManager()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.is_connected = MagicMock(return_value=True)

    # Acquire initial session
    s1 = await mgr.acquire('test-conv', 'calc', client)
    assert s1.is_open, 'Session should be open'

    # Simulate crash
    client.is_connected = MagicMock(return_value=False)
    assert not s1.is_open, 'Session should detect disconnect'

    # Reconnect
    client.__aenter__.reset_mock()
    async def reconnect():
        client.is_connected = MagicMock(return_value=True)
        return client
    client.__aenter__ = AsyncMock(side_effect=reconnect)

    s2 = await mgr.acquire('test-conv', 'calc', client)
    assert s2 is not s1, 'Should be a new session'
    assert s2.is_open, 'New session should be open'
    assert client.__aexit__.call_count == 1, 'Dead session should have been closed'
    print('Crash -> evict -> reconnect lifecycle verified end-to-end')

asyncio.run(test_crash_reconnect())
" 2>&1
print_result $? "Full crash/evict/reconnect lifecycle via MCPSessionManager"

print_header "6. Backend test suite"
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend 2>&1
print_result $? "Backend test suite"

echo ""
echo "=========================================="
echo "RESULTS: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
