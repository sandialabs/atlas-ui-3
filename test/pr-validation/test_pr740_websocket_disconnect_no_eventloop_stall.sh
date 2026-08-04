#!/bin/bash
# Test script for PR #740: Stop a blocking tokenizer download from stalling the
# event loop, and handle a mid-turn websocket disconnect gracefully.
#
# Test plan:
# - LiteLLM must not attempt the huggingface.co tokenizer download for a
#   llama-family model name.  Exercised against the real litellm tokenizer
#   selection path (no mocks), asserting both the resulting tokenizer type and
#   that the call returns too fast to have attempted a network round trip.
# - A real uvicorn-hosted Atlas server, a real websocket client, and a real
#   streaming LLM turn against the mock gateway: the client hangs up while the
#   turn is still in flight.  The server must stay healthy, must cancel the
#   turn, and must not try to send updates into the closed socket.
# - Run the regression unit suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MOCK_PORT="${PR740_MOCK_PORT:-8022}"
APP_PORT="${PR740_APP_PORT:-8023}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

# ==========================================================================
# Check 1: no huggingface.co tokenizer download for llama-family models
# ==========================================================================
print_header "Check 1: LiteLLM does not attempt the HF tokenizer download"

python - <<'PY'
import sys
import time

# Importing the caller is what applies the setting.
import atlas.modules.llm.litellm_caller  # noqa: F401
import litellm
from litellm.utils import _select_tokenizer_helper

if litellm.disable_hf_tokenizer_download is not True:
    print("FAIL: litellm.disable_hf_tokenizer_download is not True")
    sys.exit(1)

# The real selection path for a llama-3 model name.  Before this change it
# called a blocking Tokenizer.from_pretrained("Xenova/llama-3-tokenizer") from
# inside the event loop.
start = time.monotonic()
result = _select_tokenizer_helper("meta/llama-3-70b-instruct")
elapsed = time.monotonic() - start

if result["type"] != "openai_tokenizer":
    print(f"FAIL: expected the local tiktoken fallback, got {result['type']}")
    sys.exit(1)

# A real network attempt -- success, failure or retry -- cannot finish this fast.
if elapsed > 1.0:
    print(f"FAIL: tokenizer selection took {elapsed:.2f}s -- network was attempted")
    sys.exit(1)

print(f"OK: llama-3 model resolved to {result['type']} in {elapsed * 1000:.0f}ms")
PY
print_result $? "llama-family models use the local tokenizer, no HF download"

# ==========================================================================
# Check 2: mid-turn disconnect leaves the server healthy, turn cancelled
# ==========================================================================
print_header "Check 2: websocket disconnect mid-turn is handled gracefully"

WORK_DIR="$(mktemp -d)"
SERVER_LOG="$WORK_DIR/atlas.log"
MOCK_LOG="$WORK_DIR/mock.log"
MOCK_PID=""
APP_PID=""

cleanup() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# --- mock LLM gateway ---
MOCK_LLM_PORT="$MOCK_PORT" python "$PROJECT_ROOT/mocks/llm-mock/main.py" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 40); do
    curl -s "${MOCK_URL}/health" >/dev/null 2>&1 && break
    sleep 0.5
done

# Make the gateway hang so the turn is unambiguously still in flight when the
# client hangs up.  Without this the mock answers in milliseconds and the race
# is decided by scheduling luck.
curl -s -X POST "${MOCK_URL}/test/force-error" \
    -H "Content-Type: application/json" \
    -d '{"error_type": "timeout", "count": 5}' >/dev/null

# --- Atlas app, configured with a llama-3 model pointed at the mock ---
cat > "$WORK_DIR/serve.py" <<PY
import logging

import uvicorn

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import ModelConfig

config = app_factory.get_config_manager()
config.app_settings.debug_mode = True
config.app_settings.test_user = "test@test.com"
config.llm_config.models["llama3-mock"] = ModelConfig(
    model_name="meta/llama-3-70b-instruct",
    model_url="${MOCK_URL}",
    api_key="test-key",
)

from atlas.main import app  # noqa: E402  (import after config is primed)

# The disconnect path reports at INFO; make sure it reaches the log.
logging.getLogger("atlas.main").setLevel(logging.INFO)
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.INFO)

uvicorn.run(app, host="127.0.0.1", port=${APP_PORT}, log_level="info")
PY

python "$WORK_DIR/serve.py" >"$SERVER_LOG" 2>&1 &
APP_PID=$!

APP_UP=1
for _ in $(seq 1 60); do
    if curl -s "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null 2>&1; then
        APP_UP=0
        break
    fi
    sleep 0.5
done

if [ "$APP_UP" -ne 0 ]; then
    echo "FAIL: Atlas app did not start"
    tail -40 "$SERVER_LOG"
    print_result 1 "disconnect cancels the turn and the server stays healthy"
else
    python - "$APP_PORT" "$SERVER_LOG" <<'PY'
import json
import sys
import time
import urllib.request

from websockets.sync.client import connect

port, log_path = sys.argv[1], sys.argv[2]

# Start a turn, then hang up while the gateway is still hanging.
with connect(
    f"ws://127.0.0.1:{port}/ws",
    additional_headers={"X-User-Email": "test@test.com"},
) as ws:
    ws.send(json.dumps({
        "type": "chat",
        "content": "hello",
        "model": "llama3-mock",
    }))
    time.sleep(1.5)

# The server must still be serving everyone else.
deadline = time.time() + 15
healthy = False
while time.time() < deadline:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=2
        ) as resp:
            if resp.status == 200:
                healthy = True
                break
    except Exception:
        time.sleep(0.3)

if not healthy:
    print("FAIL: /api/health did not respond after the client disconnected")
    sys.exit(1)

# Give the disconnect handler a moment to log.
time.sleep(1.0)
log = open(log_path).read()

if "Cancelling active chat task (client disconnected)" not in log:
    print("FAIL: disconnect did not cancel the in-flight turn")
    print(log[-3000:])
    sys.exit(1)

if 'Cannot call "send" once a close message has been sent' in log:
    print("FAIL: an update was still sent into the closed websocket")
    sys.exit(1)

if "Task exception was never retrieved" in log:
    print("FAIL: the chat task leaked an unhandled exception")
    sys.exit(1)

print("OK: server healthy, turn cancelled, no send-after-close, no leaked task")
PY
    print_result $? "disconnect cancels the turn and the server stays healthy"
fi

cleanup
trap - EXIT

# ==========================================================================
# Check 3: regression unit suite
# ==========================================================================
print_header "Check 3: regression unit tests"

(cd "$PROJECT_ROOT/atlas" && python -m pytest tests/test_websocket_disconnect_resilience.py -q)
print_result $? "atlas/tests/test_websocket_disconnect_resilience.py"

echo ""
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
echo "=========================================="
[ $FAILED -eq 0 ] && exit 0 || exit 1
