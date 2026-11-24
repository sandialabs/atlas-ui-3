#!/bin/bash
# PR #1202: startup must not wait forever on a stdio MCP server that never
# completes the MCP handshake. Starts the real backend with three stdio
# servers: the bundled calculator, one whose script is missing (the hardened
# image case) and one that never answers. The backend has to become healthy
# and report the calculator tools; before the fix it stayed in
# "Waiting for application startup" indefinitely.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

WORK="$(mktemp -d)"
FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr1202"
BACKEND_PID=""
cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        # A backend stuck in startup does not exit on SIGTERM; escalate.
        kill "$BACKEND_PID" 2>/dev/null || true
        for _ in $(seq 1 10); do
            kill -0 "$BACKEND_PID" 2>/dev/null || break
            sleep 1
        done
        pkill -KILL -P "$BACKEND_PID" 2>/dev/null || true
        kill -KILL "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

PORT="$(python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
export PORT
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
find "$PROJECT_ROOT/atlas/config" -maxdepth 1 -type f \
    \( -name '*.json' -o -name '*.yml' -o -name '*.md' \) -exec cp '{}' "$WORK/config/" \;
cat > "$WORK/config/mcp.json" <<JSON
{
  "calculator": {
    "command": ["python", "mcp/calculator/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "compliance_level": "Public"
  },
  "missing_script": {
    "command": ["python", "mcp/not_shipped_in_this_image/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "compliance_level": "Public"
  },
  "silent": {
    "command": ["python", "$FIXTURES/silent_stdio_server.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "compliance_level": "Public"
  }
}
JSON

export APP_CONFIG_DIR="$WORK/config"
export APP_LOG_DIR="$WORK/logs"
export RUNTIME_LOG_DIR="$WORK/logs"
export RUNTIME_FEEDBACK_DIR="$WORK/data/feedback"
export CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db"
export AGENT_PORTAL_DB_URL="duckdb:///$WORK/data/agent_portal.db"
export DEBUG_MODE=true
export USE_MOCK_S3=true
export FEATURE_TOOLS_ENABLED=true
export FEATURE_RAG_ENABLED=false
export FEATURE_RAG_MCP_ENABLED=false
export ATLAS_HOST=127.0.0.1
export MCP_DISCOVERY_TIMEOUT=3
export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAPABILITY_TOKEN_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"

(cd "$PROJECT_ROOT/atlas" && exec python main.py) > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!

started="$(date +%s)"
healthy=false
for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$PORT/api/heartbeat" > /dev/null; then
        healthy=true
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done
elapsed=$(( $(date +%s) - started ))
if [ "$healthy" != true ]; then
    tail -n 60 "$WORK/backend.log"
    echo "FAILED: backend not healthy after ${elapsed}s with a stdio MCP server that never connects"
    exit 1
fi
echo "PASSED: backend healthy after ${elapsed}s despite a silent and a missing stdio MCP server"

python - <<'PY'
import json
import os
import urllib.request

request = urllib.request.Request(
    f"http://127.0.0.1:{os.environ['PORT']}/api/config",
    headers={"X-User-Email": "test@test.com"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    config = json.load(response)
servers = {entry["server"]: entry for entry in config["tools"]}
assert servers["calculator"]["tools"], "calculator tools must be discovered"
for name in ("silent", "missing_script"):
    assert not servers.get(name, {}).get("tools"), f"{name} must expose no tools"
print("PASSED: calculator tools discovered; the two broken servers expose none")
PY

if ! grep -q "Timeout connecting to MCP server" "$WORK"/logs/*.jsonl "$WORK/backend.log" 2>/dev/null; then
    echo "FAILED: no connect-timeout failure was logged for the silent server"
    exit 1
fi
echo "PASSED: the silent server was reported as a connect timeout"

PYTHONPATH="$PROJECT_ROOT" python -m pytest "$PROJECT_ROOT/atlas/tests/test_mcp_connect_timeout.py" \
    "$PROJECT_ROOT/atlas/tests/test_mcp_delegated_discovery_logging.py" -q
echo "ALL CHECKS PASSED"
