#!/bin/bash
# Test script for PR #981: a joined conversation refreshes its tool activity
# while the run is still in progress (issue #980).
#
# A run's frames stay bound to the socket that started it, so a conversation
# opened from another tab only held the snapshot the run had when the view
# was opened; tool rows the run produced afterwards stayed invisible until the
# run ended. The client now polls the run's live record and appends what has
# appeared. This script exercises the backend half of that contract end to
# end against a real backend:
#   1. A tracked agent run (two atlas_sleep calls) pauses on its first tool
#      approval, so it is genuinely in flight.
#   2. `GET /api/conversations/{id}` serves the run's live session as
#      `in_flight: true`, with no tool row yet.
#   3. Answering the first approval executes the tool and pauses on the
#      second; a mid-run poll now sees the tool row that landed, and it
#      carries the tool_call_id the client matches on.
#   4. Answering the second approval completes the run; the store record is
#      then served and holds the tool rows the poll saw.
#
# A real backend is started on a free port with a scripted OpenAI-compatible
# mock model (fixtures/pr981/mock_llm.py) that returns atlas_sleep tool calls,
# so the turn is admitted as a tracked run and pauses on approval exactly as
# it does in the app. Nothing on the path under test is stubbed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr981"

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

cd "$PROJECT_ROOT" || exit 1
source .venv/bin/activate

WORK="$(mktemp -d)"
export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
trap 'kill $MOCK_PID $BACKEND_PID 2>/dev/null; rm -rf "$WORK"' EXIT

print_header "Starting scripted mock model on :$MOCK_LLM_PORT and backend on :$ATLAS_PORT"
python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$WORK/config/"
# model_url gets no env substitution, so the port is written in here.
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

(
  cd "$PROJECT_ROOT/atlas" && \
  DEBUG_MODE=true \
  FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true FEATURE_TOOLS_ENABLED=true \
  FEATURE_RAG_ENABLED=false FEATURE_WORKSPACES_ENABLED=false FEATURE_AGENT_PORTAL_ENABLED=false \
  USE_MOCK_S3=true REQUIRE_TOOL_APPROVAL_BY_DEFAULT=true FORCE_TOOL_APPROVAL_GLOBALLY=false \
  CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" APP_LOG_DIR="$WORK/logs" \
  APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
  python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$WORK/backend.log" 2>&1
) &
BACKEND_PID=$!

for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 && break
    sleep 1
done
curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1
print_result $? "backend is up"
curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null 2>&1
print_result $? "scripted mock model is up"

print_header "Mid-run live tool visibility contract, end to end"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -40 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

print_header "Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
