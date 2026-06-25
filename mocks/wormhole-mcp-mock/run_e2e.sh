#!/bin/bash
# End-to-end test for the Wormhole subtoken flow (issue #640).
# Starts the mock MCP server, runs the driver against the real Atlas code, and
# leaves the dashboard at /status populated for inspection.
set -e
cd "$(dirname "$0")"

PORT="${WORMHOLE_MOCK_PORT:-8021}"
PROJECT_ROOT="$(cd ../.. && pwd)"
export WORMHOLE_MOCK_URL="http://127.0.0.1:${PORT}"
export FEATURE_WORMHOLE_ENABLED=true
export MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-e2e-wormhole-test-key-not-a-placeholder-32+}"

echo "Starting Wormhole MCP mock on port ${PORT}..."
WORMHOLE_MOCK_PORT="${PORT}" python main.py >/tmp/wormhole_mock.log 2>&1 &
MOCK_PID=$!
cleanup() { kill "${MOCK_PID}" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for health
for _ in $(seq 1 30); do
  curl -s "${WORMHOLE_MOCK_URL}/health" >/dev/null 2>&1 && break
  sleep 0.5
done

echo "Running E2E driver..."
PYTHONPATH="${PROJECT_ROOT}" python e2e_wormhole_test.py
RC=$?
echo "Dashboard (while mock runs): ${WORMHOLE_MOCK_URL}/status"
exit $RC
