#!/bin/bash
# Test script for PR #1033: explicit allowed data classifications (issue #1032).
#
# Starts a real backend on a free port with three classifications
# (UUR, ITAR, ECI), models and MCP servers that declare
# allowed_data_classifications (plus a legacy compliance_level and an
# undeclared component), and the scripted mock model from pr958. Then:
#   1. /api/config exposes each component's effective classifications.
#   2. The issue's matrix over the real WebSocket: a UUR-only model is refused
#      in ITAR/ECI conversations, a multi-classification model runs in each.
#   3. A legacy compliance_level acts as a one-element list.
#   4. Undeclared models and MCP servers fail closed in a classified session.
#   5. A UUR-only MCP server is refused in ITAR; a multi-classification one
#      is allowed.
#   6. A misspelt level is refused; with no level selected nothing is refused.
#   7. Backend unit suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1032"

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
python "$SCRIPT_DIR/fixtures/pr958/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/compliance-levels.json" "$WORK/config/"
echo '{}' > "$WORK/config/rag-sources.json"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

(
  cd "$PROJECT_ROOT/atlas" && \
  DEBUG_MODE=true \
  FEATURE_COMPLIANCE_LEVELS_ENABLED=true FEATURE_COMPLIANCE_LEVEL_REQUIRED=false \
  FEATURE_TOOLS_ENABLED=true FEATURE_RAG_ENABLED=false FEATURE_CHAT_HISTORY_ENABLED=false \
  FEATURE_WORKSPACES_ENABLED=false FEATURE_AGENT_PORTAL_ENABLED=false \
  USE_MOCK_S3=true APP_LOG_DIR="$WORK/logs" \
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

print_header "1-6. Classification enforcement over the real REST API and WebSocket"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -40 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

if [ -z "$SKIP_UNIT_TESTS" ]; then
    print_header "7. Backend unit suite"
    PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
    PYTEST_RC=$?
    echo "$PYTEST_OUT" | tail -3
    print_result $PYTEST_RC "backend unit tests"
fi

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
