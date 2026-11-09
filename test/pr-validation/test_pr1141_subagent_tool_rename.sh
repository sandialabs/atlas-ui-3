#!/bin/bash
# Test script for PR #1141: rename atlas_launch to atlas_launch_subagent
#
# Test plan:
# - Unit: the new names are exposed, every deprecated name still resolves,
#   executes, is gated and satisfies a hook matcher (backend + frontend).
# - End to end, over the real /api/config, workspaces API and WebSocket with a
#   scripted model: the subagent tools work under the new names, under the
#   deprecated aliases, and when a new selection meets an old call; only the
#   new names are advertised.
# - Multi-replica harness, because the change touches
#   atlas/application/chat/runs/ (launcher.py, follow_up.py). Skip it with
#   ATLAS_PR1141_SKIP_MULTIREPLICA=1 when no container runtime is available.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr1141"
WORK="$(mktemp -d)"
BACKEND_PID=""
MOCK_PID=""
cleanup() {
    # Only the two processes this script started: other Atlas backends on the
    # machine are not ours to stop.
    for pid in "$BACKEND_PID" "$MOCK_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() { echo "FAILED: $*"; exit 1; }

free_port() {
    python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}
wait_for_health() {
    for _ in $(seq 1 90); do
        curl -sf "$1" > /dev/null && return 0
        sleep 1
    done
    tail -40 "$WORK"/*.log
    return 1
}

echo "== backend unit tests"
out=$(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_subagent_tool_names.py tests/test_atlas_consolidated_tools.py \
    tests/test_atlas_launch_tool.py tests/test_tool_details_in_config.py 2>&1) \
    || { echo "$out" | tail -30; fail "backend tests"; }
echo "$out" | tail -1

echo "== frontend unit tests"
(cd frontend && npx vitest run src/test/subagent-tool-names.test.jsx \
    src/test/atlas-tools-consolidation.test.js src/test/tools-panel.test.jsx) || fail "vitest"

echo "== end to end: new names and deprecated aliases over the real WebSocket"
export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$WORK/config/"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"
python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_health "http://127.0.0.1:$MOCK_LLM_PORT/health" || fail "mock LLM did not start"

# Started by hand rather than through agent_start.sh, which stops every Atlas
# backend on the machine before starting its own.
(
    cd atlas
    exec env \
        PYTHONPATH="$PROJECT_ROOT" \
        PORT="$ATLAS_PORT" \
        DEBUG_MODE=true \
        FEATURE_CHAT_HISTORY_ENABLED=true \
        FEATURE_AGENT_MODE_AVAILABLE=true \
        FEATURE_TOOLS_ENABLED=true \
        FEATURE_WORKSPACES_ENABLED=true \
        FEATURE_ATLAS_LAUNCH_ENABLED=true \
        FEATURE_RAG_ENABLED=false \
        USE_MOCK_S3=true \
        REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false \
        FORCE_TOOL_APPROVAL_GLOBALLY=false \
        MAX_CONCURRENT_RUNS_PER_USER=50 \
        SESSION_REPOSITORY_TYPE=memory \
        REDIS_URL= \
        CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" \
        APP_LOG_DIR="$WORK/logs" \
        APP_CONFIG_DIR="$WORK/config" \
        MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
        python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" --no-use-colors
) > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
wait_for_health "http://127.0.0.1:$ATLAS_PORT/api/health" || fail "backend did not start"

python "$FIXTURES/scenario.py" || { tail -60 "$WORK/backend.log"; fail "end-to-end scenario"; }

deprecated=$(grep -h -c "Deprecated tool name" "$WORK"/logs/* "$WORK/backend.log" 2>/dev/null \
    | paste -sd+ | bc)
[ "${deprecated:-0}" -gt 0 ] || fail "calls under a deprecated name were not logged"
echo "PASSED: calls under a deprecated name are logged ($deprecated log lines)"

if [ "${ATLAS_PR1141_SKIP_MULTIREPLICA:-0}" = "1" ]; then
    echo "== multi-replica harness SKIPPED (ATLAS_PR1141_SKIP_MULTIREPLICA=1)"
else
    echo "== multi-replica harness (two real replicas on shared Redis and PostgreSQL)"
    bash test/multireplica_tests.sh || fail "multi-replica harness"
fi

echo "PASSED: subagent tools work under their new names and their deprecated aliases"
