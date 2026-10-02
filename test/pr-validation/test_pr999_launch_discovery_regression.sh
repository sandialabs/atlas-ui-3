#!/bin/bash
# PR #999: exercise launch-owned discovery over the real WebSocket and REST API.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate

if pgrep -f '[u]vicorn main:app' > /dev/null; then
    echo "Refusing to restart an existing backend; stop it before running this validation."
    exit 1
fi

WORK="$(mktemp -d)"
FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr949"
BACKEND_PID=""
MOCK_PID=""
cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        pkill -P "$BACKEND_PID" 2>/dev/null || true
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$MOCK_PID" ]; then
        kill "$MOCK_PID" 2>/dev/null || true
        wait "$MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

free_port() {
    python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}
wait_for_health() {
    for _ in $(seq 1 60); do
        curl -sf "$1/health" > /dev/null && return 0
        sleep 1
    done
    cat "$WORK"/*.log
    return 1
}

export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$WORK/config/"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"
export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
cat > "$WORK/test.env" <<EOF
PORT=$ATLAS_PORT
DEBUG_MODE=true
FEATURE_CHAT_HISTORY_ENABLED=true
FEATURE_AGENT_MODE_AVAILABLE=true
FEATURE_TOOLS_ENABLED=true
FEATURE_WORKSPACES_ENABLED=true
FEATURE_ATLAS_LAUNCH_ENABLED=true
FEATURE_RAG_ENABLED=false
FEATURE_AGENT_PORTAL_ENABLED=false
USE_MOCK_S3=true
REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false
FORCE_TOOL_APPROVAL_GLOBALLY=false
MAX_CONCURRENT_RUNS_PER_USER=50
CHAT_HISTORY_DB_URL=duckdb:///$WORK/data/chat_history.db
APP_LOG_DIR=$WORK/logs
APP_CONFIG_DIR=$WORK/config
EOF
python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_health "http://127.0.0.1:$MOCK_LLM_PORT"
bash "$PROJECT_ROOT/agent_start.sh" --backend-only --env-file "$WORK/test.env" > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
wait_for_health "http://127.0.0.1:$ATLAS_PORT/api"

python - <<'PY'
import asyncio
import json
import os
import urllib.request

import websockets


async def main():
    port = os.environ["ATLAS_PORT"]
    headers = {"X-User-Email": "owner@example.com", "Content-Type": "application/json"}
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/workspaces", headers=headers,
                                    data=json.dumps({
        "name": "Research",
        "description": "Launch discovery regression validation",
        "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
    }).encode())
    with urllib.request.urlopen(request, timeout=15) as response:
        assert response.status == 200
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws", additional_headers=headers) as ws:
        for plan, tools in [
            ("launch_only", ["atlas_launch"]),
            ("same_step", ["atlas_launch", "atlas_discover_launch_options"]),
        ]:
            await ws.send(json.dumps({
                "type": "chat", "content": f"plan={plan}", "model": "scripted-mock",
                "save_mode": "server", "selected_tools": tools, "agent_mode": True,
            }))
            results = {}
            parent_run = None
            async with asyncio.timeout(90):
                while True:
                    frame = json.loads(await ws.recv())
                    kind = frame.get("type")
                    if kind == "run_started" and not frame.get("parent_run_id"):
                        parent_run = frame["run_id"]
                    elif kind == "tool_approval_request":
                        await ws.send(json.dumps({
                            "type": "tool_approval_response", "approved": True,
                            "tool_call_id": frame["tool_call_id"], "run_id": frame.get("run_id"),
                        }))
                    elif kind == "tool_complete" and frame.get("run_id") in (None, parent_run):
                        results[frame["tool_name"]] = frame
                    elif kind == "run_status":
                        run = frame.get("run") or {}
                        if run.get("run_id") == parent_run and run.get("status") in ("completed", "failed"):
                            assert run["status"] == "completed", frame
                            break
                    elif kind == "error":
                        raise AssertionError(frame)
            launched = results.get("atlas_launch")
            assert launched and launched.get("success"), results
            handle = json.loads(launched["result"].splitlines()[-1])
            assert handle["run_id"] and handle["conversation_id"], handle
            assert handle["workspace"] == "Research", handle
            if plan == "launch_only":
                assert "atlas_discover_launch_options" not in results
            else:
                discovered = results.get("atlas_discover_launch_options")
                assert discovered and discovered.get("success"), results
            print(f"PASSED: {plan} launches with authorized discovery")


asyncio.run(main())
PY

DEBUG_MODE=true "$PROJECT_ROOT/test/run_tests.sh" backend
echo "PASSED: launch discovery regression validation"
