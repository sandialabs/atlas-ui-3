#!/bin/bash
# PR #1204 (issue #1203): a backend test blocked on a WebSocket frame that
# never comes must fail by name, in bounded time, instead of stalling the run.
#
# Checks:
#   1. pytest-timeout is installed with the dev extra.
#   2. The original pattern (TestClient.receive_json() against a handler that
#      answers and keeps listening) really does block, and the timeout turns
#      it into one failed test while the next test still runs.
#   3. The bounded client and the admission-guard tests that use it pass,
#      repeatedly, under pytest-xdist.
#   4. A turn that never reaches the injected failure is reported as a
#      deadline failure naming the frames received, not as a pass.
#   5. The backend suite passes under pytest-xdist, as CI runs it.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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

cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/scripts"
export MCP_CONFIG_FILE=mcp-test.json

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. The plugin ships with the dev extra.
python -c "import pytest_timeout" 2>/dev/null
print_result $? "pytest-timeout is installed"
grep -q '"pytest-timeout' pyproject.toml && grep -q 'name = "pytest-timeout"' uv.lock
print_result $? "pytest-timeout is declared in pyproject.toml and uv.lock"

# 2. The original blocking pattern, outside the suite so nothing is written
#    into the checkout. Without the timeout this file never finishes.
cat > "$WORK/test_blocking_receive.py" <<'PY'
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient


async def answers_and_keeps_listening(websocket):
    await websocket.accept()
    while True:
        await websocket.receive_json()
        await websocket.send_json({"type": "error"})


app = Starlette(routes=[WebSocketRoute("/ws", answers_and_keeps_listening)])


def test_blocked_on_a_frame_that_never_arrives():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "chat"})
        for _ in range(20):
            ws.receive_json()


def test_the_run_carries_on():
    pass
PY
START=$(date +%s)
(cd "$WORK" && timeout 120 python -m pytest test_blocking_receive.py -q \
    --rootdir "$WORK" -c /dev/null -p no:cacheprovider --timeout=5 > "$WORK/blocking.log" 2>&1)
RC=$?
ELAPSED=$(( $(date +%s) - START ))
[ "$RC" -eq 1 ] && grep -q "1 failed, 1 passed" "$WORK/blocking.log" \
    && grep -q "Failed: Timeout" "$WORK/blocking.log" && [ "$ELAPSED" -lt 60 ]
RESULT=$?
[ "$RESULT" -ne 0 ] && tail -20 "$WORK/blocking.log"
print_result $RESULT "a blocked receive_json() is one named failure in ${ELAPSED}s and the run carries on"
grep -q "testclient.py.*in receive" "$WORK/blocking.log"
print_result $? "the timeout report shows the test blocked in TestClient.receive"

# 3. The bounded client and its users, repeatedly, in parallel.
STABLE=0
for _ in 1 2 3 4 5; do
    (cd atlas && timeout 300 python -m pytest tests/test_asgi_websocket_session.py \
        tests/test_websocket_admission_guard.py -q -n 2 -p no:cacheprovider > "$WORK/bounded.log" 2>&1)
    if [ $? -ne 0 ] || ! grep -q "16 passed" "$WORK/bounded.log"; then
        STABLE=1
        tail -20 "$WORK/bounded.log"
        break
    fi
done
print_result $STABLE "bounded WebSocket tests pass 5 times under pytest-xdist"

# 4. A refused turn is a deadline failure, not the injected failure.
(cd atlas && timeout 120 python -m pytest tests/test_websocket_admission_guard.py -q \
    -p no:cacheprovider -k "never_reaches_the_window or run_started_send_failure" > "$WORK/window.log" 2>&1)
RC=$?
[ "$RC" -eq 0 ] && grep -q "2 passed" "$WORK/window.log"
RESULT=$?
[ "$RESULT" -ne 0 ] && tail -20 "$WORK/window.log"
print_result $RESULT "the injected failure is verified and a stalled turn is reported as a deadline failure"

# 5. The suite, the way CI's parallel pass runs it.
PYTEST_ARGS="-n 2 -p no:cacheprovider" ./test/run_tests.sh backend > "$WORK/suite.log" 2>&1
RESULT=$?
[ "$RESULT" -ne 0 ] && tail -30 "$WORK/suite.log"
print_result $RESULT "backend suite under pytest-xdist ($(grep -Eo '[0-9]+ passed, [0-9]+ skipped' "$WORK/suite.log" | tail -1))"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
