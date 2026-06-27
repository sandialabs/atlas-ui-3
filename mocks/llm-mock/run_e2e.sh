#!/bin/bash
# End-to-end test: the configured per-model API key wins over a conflicting
# OPENAI_API_KEY, and requests for an OpenAI-looking model go to the configured
# mock gateway (PR #678 / API key coercion removal).
#
# Starts the mock LLM server and runs the driver against the real Atlas code.
set -e
cd "$(dirname "$0")"

PORT="${MOCK_LLM_PORT:-8002}"
PROJECT_ROOT="$(cd ../.. && pwd)"
export MOCK_LLM_URL="http://127.0.0.1:${PORT}"
# Require auth so the mock proves a credential actually arrived on the wire.
export MOCK_LLM_REQUIRE_AUTH=true

echo "Starting Mock LLM server on port ${PORT}..."
MOCK_LLM_PORT="${PORT}" python main.py >/tmp/llm_mock_e2e.log 2>&1 &
MOCK_PID=$!
cleanup() { kill "${MOCK_PID}" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for health
for _ in $(seq 1 30); do
  curl -s "${MOCK_URL}/health" >/dev/null 2>&1 && break
  sleep 0.5
done

echo "Running E2E driver..."
PYTHONPATH="${PROJECT_ROOT}" python e2e_llm_api_key_test.py
RC=$?
exit $RC
