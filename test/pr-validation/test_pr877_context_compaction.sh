#!/bin/bash
# Validation script for issue #877: context tracking and manual/automatic
# compaction for long conversations and agent runs.
#
# A real backend is started on a free port with a scripted OpenAI-compatible
# mock model (fixtures/pr877/mock_llm.py) whose configured context window is
# small, so a few turns reach the compaction threshold. The scenario checks,
# end to end over the WebSocket and the conversations REST API:
#   1. Automatic compaction before a turn writes a stored checkpoint, the next
#      request carries it instead of the early turns, and the full transcript
#      is kept.
#   2. Manual compaction (with a focus) adds no user turn; the per-conversation
#      toggle off disables automatic compaction.
#   3. An agent run compacts itself mid-run and recovers from a context-length
#      error, finishing without a new user message and without re-running a
#      tool call.
#   4. An unknown model alias reports an assumed context window.
# Nothing on the path is stubbed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr877"

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
  exec env DEBUG_MODE=true \
  FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true FEATURE_TOOLS_ENABLED=true \
  FEATURE_RAG_ENABLED=false FEATURE_WORKSPACES_ENABLED=false FEATURE_CONTEXT_COMPACTION_ENABLED=true \
  CONTEXT_AUTO_COMPACT_DEFAULT=true CONTEXT_COMPACTION_THRESHOLD_PERCENT=80 \
  USE_MOCK_S3=true LLM_MAX_RETRIES=0 \
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

print_header "Config exposes the compaction settings"
curl -sf -H "X-User-Email: owner@example.com" "http://127.0.0.1:$ATLAS_PORT/api/config/shell" \
  | python -c "import json,sys; c=json.load(sys.stdin)['context_compaction']; sys.exit(0 if c['enabled'] and c['auto_default'] and c['threshold_percent']==80 else 1)"
print_result $? "/api/config/shell carries context_compaction"

print_header "Context compaction, end to end"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -40 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

print_header "Compaction unit suites"
PYTEST_OUT="$(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q tests/test_context_budget.py tests/test_context_checkpoint_history.py tests/test_context_compaction.py tests/test_context_compaction_flow.py tests/test_websocket_context_compaction.py 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "compaction unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
