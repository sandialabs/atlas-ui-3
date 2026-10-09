#!/bin/bash
# Test script for PR #1043: enforce saved conversation classification
# boundaries on restore and resume (issue #1042).
#
# Covers the PR's test plan end to end against a real backend:
#   1. A conversation saved under CUI records CUI (server-managed).
#   2. From a new connection under UUR it is refused and the model is never
#      called; client-supplied classification fields are ignored.
#   3. restore_conversation and the REST fetch under UUR are refused (409,
#      no content); under CUI both work and the history reaches the model.
#   4. Switching level mid-conversation is refused, even without the id.
#   5. A new UUR conversation works and is recorded as UUR.
#   6. Refusals are audited in the backend log.
#   7. The legacy stamp script records a level only on unrecorded rows.
#   8. Backend unit suite.
#
# The scripted mock model logs every call, so "refused" is checked as "the
# model received nothing". All prompts are synthetic placeholders.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1043"

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
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$FIXTURES/compliance-levels.json" "$WORK/config/"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

(
  cd "$PROJECT_ROOT/atlas" && \
  DEBUG_MODE=true FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_COMPLIANCE_LEVELS_ENABLED=true \
  FEATURE_COMPLIANCE_LEVEL_REQUIRED=false FEATURE_OIDC_AUTH_ENABLED=false FEATURE_PROXY_SECRET_ENABLED=false \
  FEATURE_TOOLS_ENABLED=false FEATURE_RAG_ENABLED=false FEATURE_WORKSPACES_ENABLED=false FEATURE_AGENT_PORTAL_ENABLED=false \
  USE_MOCK_S3=true CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" APP_LOG_DIR="$WORK/logs" \
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

print_header "1-5. Classification boundaries over the real WebSocket and REST API"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -40 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

print_header "6. Refusals are audited without content"
AUDIT="$(cat "$WORK/backend.log" "$WORK"/logs/* 2>/dev/null | grep 'Refused .* of conversation')"
[ -n "$AUDIT" ]
print_result $? "refused attempts are logged"
echo "$AUDIT" | grep -q "placeholder-"
[ $? -ne 0 ]
print_result $? "audit lines carry no conversation content"

kill $BACKEND_PID 2>/dev/null; wait $BACKEND_PID 2>/dev/null

print_header "7. Legacy stamp script"
STAMP_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/stamp_check.py" "$WORK/stamp.db" 2>&1)"
STAMP_RC=$?
echo "$STAMP_OUT"
print_result $STAMP_RC "stamp script records a level only on legacy rows"

print_header "8. Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
