#!/bin/bash
# Test script for PR #1230: reasoning round-trips on the assistant tool-call message.
#
# Reasoning models served through some providers reject the second LLM call of
# a tool-using turn when the assistant tool-call message lacks the reasoning
# the model produced with it ("The assistant turn at messages[2] is missing
# its reasoning ... Send it back in 'reasoning_content'"). This script runs a
# strict OpenAI-compatible mock of such a model (fixtures/pr1230/mock_llm.py)
# and drives real agent turns through LiteLLM's HTTP client against it:
#   1. atlas-chat --agent-mode (streaming path) completes a tool-using turn.
#   2. An agent turn with streaming off (non-streaming path) completes too.
#   3. The follow-up requests carried the reasoning back.
#   4. The reasoning text never reaches CLI output or the app logs.
#   5. The unit tests for the round trip pass.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1230"
REASONING_MARK="PR1230-HIDDEN-REASONING"

PASSED=0
FAILED=0
print_result() {
    if [ "$1" -eq 0 ]; then
        echo "PASSED: $2"; PASSED=$((PASSED + 1))
    else
        echo "FAILED: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=== $1 ==="; }

free_port() { python - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

WORK="$(mktemp -d)"
MOCK_PID=""
trap '[ -n "$MOCK_PID" ] && kill $MOCK_PID 2>/dev/null; rm -rf "$WORK"' EXIT

export MOCK_LLM_PORT="$(free_port)"
mkdir -p "$WORK/config" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$WORK/config/"
echo '{}' > "$WORK/config/rag-sources.json"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null 2>&1 && break
    sleep 0.5
done
curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null 2>&1
print_result $? "strict reasoning mock model is up"

export APP_CONFIG_DIR="$WORK/config" APP_LOG_DIR="$WORK/logs" LLM_MAX_RETRIES=0 \
    FEATURE_TOOLS_ENABLED=true FEATURE_RAG_ENABLED=false USE_MOCK_S3=true \
    CAPABILITY_TOKEN_SECRET="${CAPABILITY_TOKEN_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')}"

print_header "Check 1: agent-mode tool turn through atlas-chat (streaming path)"
CLI_OUT="$(python -m atlas.atlas_chat_cli "write notes" --model strict-reasoner \
    --agent-mode --tools atlas_canvas --config-dir "$WORK/config" 2>&1)"
CLI_RC=$?
echo "$CLI_OUT" | tail -5
[ $CLI_RC -eq 0 ] && echo "$CLI_OUT" | grep -q "FINAL-ANSWER-OK"
print_result $? "atlas-chat agent turn reaches the final answer"

print_header "Check 2: agent tool turn with streaming off (non-streaming path)"
NS_OUT="$(python "$FIXTURES/non_streaming_turn.py" 2>&1)"
NS_RC=$?
echo "$NS_OUT" | tail -3
print_result $NS_RC "non-streaming agent turn reaches the final answer"

print_header "Check 3: follow-up requests carried the reasoning back"
LOG_JSON="$(curl -sf "http://127.0.0.1:$MOCK_LLM_PORT/test/log")"
python - "$LOG_JSON" <<'PY'
import json, sys
log = json.loads(sys.argv[1])
follow_ups = [e for e in log if e["assistant_tool_calls"]]
modes = {e["stream"] for e in follow_ups}
rejected = [e for e in log if e.get("rejected")]
print(f"requests={len(log)} follow_ups={len(follow_ups)} stream_modes={sorted(modes)} rejected={len(rejected)}")
ok = follow_ups and not rejected and modes == {True, False} and all(
    c["reasoning_ok"] for e in follow_ups for c in e["assistant_tool_calls"])
sys.exit(0 if ok else 1)
PY
print_result $? "streaming and non-streaming follow-ups both returned reasoning_content"

print_header "Check 4: reasoning stays out of output and logs"
LEAK=0
echo "$CLI_OUT$NS_OUT" | grep -q "$REASONING_MARK" && LEAK=1
grep -rq "$REASONING_MARK" "$WORK/logs" 2>/dev/null && LEAK=1
print_result $LEAK "reasoning text absent from CLI output and app logs"

print_header "Check 5: unit tests"
python -m pytest atlas/tests/test_reasoning_round_trip.py -q -p no:cacheprovider > "$WORK/pytest.log" 2>&1
PT_RC=$?
tail -1 "$WORK/pytest.log"
print_result $PT_RC "test_reasoning_round_trip.py"

echo ""
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
