#!/bin/bash
# Test script for PR #1137: release MCP sessions on the runtime tool manager.
#
# Issue #1013: disconnect cleanup, finished-run teardown and conversation
# restore released MCP sessions on a package-level ``mcp_tool_manager``
# singleton -- a second ``MCPToolManager`` no client was ever served by -- so
# the runtime manager's per-conversation sessions and cached per-user clients
# leaked until restart.
#
# Test plan:
# - The legacy package-level singletons are gone: importing
#   ``atlas.modules.mcp_tools`` / ``atlas.modules.llm`` must not build an
#   ``mcp_tool_manager`` / ``llm_caller`` instance at import time.
# - A real uvicorn-hosted Atlas server and a real websocket client: a real
#   streaming LLM turn against the mock gateway, then the client hangs up.
#   The disconnect cleanup must run the new release path against the chat
#   service's tool manager without logging an release error, and the server
#   must stay healthy.
# - The real release path against the real runtime manager: a session and a
#   cached per-user client seeded into ``app_factory.get_mcp_manager()`` (the
#   same manager ``create_chat_service`` hands to every chat service) are both
#   gone after ``cleanup_disconnected_session`` runs against the real session
#   repository.
# - Run the backend unit suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MOCK_PORT="${PR1137_MOCK_PORT:-8031}"
APP_PORT="${PR1137_APP_PORT:-8032}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

WORK_DIR="$(mktemp -d)"
SERVER_LOG="$WORK_DIR/atlas.log"
MOCK_LOG="$WORK_DIR/mock.log"
MOCK_PID=""
APP_PID=""

cleanup() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# DEBUG_MODE=true refuses to start without an explicit loopback bind host, and
# the default chat-history DuckDB file may be locked by another process on this
# machine. The two env assignments below are scoped per command so the final
# backend-test step runs in the caller's environment, unchanged.

# ==========================================================================
# Check 1: no import-time MCPToolManager / LiteLLMCaller singletons
# ==========================================================================
print_header "Check 1: the legacy package-level singletons are gone"

python - <<'PY'
import sys

import atlas.modules.llm as llm_package
import atlas.modules.mcp_tools as mcp_tools_package

for package, name in ((mcp_tools_package, "mcp_tool_manager"), (llm_package, "llm_caller")):
    if hasattr(package, name):
        print(f"FAIL: {package.__name__} still builds the import-time {name!r} singleton")
        sys.exit(1)
    if name in getattr(package, "__all__", []):
        print(f"FAIL: {package.__name__} still exports {name!r} in __all__")
        sys.exit(1)

print("OK: no import-time tool-manager or LLM-caller singleton is built or exported")
PY
print_result $? "package-level singletons removed"

# ==========================================================================
# Check 2: a real server handles a real turn + websocket disconnect
# ==========================================================================
print_header "Check 2: disconnect cleanup runs the release path in the real server"

# --- mock LLM gateway ---
MOCK_LLM_PORT="$MOCK_PORT" python "$PROJECT_ROOT/mocks/llm-mock/main.py" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 40); do
    curl -s "${MOCK_URL}/health" >/dev/null 2>&1 && break
    sleep 0.5
done

# --- Atlas app, configured with a model pointed at the mock ---
cat > "$WORK_DIR/serve.py" <<PY
import logging

import uvicorn

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import ModelConfig

config = app_factory.get_config_manager()
config.app_settings.debug_mode = True
config.app_settings.test_user = "test@test.com"
config.llm_config.models["pr1137-mock"] = ModelConfig(
    model_name="pr1137-mock-model",
    model_url="${MOCK_URL}",
    api_key="test-key",
)

from atlas.main import app  # noqa: E402  (import after config is primed)

# The disconnect path reports at INFO; make sure it reaches the log.
logging.getLogger("atlas.main").setLevel(logging.INFO)
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.INFO)

uvicorn.run(app, host="127.0.0.1", port=${APP_PORT}, log_level="info")
PY

ATLAS_HOST="127.0.0.1" CHAT_HISTORY_DB_URL="duckdb:///$WORK_DIR/pr1137_chat_history.duckdb" \
    python "$WORK_DIR/serve.py" >"$SERVER_LOG" 2>&1 &
APP_PID=$!

APP_UP=1
for _ in $(seq 1 60); do
    if curl -s "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null 2>&1; then
        APP_UP=0
        break
    fi
    sleep 0.5
done

if [ "$APP_UP" -ne 0 ]; then
    echo "FAIL: Atlas app did not start"
    tail -40 "$SERVER_LOG"
    print_result 1 "real server runs a turn and survives the disconnect"
else
    python - "$APP_PORT" "$SERVER_LOG" <<'PY'
import json
import sys
import time

from websockets.sync.client import connect

port, log_path = sys.argv[1], sys.argv[2]

# A real streaming turn against the mock gateway, then hang up. The mock
# cannot emit tool calls, so no MCP session opens -- the point is that the
# real disconnect cleanup runs the new release path end to end in the real
# server process without erroring.
turn_completed = False
with connect(
    f"ws://127.0.0.1:{port}/ws",
    additional_headers={"X-User-Email": "test@test.com"},
) as ws:
    ws.send(json.dumps({
        "type": "chat",
        "content": "hello",
        "model": "pr1137-mock",
    }))
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            frame = json.loads(ws.recv(timeout=5))
        except TimeoutError:
            break
        if frame.get("type") in ("response_complete", "error"):
            turn_completed = True
            break

if not turn_completed:
    print("FAIL: the chat turn never reached a terminal frame")
    sys.exit(1)

# Let the server's disconnect cleanup land before reading the log.
time.sleep(2.0)
log = open(log_path, encoding="utf-8", errors="replace").read()

if "Error releasing MCP sessions on disconnect" in log:
    print("FAIL: the disconnect release path raised inside the server")
    for line in log.splitlines():
        if "Error releasing MCP sessions" in line:
            print(f"  {line}")
    sys.exit(1)

if "WebSocket connection closed for session" not in log:
    print("FAIL: the disconnect teardown did not run to completion")
    sys.exit(1)

print("OK: turn completed, disconnect cleanup ran the release path, no release error")
PY
    print_result $? "disconnect cleanup ran cleanly in the real server"

    # The server must still be serving everyone else.
    curl -s "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null 2>&1
    print_result $? "server stays healthy after the disconnect"
fi

# ==========================================================================
# Check 3: the real release path clears the real runtime manager
# ==========================================================================
print_header "Check 3: released entries are gone from the runtime manager"

CHAT_HISTORY_DB_URL="duckdb:///$WORK_DIR/pr1137_chat_history.duckdb" python - <<'PY'
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import atlas.main as main
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.mcp_tools.session_manager import ManagedSession

USER = "test@test.com"
CONVERSATION_ID = "pr1137-conv"


async def check() -> int:
    # The manager every chat service is handed -- the one that serves chat.
    manager = app_factory.get_mcp_manager()
    chat_service = app_factory.create_chat_service()

    session_id = uuid4()
    session = await chat_service.create_session(session_id, USER)
    session.context["conversation_id"] = CONVERSATION_ID
    await chat_service.persist_session(session)

    # Seed the runtime manager the way a tool call would: one persistent MCP
    # session and one cached per-user client scoped to the conversation.
    user_key = USER.lower()
    session_key = (user_key, CONVERSATION_ID, "pr1137-server")
    mcp_client = MagicMock()
    mcp_client.__aexit__ = AsyncMock(return_value=False)
    managed = ManagedSession(mcp_client)
    managed._opened = True
    manager._session_manager._sessions[session_key] = managed
    manager._session_manager._conv_index[CONVERSATION_ID] = {session_key}
    http_client = MagicMock()
    http_client.__aexit__ = AsyncMock(return_value=False)
    manager._user_clients[(user_key, "pr1137-server", CONVERSATION_ID)] = http_client

    # The real disconnect cleanup path, against the real session repository.
    await main.cleanup_disconnected_session(chat_service, session_id, USER, {"task": None})

    problems = []
    if session_key in manager._session_manager._sessions:
        problems.append("session entry survived on the runtime manager")
    if CONVERSATION_ID in manager._session_manager._conv_index:
        problems.append("conversation index entry survived")
    if (user_key, "pr1137-server", CONVERSATION_ID) in manager._user_clients:
        problems.append("cached per-user client survived")
    if not mcp_client.__aexit__.await_count:
        problems.append("the MCP session's client was never closed")
    if not http_client.__aexit__.await_count:
        problems.append("the cached per-user client was never closed")

    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("OK: release cleared the runtime manager's session and client entries")
    return 1 if problems else 0


try:
    sys.exit(asyncio.run(check()))
finally:
    # Leave the shared runtime manager clean regardless of the outcome.
    manager = app_factory.get_mcp_manager()
    asyncio.run(manager.release_sessions(CONVERSATION_ID, user_email=USER))
PY
print_result $? "release clears the runtime manager's entries"

# ==========================================================================
# Final: run backend unit tests
# ==========================================================================
print_header "Final: backend unit tests"
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
