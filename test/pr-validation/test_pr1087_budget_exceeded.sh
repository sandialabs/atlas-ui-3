#!/bin/bash
# Test script for PR #1087: report exhausted LiteLLM budgets as budget_exceeded
#
# Test plan:
# - A local OpenAI-compatible mock returns LiteLLM's captured budget refusals:
#   422 (>= 1.104), 429 (older / budget_exceeded_status_code), 400 (per-model
#   virtual key), and a routing 429 with no structured type ("crossed budget")
# - Each real litellm request is classified as LLMBudgetExceededError with the
#   budget-specific user copy, and only ONE request reaches the mock (no retry)
# - The raw key/team/spend text never appears in the user-facing message
# - A real RPM/TPM 429 keeps the rate-limit classification and is retried
# - classify_llm_error() reports error_type "budget_exceeded"
# - Run the backend unit test suite

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

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
export PYTHONPATH="$PROJECT_ROOT"

WORK_DIR=$(mktemp -d -t atlas-pr1087-XXXXXX)
COUNTS_FILE="$WORK_DIR/counts.json"
MOCK_LOG="$WORK_DIR/mock.log"
PORT=$(python - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)

cat > "$WORK_DIR/mock_proxy.py" <<'PY'
"""OpenAI-compatible mock returning LiteLLM's budget-refusal envelopes."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
counts_file = sys.argv[2]

RESPONSES = {
    "budget422": (422, {
        "error": {
            "message": ("Budget has been exceeded! Key=lit-8229-exhausted "
                        "(sk-...X-rQ) Current cost: 0.0, Max budget: 0.0"),
            "type": "budget_exceeded", "param": None, "code": "422",
        }
    }),
    "budget429": (429, {
        "error": {
            "message": "Budget has been exceeded! Key=lit-8229-exhausted (sk-...X-rQ)",
            "type": "budget_exceeded", "param": None, "code": "429",
        }
    }),
    "budget400": (400, {
        "error": {
            "message": "Budget has been exceeded for model gpt-4o.",
            "type": "budget_exceeded", "param": None, "code": "400",
        }
    }),
    "routing429": (429, {
        "error": {
            "message": "Deployment crossed budget for provider azure; refusing request.",
            "type": None, "param": None, "code": "429",
        }
    }),
    "rpm429": (429, {
        "error": {
            "message": ("Rate limit reached for gpt-4o on tokens per min (TPM): "
                        "Limit 30000, Used 30000."),
            "type": "tokens", "param": None, "code": "429",
        }
    }),
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        scenario = self.path.strip("/").split("/")[0]
        status, body = RESPONSES.get(scenario, (404, {"error": {"message": "unknown"}}))
        counts = {}
        try:
            with open(counts_file) as fh:
                counts = json.load(fh)
        except (OSError, ValueError):
            pass
        counts[scenario] = counts.get(scenario, 0) + 1
        with open(counts_file, "w") as fh:
            json.dump(counts, fh)
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
PY

python "$WORK_DIR/mock_proxy.py" "$PORT" "$COUNTS_FILE" > "$MOCK_LOG" 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null; rm -rf "$WORK_DIR"' EXIT

python - "$PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 10
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            break
    except OSError:
        time.sleep(0.05)
else:
    raise SystemExit("mock proxy did not start")
PY
print_result $? "Mock OpenAI-compatible proxy started"

LLM_MAX_RETRIES=2 LLM_RETRY_MAX_WAIT_SECONDS=30 python - "$PORT" "$COUNTS_FILE" <<'PY'
import asyncio
import json
import os
import sys

import litellm

from atlas.application.chat.utilities.error_handler import classify_llm_error
from atlas.domain.errors import (
    LLM_BUDGET_EXCEEDED_MESSAGE,
    LLMBudgetExceededError,
    RateLimitError,
)
from atlas.modules.llm.litellm_caller import LiteLLMCaller

# litellm's own global `drop_params` handling retries a 422 with parameters
# dropped and can swallow the second refusal. That is litellm's param-retry
# behaviour, not Atlas's classification; disabling it here keeps the real
# provider exception visible so the classifier is exercised end to end.
litellm.drop_params = False

port, counts_file = sys.argv[1], sys.argv[2]
BUDGET_MESSAGE = LLM_BUDGET_EXCEEDED_MESSAGE
SECRETS = ("lit-8229-exhausted", "sk-...X-rQ", "Current cost", "Max budget")

failures = []


def check(condition, label):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label}")
        failures.append(label)


def count(scenario):
    try:
        with open(counts_file) as fh:
            return json.load(fh).get(scenario, 0)
    except (OSError, ValueError):
        return 0


async def classify(scenario, model="openai/gpt-4o"):
    caller = object.__new__(LiteLLMCaller)
    try:
        await caller._acompletion_with_retry(
            model=model,
            messages=[{"role": "user", "content": "hello"}],
            api_base=f"http://127.0.0.1:{port}/{scenario}/v1",
            api_key="test-key",
            # Provider-client retries (OpenAI SDK) are separate from Atlas's
            # backoff loop; disable them so the request count reflects Atlas's
            # own retry decisions.
            max_retries=0,
        )
    except Exception as exc:
        try:
            caller._raise_llm_domain_error(exc)
        except Exception as domain_exc:
            return domain_exc
        raise AssertionError(f"{scenario} produced no domain error")
    raise AssertionError(f"{scenario} unexpectedly succeeded")


async def main():
    for scenario in ("budget422", "budget429", "budget400", "routing429"):
        err = await classify(scenario)
        check(isinstance(err, LLMBudgetExceededError), f"{scenario}: classified as budget error")
        check(err.message == BUDGET_MESSAGE, f"{scenario}: budget-specific user copy")
        check(count(scenario) == 1, f"{scenario}: not retried (1 request, got {count(scenario)})")
        check(not any(s in err.message for s in SECRETS), f"{scenario}: no key/team/spend leak")
        check(err.__cause__ is not None, f"{scenario}: original cause preserved")

    rpm = await classify("rpm429")
    check(isinstance(rpm, RateLimitError), "rpm429: still a rate limit")
    check("high traffic" in rpm.message, "rpm429: keeps the existing rate-limit copy")
    check(count("rpm429") >= 2, f"rpm429: still retried ({count('rpm429')} requests)")

    err_class, user_msg, _log = classify_llm_error(LLMBudgetExceededError(BUDGET_MESSAGE))
    check(err_class is LLMBudgetExceededError, "classify_llm_error preserves the budget class")
    check(user_msg == BUDGET_MESSAGE, "classify_llm_error preserves the budget copy")

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        raise SystemExit(1)
    print("\nAll budget-exceeded checks passed")


asyncio.run(main())
PY
print_result $? "Budget-exceeded end-to-end checks"

./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
