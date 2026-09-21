#!/bin/bash
# Test script for PR #957: reopening a conversation mid-stream shows the
# assistant reply from its beginning (issue #957, deferred from #956).
#
# Covers:
#   1. The run's replay buffer tracks the open segment while the answer
#      streams, and the in-flight record exposes it as streaming_text.
#   2. restore_conversation replays the segment as a run-tagged token_stream
#      frame (replay: true) -- to the run's own connection and to another
#      connection of the same user (the second-tab shape, which receives no
#      live stream after it).
#   3. Replay plus the run's live continuation reassembles the whole answer.
#   4. After the run ends there is nothing left to replay and the stored
#      record is served.
#   5. Backend unit suite.
#
# A real backend is started on a free port with a scripted OpenAI-compatible
# mock model (fixtures/pr957/mock_llm.py) that streams a labelled answer one
# word at a time, so the reopen lands genuinely mid-stream. Nothing on the
# path under test is stubbed.
#
# This driver runs on demand, not in a workflow: directly
# (`bash test/pr-validation/test_pr957_mid_stream_reopen.sh`) or through the
# PR-validation runner (`bash test/run_pr_validation.sh 962`), per
# test/pr-validation/README.md.
#
# The visual half of the same scenario lives in
# fixtures/pr957/ui_reopen.mjs: a Playwright driver (same mock model, same
# backend, real browser) that reopens the streaming conversation in a second
# tab and asserts the bubble starts at the answer's first word with the
# "answer in progress" marker and no live caret, then that the run-end
# reload settles the view to the complete stored transcript. It also
# captures the two screenshots committed beside it
# (reopen-mid-stream-marker.png, reopen-settled-transcript.png).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr957"

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

print_header "1-4. Mid-stream reopen, end to end over the real WebSocket and REST API"
SCENARIO_OUT="$(PYTHONPATH="$PROJECT_ROOT" python "$FIXTURES/scenario.py" 2>&1)"
SCENARIO_RC=$?
echo "$SCENARIO_OUT"
if [ $SCENARIO_RC -ne 0 ]; then
    echo "--- backend log tail ---"; tail -40 "$WORK/backend.log"
fi
print_result $SCENARIO_RC "end-to-end scenario"

print_header "5. Backend unit suite"
PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_RC=$?
echo "$PYTEST_OUT" | tail -3
print_result $PYTEST_RC "backend unit tests"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]