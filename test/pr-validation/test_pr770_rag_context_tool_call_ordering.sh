#!/bin/bash
# Test script for PR #770: keep the RAG context message out of tool-call blocks
#
# Starts a stub provider that enforces the OpenAI/Azure rule "an assistant
# message with tool_calls must be followed by tool messages responding to each
# tool_call_id", the mock ATLAS RAG API, and an MCP server with one tool. Then
# it drives a real RAG + tools chat turn over the WebSocket -- the combination
# that reaches a continuation round, where the context message used to be
# inserted between the assistant tool_calls message and its tool reply.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr770"

BACKEND_PORT=8125
export STRICT_ORDER_LLM_PORT=8126
export ATLAS_RAG_MOCK_PORT=8127

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

STUB_PID=""
RAG_PID=""
BACKEND_PID=""

cleanup() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$RAG_PID" ] && kill "$RAG_PID" 2>/dev/null
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup EXIT

cd "$PROJECT_ROOT"
source .venv/bin/activate

# load_dotenv() does not override the real environment, so these win over .env.
export APP_CONFIG_DIR="$FIXTURES/config"
export MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-pr770-validation-key-at-least-32-chars-long}"
export FEATURE_TOOLS_ENABLED=true
export FEATURE_RAG_ENABLED=true
export USE_MOCK_S3=true
# litellm's aiohttp transport for OpenAI-compatible endpoints imports orjson,
# which is not a declared dependency. Use the httpx transport so the script does
# not depend on whether the local environment happens to have orjson.
export DISABLE_AIOHTTP_TRANSPORT=true

# --- Start the stub provider ---
python "$FIXTURES/strict_order_llm_stub.py" &
STUB_PID=$!

for _ in $(seq 1 20); do
    curl -s -o /dev/null "http://127.0.0.1:$STRICT_ORDER_LLM_PORT/_requests" && break
    sleep 0.5
done

# The stub must reject a split tool-call block and accept a contiguous one --
# this is the provider behavior the whole test depends on.
SPLIT_BODY='{"model":"stub","messages":[
  {"role":"user","content":"hi"},
  {"role":"assistant","content":"","tool_calls":[{"id":"call_x","type":"function","function":{"name":"t","arguments":"{}"}}]},
  {"role":"system","content":"Retrieved context"},
  {"role":"tool","content":"result","tool_call_id":"call_x"}]}'
STUB_SPLIT=$(curl -s -o /tmp/pr770_split.json -w "%{http_code}" -X POST \
    "http://127.0.0.1:$STRICT_ORDER_LLM_PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' -d "$SPLIT_BODY")
[ "$STUB_SPLIT" = "400" ] && grep -q "call_x" /tmp/pr770_split.json
print_result $? "Stub provider rejects a split tool-call block naming the id (got $STUB_SPLIT)"

CONTIGUOUS_BODY='{"model":"stub","messages":[
  {"role":"system","content":"Retrieved context"},
  {"role":"user","content":"hi"},
  {"role":"assistant","content":"","tool_calls":[{"id":"call_x","type":"function","function":{"name":"t","arguments":"{}"}}]},
  {"role":"tool","content":"result","tool_call_id":"call_x"}]}'
STUB_OK=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$STRICT_ORDER_LLM_PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' -d "$CONTIGUOUS_BODY")
[ "$STUB_OK" = "200" ]
print_result $? "Stub provider accepts a contiguous tool-call block (got $STUB_OK)"

# --- Start the mock RAG API ---
python "$PROJECT_ROOT/mocks/atlas-rag-api-mock/main.py" > /tmp/pr770_rag.log 2>&1 &
RAG_PID=$!

RAG_UP=1
for _ in $(seq 1 30); do
    if curl -s -f -H "Authorization: Bearer test-atlas-rag-token" \
        "http://127.0.0.1:$ATLAS_RAG_MOCK_PORT/api/v1/discover/datasources?as_user=test@test.com" \
        > /dev/null 2>&1; then
        RAG_UP=0
        break
    fi
    sleep 1
done
print_result $RAG_UP "Mock RAG API started on port $ATLAS_RAG_MOCK_PORT"

# --- Start the backend against the fixture config ---
cd "$PROJECT_ROOT/atlas"
"$PROJECT_ROOT/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
    > /tmp/pr770_backend.log 2>&1 &
BACKEND_PID=$!
cd "$PROJECT_ROOT"

BACKEND_UP=1
for _ in $(seq 1 60); do
    if curl -s -f "http://127.0.0.1:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
        BACKEND_UP=0
        break
    fi
    sleep 1
done
print_result $BACKEND_UP "Backend started on port $BACKEND_PORT"

if [ $BACKEND_UP -ne 0 ]; then
    echo "Backend did not start. Last log lines:"
    tail -20 /tmp/pr770_backend.log
    echo ""
    echo "Passed: $PASSED | Failed: $FAILED"
    exit 1
fi

curl -s "http://127.0.0.1:$BACKEND_PORT/api/config" | grep -q "orderdocs"
print_result $? "Discovery advertises the orderdocs tool server"

curl -s "http://127.0.0.1:$BACKEND_PORT/api/config" | grep -q "atlas_rag:technical-docs"
print_result $? "Discovery advertises the mock RAG data source"

# --- The actual behaviour under test ---
python "$FIXTURES/ws_check.py" "$BACKEND_PORT" "$STRICT_ORDER_LLM_PORT"
print_result $? "RAG+tools continuation round keeps the assistant/tool block contiguous"

cleanup
BACKEND_PID=""
RAG_PID=""
STUB_PID=""

# Final: run backend unit tests against the project's own config, not the
# stub-only fixture config this script has been pointing the backend at.
unset APP_CONFIG_DIR
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
