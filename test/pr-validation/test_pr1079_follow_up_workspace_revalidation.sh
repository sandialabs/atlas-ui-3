#!/bin/bash
# Test script for PR #1079: atlas_follow_up revalidates the sub-conversation's
# saved workspace before admitting the follow-up turn.
#
#   1. A follow-up succeeds while the saved workspace still exists and is
#      accessible to the caller.
#   2. After the workspace is deleted over the REST API, the follow-up is
#      refused with a model-readable refusal, and nothing is appended to the
#      child's transcript.
#   3. A workspace recreated under the SAME name (different id) does not
#      satisfy the follow-up: the saved workspace is resolved by id, never by
#      name.
#   4. A RENAMED workspace (same id) keeps working, and the handle carries the
#      current name.
#   5. Backend unit suite.
#
# A real backend is started on a free port with a scripted OpenAI-compatible
# mock model (fixtures/pr1079/mock_llm.py), a puppet that only decides which
# tool calls the parent makes; launch, follow-up, workspace storage and
# authorization are the real code paths.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1079"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }

free_port() { python - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

start_backend() {
    (
      cd "$PROJECT_ROOT/atlas" && \
      exec env \
      DEBUG_MODE=true ENVIRONMENT=development \
      FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true FEATURE_TOOLS_ENABLED=true \
      FEATURE_WORKSPACES_ENABLED=true FEATURE_ATLAS_LAUNCH_ENABLED=true \
      FEATURE_RAG_ENABLED=false \
      USE_MOCK_S3=true REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false FORCE_TOOL_APPROVAL_GLOBALLY=false \
      MAX_CONCURRENT_RUNS_PER_USER=50 ATLAS_LAUNCH_MAX_CHILDREN_PER_RUN=10 \
      CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" APP_LOG_DIR="$WORK/logs" \
      APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
      python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$2" 2>&1
    ) &
    BACKEND_PID=$!
    for _ in $(seq 1 60); do
        curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

stop_backend() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    BACKEND_PID=""
    for _ in $(seq 1 30); do
        curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 || return 0
        sleep 1
    done
    return 1
}

cd "$PROJECT_ROOT" || exit 1
source .venv/bin/activate

WORK="$(mktemp -d)"
export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
# The backend refuses to boot MCP token storage without an explicit encryption
# key; a per-run ephemeral one is fine because nothing is persisted across runs.
MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MCP_TOKEN_ENCRYPTION_KEY
trap 'kill $MOCK_PID $BACKEND_PID 2>/dev/null; rm -rf "$WORK"' EXIT

print_header "Starting scripted mock model on :$MOCK_LLM_PORT"
python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$WORK/config/"
# model_url gets no env substitution, so the port is written in here.
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null 2>&1 && break
    sleep 1
done
curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null 2>&1
print_result $? "scripted mock model is up"

print_header "Starting backend on :$ATLAS_PORT with FEATURE_ATLAS_LAUNCH_ENABLED=true"
start_backend true "$WORK/backend.log"
print_result $? "backend is up"

print_header "1-4. follow-up workspace re-validation, over the real WebSocket and REST API"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -60 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

stop_backend

print_header "5. Backend unit suite for the launch tools"
PYTEST_OUT="$(cd "$PROJECT_ROOT/atlas" && python -m pytest tests/test_atlas_launch_tool.py -q --tb=short 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "launch tool unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]