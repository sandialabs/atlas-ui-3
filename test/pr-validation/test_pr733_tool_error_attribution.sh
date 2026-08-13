#!/bin/bash
# Test script for PR #733: Name the rejected tool when a provider rejects the request
# Covers test plan items from the PR description.
#
# Starts a stub provider that applies the documented tool-schema validation and an
# MCP server that advertises one invalid tool schema, then drives real chat turns
# over the WebSocket to check what the user is actually told.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr733"

BACKEND_PORT=8123
export STRICT_LLM_PORT=8124

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
BACKEND_PID=""

cleanup() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup EXIT

cd "$PROJECT_ROOT"
source .venv/bin/activate

# load_dotenv() does not override the real environment, so these win over .env.
export APP_CONFIG_DIR="$FIXTURES/config"
export MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-pr733-validation-key-at-least-32-chars-long}"
export FEATURE_TOOLS_ENABLED=true
export USE_MOCK_S3=true

# --- Start the stub provider ---
python "$FIXTURES/strict_llm_stub.py" &
STUB_PID=$!

for _ in $(seq 1 20); do
    curl -s -o /dev/null "http://127.0.0.1:$STRICT_LLM_PORT/v1/chat/completions" && break
    sleep 0.5
done

# The stub must reject an invalid tool schema and accept a valid one.
STUB_INVALID=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$STRICT_LLM_PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"stub","messages":[],"tools":[{"type":"function","function":{"name":"t","parameters":{"type":"string"}}}]}')
[ "$STUB_INVALID" = "400" ]
print_result $? "Stub provider rejects a non-object tool schema (got $STUB_INVALID)"

STUB_VALID=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$STRICT_LLM_PORT/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"stub","messages":[],"tools":[{"type":"function","function":{"name":"t","parameters":{"type":"object"}}}]}')
[ "$STUB_VALID" = "200" ]
print_result $? "Stub provider accepts a valid tool schema (got $STUB_VALID)"

# --- Start the backend against the fixture config ---
cd "$PROJECT_ROOT/atlas"
"$PROJECT_ROOT/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
    > /tmp/pr733_backend.log 2>&1 &
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
    tail -20 /tmp/pr733_backend.log
    echo ""
    echo "Passed: $PASSED | Failed: $FAILED"
    exit 1
fi

# The malformed tool is admitted by discovery today (see #727), which is the
# precondition this PR's error reporting has to cope with.
curl -s "http://127.0.0.1:$BACKEND_PORT/api/config" | grep -q "badschema_lookup\|lookup"
print_result $? "Discovery admits the tool with the invalid schema"

# --- The actual behaviour under test ---
python "$FIXTURES/ws_check.py" "$BACKEND_PORT"
print_result $? "Error frame names the rejected tool and carries error_type=bad_request"

cleanup
BACKEND_PID=""
STUB_PID=""

# Final: run backend unit tests
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
