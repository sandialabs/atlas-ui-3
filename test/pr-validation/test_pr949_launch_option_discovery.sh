#!/bin/bash
# Test script for PR #949: ATLAS launch option discovery.
#
# `atlas_discover_launch_options` publishes the caller's authorized workspaces
# and models, and a successful discovery is a hard precondition for
# `atlas_launch`. Covers the PR's test plan end to end:
#
#   1. A saved workspace exists, and /api/config advertises the discovery tool
#      when FEATURE_ATLAS_LAUNCH_ENABLED is on.
#   2. atlas_launch with no prior discovery is refused, and the refusal tells
#      the model to run discovery first -- no sub-conversation is started.
#   3. Discovery followed by atlas_launch starts the sub-conversation under the
#      discovered workspace.
#   4. One step holding [launch, discovery] -- launch listed first -- still
#      discovers first and launches (the pre-pass in execute_multiple_tools).
#   5. Discovery reports the saved workspace and a bare model name, with no
#      provider, exactly as the tool description promises.
#   6. A launch naming an undiscovered workspace is refused.
#   7. A launch naming an undiscovered model is refused.
#   8. With FEATURE_ATLAS_LAUNCH_ENABLED off, neither tool is offered at all.
#   9. Backend unit suite.
#
# A real backend is started on a free port with a scripted OpenAI-compatible
# mock model (fixtures/pr949/mock_llm.py). The mock is a puppet only so the
# ORDER of the two tool calls can be driven deliberately -- the discovery, the
# authorization and the launch itself are the real code paths.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr949"

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
    # $1 = FEATURE_ATLAS_LAUNCH_ENABLED, $2 = log file
    # `exec` so $! is uvicorn itself; killing the subshell would leave the
    # server running and the next check would read a stale config.
    (
      cd "$PROJECT_ROOT/atlas" && \
      exec env \
      DEBUG_MODE=true \
      FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true FEATURE_TOOLS_ENABLED=true \
      FEATURE_WORKSPACES_ENABLED=true FEATURE_ATLAS_LAUNCH_ENABLED="$1" \
      FEATURE_RAG_ENABLED=false FEATURE_AGENT_PORTAL_ENABLED=false \
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
    # Do not proceed until the port is actually free, or the next start_backend
    # will "succeed" against the server we just asked to stop.
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
print_result $? "backend is up with launch enabled"

print_header "1-7. Launch option discovery, over the real WebSocket and REST API"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -60 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

print_header "8. With the feature flag off, neither launch tool is offered"
stop_backend
rm -f "$WORK/data/chat_history.db"
start_backend false "$WORK/backend-off.log"
print_result $? "backend is up with launch disabled"
OFF_TOOLS="$(curl -sf -H 'X-User-Email: owner@example.com' "http://127.0.0.1:$ATLAS_PORT/api/config" \
  | python -c 'import json,sys; b=json.load(sys.stdin); print(" ".join(n for s in (b.get("tools") or []) if isinstance(s,dict) and s.get("server")=="atlas" for n in (s.get("tools") or [])))')"
echo "atlas tools offered with launch disabled: ${OFF_TOOLS:-<none>}"
# The atlas server itself must still be there with its ungated tools -- an
# empty list would mean the query failed, not that the gate worked.
if [ -z "$OFF_TOOLS" ]; then
    echo "  the atlas server reported no tools at all; the /api/config query is wrong, not the gate"
    RC=1
else
    case "$OFF_TOOLS" in
        *launch*|*get_runs*|*result*) RC=1 ;;
        *) RC=0 ;;
    esac
fi
print_result $RC "neither atlas_launch nor the discovery tool is advertised when the flag is off"
stop_backend

print_header "9. Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
