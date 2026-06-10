#!/bin/bash
# Test script for PR #541: Fix MCP dead sessions not evicted on server-side termination.
#
# Test plan:
# - _is_session_terminated_error matches the documented markers
#   ("Session terminated", "Session not found", "Invalid session ID")
# - _is_session_terminated_error walks the exception chain so a wrapped
#   FastMCP-level error still triggers eviction
# - _is_session_terminated_error ignores unrelated errors (e.g. ValueError)
# - End-to-end: MCPToolManager.execute_tool evicts the cached session via
#   _session_manager.release(conversation_id, server_name) when the underlying
#   FastMCP client raises "Session terminated", so the next call can open a
#   fresh session
# - execute_tool does NOT evict the session on unrelated tool errors
# - Backend MCP tool-result parsing suite (which now carries the
#   TestSessionTerminatedEviction class) passes

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

print_header "PR #541: MCP dead-session eviction validation"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. Marker matching — direct exception
# ==========================================
print_header "1. _is_session_terminated_error matches documented markers"

RESULT=$(python3 -c '
from atlas.modules.mcp_tools.client import _is_session_terminated_error

cases = [
    Exception("Session terminated by server"),
    Exception("404 Not Found: Session not found"),
    Exception("HTTP 400 Invalid session id: abc123"),
]
if all(_is_session_terminated_error(e) for e in cases):
    print("OK")
else:
    results = [(str(e), _is_session_terminated_error(e)) for e in cases]
    print(f"BAD {results!r}")
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "All three markers detected (got: $RESULT)"

# ==========================================
# 2. Marker matching — walks __cause__ / __context__ chain
# ==========================================
print_header "2. _is_session_terminated_error walks exception chain"

RESULT=$(python3 -c '
from atlas.modules.mcp_tools.client import _is_session_terminated_error

inner = Exception("Session terminated")
outer = RuntimeError("MCP call failed")
outer.__cause__ = inner
chained_ok = _is_session_terminated_error(outer)

ctx_inner = Exception("Session not found")
ctx_outer = RuntimeError("Upstream failure")
ctx_outer.__context__ = ctx_inner
ctx_ok = _is_session_terminated_error(ctx_outer)

if chained_ok and ctx_ok:
    print("OK")
else:
    print(f"BAD chained={chained_ok} context={ctx_ok}")
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Chained exceptions detected via __cause__ and __context__ (got: $RESULT)"

# ==========================================
# 3. Marker matching — rejects unrelated errors
# ==========================================
print_header "3. _is_session_terminated_error ignores unrelated errors"

RESULT=$(python3 -c '
from atlas.modules.mcp_tools.client import _is_session_terminated_error

cases = [
    ValueError("Tool argument validation failed"),
    RuntimeError("Connection refused"),
    Exception("Timeout after 120s"),
]
if not any(_is_session_terminated_error(e) for e in cases):
    print("OK")
else:
    results = [(str(e), _is_session_terminated_error(e)) for e in cases]
    print(f"BAD {results!r}")
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Unrelated errors not matched (got: $RESULT)"

# ==========================================
# 4. End-to-end: execute_tool evicts session on termination
# ==========================================
print_header "4. execute_tool evicts cached session on 'Session terminated'"

RESULT=$(python3 -c '
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.domain.messages.models import ToolCall


async def main():
    server_config = {"url": "http://localhost:8001/mcp", "transport": "http"}

    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.mcp_config.servers = {"srv": Mock()}
        mock_cm.mcp_config.servers["srv"].model_dump.return_value = server_config
        mock_cm.app_settings.mcp_call_timeout = 120

        mgr = MCPToolManager()
        mgr.servers_config = {"srv": server_config}

        with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
            c = MockClient.return_value
            c.__aenter__.return_value = c
            c.is_connected = MagicMock(return_value=True)
            c.call_tool = AsyncMock(side_effect=Exception("Session terminated"))

            await mgr.initialize_clients()

            tool = Mock()
            tool.name = "get_state"
            mgr._tool_index = {"srv_get_state": {"server": "srv", "tool": tool}}

            release_mock = AsyncMock()
            mgr._session_manager.release = release_mock

            result = await mgr.execute_tool(
                ToolCall(id="c1", name="srv_get_state", arguments={}),
                context={
                    "conversation_id": "conv-pr541",
                    "user_email": "test@example.com",
                    "update_callback": None,
                },
            )

            if result.success:
                return "BAD success=True"
            if not release_mock.await_count == 1:
                return f"BAD release awaited {release_mock.await_count}x"
            args = release_mock.await_args.args
            if args != ("conv-pr541", "srv"):
                return f"BAD release_args={args!r}"
            return "OK"


print(asyncio.run(main()))
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Session evicted with (conv-pr541, srv) on termination (got: $RESULT)"

# ==========================================
# 5. End-to-end: execute_tool does NOT evict on unrelated errors
# ==========================================
print_header "5. execute_tool does NOT evict on unrelated tool errors"

RESULT=$(python3 -c '
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.domain.messages.models import ToolCall


async def main():
    server_config = {"url": "http://localhost:8001/mcp", "transport": "http"}

    with patch("atlas.modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.mcp_config.servers = {"srv": Mock()}
        mock_cm.mcp_config.servers["srv"].model_dump.return_value = server_config
        mock_cm.app_settings.mcp_call_timeout = 120

        mgr = MCPToolManager()
        mgr.servers_config = {"srv": server_config}

        with patch("atlas.modules.mcp_tools.client.Client") as MockClient:
            c = MockClient.return_value
            c.__aenter__.return_value = c
            c.is_connected = MagicMock(return_value=True)
            c.call_tool = AsyncMock(side_effect=ValueError("Tool argument validation failed"))

            await mgr.initialize_clients()

            tool = Mock()
            tool.name = "get_state"
            mgr._tool_index = {"srv_get_state": {"server": "srv", "tool": tool}}

            release_mock = AsyncMock()
            mgr._session_manager.release = release_mock

            result = await mgr.execute_tool(
                ToolCall(id="c2", name="srv_get_state", arguments={}),
                context={
                    "conversation_id": "conv-pr541",
                    "user_email": "test@example.com",
                    "update_callback": None,
                },
            )

            if result.success:
                return "BAD success=True"
            if release_mock.await_count != 0:
                return f"BAD release awaited {release_mock.await_count}x"
            return "OK"


print(asyncio.run(main()))
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "No eviction on unrelated errors (got: $RESULT)"

# ==========================================
# 6. Regression suite: TestSessionTerminatedEviction
# ==========================================
print_header "6. Regression suite: test_mcp_tool_result_parsing.py"

cd "$ATLAS_DIR"
PYTHONPATH="$PROJECT_ROOT" python3 -m pytest \
    tests/test_mcp_tool_result_parsing.py::TestSessionTerminatedEviction \
    -x -q 2>&1
print_result $? "TestSessionTerminatedEviction passes"

cd "$PROJECT_ROOT"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
