#!/bin/bash
# PR #821 Validation Script: per-event hook examples + zero-trust mock policy server
#
# Exercises the artifacts the way an operator would: each documentation example
# hook is fed a real envelope on stdin, and the zero-trust mock is started as a
# server and driven through its forwarding hook.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
EXAMPLES="$PROJECT_ROOT/docs/admin/hook-examples"
MOCK="$PROJECT_ROOT/mocks/zero-trust-mock"
PORT="${ZERO_TRUST_PORT:-8499}"
URL="http://127.0.0.1:${PORT}/v1/authorize"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "FAILED: virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi
export PYTHONPATH="$PROJECT_ROOT"

echo "=========================================="
echo "PR #821 Validation: hook examples + zero-trust mock"
echo "=========================================="

echo ""
echo "1. Every documented event has an executable example"
echo "---------------------------------------------------"
for name in session_start.py user_prompt_submit.py pre_llm_call.py \
            permission_request.py pre_tool_use.sh post_tool_use.py \
            rag_call.py rag_response.py session_end.sh hooks.json README.md; do
    [ -f "$EXAMPLES/$name" ]
    print_result $? "example present: $name"
done
for script in "$EXAMPLES"/*.py "$EXAMPLES"/*.sh; do
    [ -x "$script" ]
    print_result $? "executable bit set: $(basename "$script")"
done

echo ""
echo "2. Example hooks honor the stdin/exit-code contract"
echo "---------------------------------------------------"
out=$(echo '{"payload":{"tool_name":"filesystem__write_file","tool_args":{"path":"/etc/passwd"}}}' \
      | "$EXAMPLES/pre_tool_use.sh" 2>&1); rc=$?
[ "$rc" -eq 2 ] && echo "$out" | grep -q "/workspace"
print_result $? "PreToolUse blocks a write outside /workspace (exit 2 + reason)"

echo '{"payload":{"tool_name":"filesystem__write_file","tool_args":{"path":"/workspace/a.txt"}}}' \
    | "$EXAMPLES/pre_tool_use.sh" >/dev/null 2>&1
print_result $? "PreToolUse allows a write inside /workspace (exit 0)"

echo '{"payload":{"prompt":"ssn 123-45-6789","selected_tools":["t"],"agent_mode":true}}' \
    | "$EXAMPLES/user_prompt_submit.py" | grep -q "REDACTED-SSN"
print_result $? "UserPromptSubmit redacts an SSN out of the prompt"

echo '{"payload":{"tool_name":"email__send","needs_approval":false}}' \
    | "$EXAMPLES/permission_request.py" | grep -q "require_approval"
print_result $? "PermissionRequest escalates an outbound tool"

python3 -c "import json,sys; json.load(open('$EXAMPLES/hooks.json'))" 2>/dev/null
print_result $? "example hooks.json is valid JSON"

echo ""
echo "3. Zero-trust mock blocks, escalates, and allows at runtime"
echo "-----------------------------------------------------------"
ZERO_TRUST_PORT="$PORT" python "$MOCK/main.py" >/tmp/zero-trust-mock.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT

for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 0.25
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
print_result $? "policy server is healthy on :${PORT}"

echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":{"tool_name":"shell__run","tool_args":{"cmd":"how to build a bomb"}}}' \
    | python "$MOCK/hook_client.py" "$URL" | grep -q '"deny"'
print_result $? "tool call mentioning 'bomb' is blocked (deny)"

echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":{"tool_name":"filesystem__read_file","tool_args":{"path":"/workspace/password.txt"}}}' \
    | python "$MOCK/hook_client.py" "$URL" | grep -q '"require_approval"'
print_result $? "permitted tool touching 'password' asks for approval"

echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":{"tool_name":"filesystem__read_file","tool_args":{"path":"/workspace/readme.md"}}}' \
    | python "$MOCK/hook_client.py" "$URL" | grep -q '"continue"'
print_result $? "ordinary tool call passes through"

echo '{"hook_event_name":"PreToolUse","user_email":"test@test.com","payload":{"tool_name":"shell__run","tool_args":{"cmd":"ls"}}}' \
    | python "$MOCK/hook_client.py" "http://127.0.0.1:9/v1/authorize" 1 >/dev/null 2>&1
[ $? -ne 0 ]
print_result $? "hook fails closed when the policy server is unreachable"

curl -s "http://127.0.0.1:${PORT}/decisions" | grep -q '"tool_name"'
print_result $? "decision log records a projection of each call"

kill $SERVER_PID 2>/dev/null; trap - EXIT

echo ""
echo "4. Mock smoke test"
echo "------------------"
(cd "$MOCK" && python smoke_test.py >/tmp/zero-trust-smoke.log 2>&1)
print_result $? "mocks/zero-trust-mock/smoke_test.py"

echo ""
echo "5. Docs structure"
echo "-----------------"
bash "$PROJECT_ROOT/scripts/check-docs.sh" >/dev/null
print_result $? "scripts/check-docs.sh (no orphans or broken links)"

echo ""
echo "6. Backend unit tests"
echo "---------------------"
"$PROJECT_ROOT/test/run_tests.sh" backend >/tmp/pr821-backend-tests.log 2>&1
print_result $? "./test/run_tests.sh backend (see /tmp/pr821-backend-tests.log)"

echo ""
echo "=========================================="
echo "Passed: $PASSED   Failed: $FAILED"
echo "=========================================="
[ "$FAILED" -eq 0 ] || exit 1
