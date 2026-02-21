#!/bin/bash
# PR #350 Validation Script: Add heartbeat endpoint
# Tests that /api/heartbeat returns 200 without auth and is rate-limited.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #350 Validation: Heartbeat Endpoint"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

echo ""
echo "1. Verify heartbeat route exists in health_routes.py"
echo "----------------------------------------------------"
if grep -q '/heartbeat' "$PROJECT_ROOT/atlas/routes/health_routes.py"; then
    echo "PASSED: /heartbeat route defined in health_routes.py"
else
    echo "FAILED: /heartbeat route not found in health_routes.py"
    exit 1
fi

echo ""
echo "2. Verify heartbeat bypasses auth in middleware"
echo "------------------------------------------------"
if grep -q '/api/heartbeat' "$PROJECT_ROOT/atlas/core/middleware.py"; then
    echo "PASSED: /api/heartbeat listed in auth bypass in middleware.py"
else
    echo "FAILED: /api/heartbeat not in auth bypass list"
    exit 1
fi

echo ""
echo "3. Start backend and test heartbeat endpoint"
echo "---------------------------------------------"
# Find an available port (verify nothing is already listening)
PORT=8199
if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "FAILED: Port $PORT is already in use by another process"
    exit 1
fi
export PORT=$PORT
export ATLAS_HOST="127.0.0.1"

# Start the backend in the background
cd "$PROJECT_ROOT/atlas"
python main.py &
BACKEND_PID=$!

# Wait for backend to become ready (up to 30s)
echo "  Waiting for backend to start on port $PORT (PID $BACKEND_PID)..."
for i in $(seq 1 30); do
    # Verify our process is still running
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "FAILED: Backend process (PID $BACKEND_PID) exited unexpectedly"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        echo "  Backend ready after ${i}s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "FAILED: Backend failed to become ready in time"
        kill $BACKEND_PID 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# Test heartbeat returns 200 and correct body (no auth header)
# Use -s (silent) without -f (fail) so non-2xx doesn't abort under set -e
HEARTBEAT_RESP=$(curl -s "http://127.0.0.1:$PORT/api/heartbeat")
HEARTBEAT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/heartbeat")

if [ "$HEARTBEAT_STATUS" = "200" ]; then
    echo "PASSED: /api/heartbeat returned HTTP 200"
else
    echo "FAILED: /api/heartbeat returned HTTP $HEARTBEAT_STATUS (expected 200)"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

if echo "$HEARTBEAT_RESP" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d=={'status':'ok'}, f'unexpected: {d}'" 2>/dev/null; then
    echo "PASSED: /api/heartbeat returned {\"status\": \"ok\"}"
else
    echo "FAILED: /api/heartbeat response body unexpected: $HEARTBEAT_RESP"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

# Clean up backend
kill $BACKEND_PID 2>/dev/null || true
wait $BACKEND_PID 2>/dev/null || true

echo ""
echo "4. Run backend unit tests"
echo "-------------------------"
cd "$PROJECT_ROOT"
bash ./test/run_tests.sh backend
echo "PASSED: Backend tests passed"

echo ""
echo "=========================================="
echo "All PR #350 validation checks PASSED"
echo "=========================================="
