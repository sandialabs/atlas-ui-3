#!/bin/bash
set -euo pipefail

stop_process_gracefully() {
    local pid="$1"
    local label="$2"

    if [[ -z "${pid:-}" ]]; then
        return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    echo "Stopping ${label} (PID: $pid)..."

    # Prefer SIGINT for uvicorn/FastMCP so it can drain cleanly.
    kill -INT "$pid" 2>/dev/null || true

    # Wait up to 15s for a clean shutdown.
    for _ in $(seq 1 15); do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null || true
            return 0
        fi
        sleep 1
    done

    echo "${label} did not stop after SIGINT; sending SIGTERM..."
    kill -TERM "$pid" 2>/dev/null || true

    for _ in $(seq 1 5); do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null || true
            return 0
        fi
        sleep 1
    done

    echo "${label} still running; sending SIGKILL..."
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

dump_e2e_logs() {
    echo "E2E failed; dumping last 80 lines of logs"
    if [[ -f "${BACKEND_LOG-}" ]]; then
        echo "--- backend.log ---"
        tail -n 80 "$BACKEND_LOG" || true
    fi
    if [[ -f "${MCP_MOCK_LOG-}" ]]; then
        echo "--- mcp-mock.log ---"
        tail -n 80 "$MCP_MOCK_LOG" || true
    fi
}

echo "Running E2E Tests..."
echo "================================="

# Resolve project root
: "${PROJECT_ROOT:=$(pwd)}"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
BACKEND_DIR="$PROJECT_ROOT/backend"
E2E_DIR="$PROJECT_ROOT/test_e2e"
MCP_MOCK_DIR="$PROJECT_ROOT/mocks/mcp-http-mock"
E2E_LOG_DIR="$PROJECT_ROOT/logs/e2e"
BACKEND_LOG="$E2E_LOG_DIR/backend.log"
MCP_MOCK_LOG="$E2E_LOG_DIR/mcp-mock.log"

echo "Project root: $PROJECT_ROOT"
echo "Frontend directory: $FRONTEND_DIR"
echo "Backend directory: $BACKEND_DIR"
echo "E2E test directory: $E2E_DIR"
echo "MCP mock directory: $MCP_MOCK_DIR"
mkdir -p "$E2E_LOG_DIR"
echo "E2E logs directory: $E2E_LOG_DIR"

trap 'rc=$?; echo "Cleaning up..."; if [[ $rc -ne 0 ]]; then dump_e2e_logs; fi; stop_process_gracefully "${BACKEND_PID-}" "backend"; stop_process_gracefully "${MCP_MOCK_PID-}" "mcp-mock"; exit $rc' EXIT

# Ensure Python virtual environment is activated so dependencies (fastmcp, etc.) are available
if [ -d "$PROJECT_ROOT/.venv" ]; then
    echo "Activating Python virtual environment at $PROJECT_ROOT/.venv"
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "WARNING: .venv directory not found at $PROJECT_ROOT/.venv; proceeding without virtualenv"
fi

# Ensure tools are visible via /api/config (frontend relies on this)
export FEATURE_TOOLS_ENABLED="${FEATURE_TOOLS_ENABLED:-true}"

# Ensure our test MCP config is used if test/run_tests.sh wasn't the entrypoint
export MCP_CONFIG_FILE="${MCP_CONFIG_FILE:-mcp-test.json}"

# Handle frontend build based on environment
cd "$FRONTEND_DIR"

if [ "${ENVIRONMENT:-}" = "cicd" ]; then
    echo "CI/CD environment: Frontend already built during Docker build, skipping rebuild..."
    # Verify dist directory exists
    if [ ! -d "dist" ]; then
        echo "ERROR: Frontend dist directory not found. Docker build may have failed."
        exit 1
    fi
    echo "Frontend build verified (dist directory exists)"
else
    echo "Local environment: Installing dependencies and building frontend..."
    export PATH="$FRONTEND_DIR/node_modules/.bin:$PATH"
    echo "Current PATH: $PATH"

    echo "Installing frontend dependencies..."
    npm install

    # Verify vite exists (local)
    if ! command -v vite >/dev/null 2>&1; then
        echo "vite binary not found in node_modules/.bin. Listing installed packages for debugging:"
        ls -1 node_modules/.bin || true
        echo "Attempting to install vite explicitly..."
        npx vite --version || {
            echo "Failed to get vite. Check that 'vite' is declared in package.json dependencies/devDependencies."
            exit 1
        }
    fi

    echo "Building frontend..."
    # Set VITE_APP_NAME for build (required for index.html template replacement)
    export VITE_APP_NAME="Chat UI"
    npx vite build
fi

# Start backend with startup validation
echo "Starting backend server..."

# Start MCP HTTP mock server (requires Bearer token auth)
echo "Starting MCP HTTP mock server..."
if lsof -Pi :8005 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Port 8005 is already in use; assuming MCP mock is running."
else
    cd "$MCP_MOCK_DIR"
    # Redirect server output to log file; only print on failures.
    python main.py >"$MCP_MOCK_LOG" 2>&1 &
    MCP_MOCK_PID=$!
    sleep 2
    if ! kill -0 "$MCP_MOCK_PID" 2>/dev/null; then
        echo "MCP mock process failed to start or died immediately"
        tail -n 80 "$MCP_MOCK_LOG" || true
        exit 1
    fi
    echo "MCP mock server started successfully (PID: $MCP_MOCK_PID)"
fi

cd "$BACKEND_DIR"

# Check if port 8000 is already in use
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Port 8000 is already in use. Stopping existing service to ensure a clean E2E environment..."
    EXISTING_PID=$(lsof -Pi :8000 -sTCP:LISTEN -t 2>/dev/null | head -1)
    echo "ℹ️  Existing service PID: ${EXISTING_PID:-unknown}"
    if [[ -n "${EXISTING_PID:-}" ]]; then
        kill "${EXISTING_PID}" 2>/dev/null || true
        sleep 2
        kill -9 "${EXISTING_PID}" 2>/dev/null || true
        sleep 1
    fi
fi

echo "Starting uvicorn server..."
# Keep warnings out of console; capture them in backend.log.
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore::DeprecationWarning}"
uvicorn main:app --host 0.0.0.0 --port 8000 >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

# Give the server a moment to start
sleep 3

# Verify the process started successfully
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend process failed to start or died immediately"
    tail -n 80 "$BACKEND_LOG" || true
    exit 1
fi

echo "Backend server started successfully (PID: $BACKEND_PID)"

# Wait for backend to be healthy (probe)
echo "Waiting for backend to become ready..."
MAX_RETRIES=15
RETRY_INTERVAL=2
SUCCESS=false
for i in $(seq 1 $MAX_RETRIES); do
    # /api/config is protected by AuthMiddleware in production mode.
    # Use the configured test user header so readiness works in both
    # DEBUG_MODE=true and DEBUG_MODE=false environments.
    if curl --silent --fail \
        -H "X-User-Email: test@test.com" \
        http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
        echo "Backend is up (after $i attempt(s))"
        SUCCESS=true
        break
    fi
    echo "  [${i}/${MAX_RETRIES}] backend not ready yet, sleeping ${RETRY_INTERVAL}s..."
    sleep $RETRY_INTERVAL
done
if ! $SUCCESS; then
    echo "Backend failed to become ready in time. Dumping last few lines of backend process (if any):"
    ps -p "$BACKEND_PID" && kill -0 "$BACKEND_PID" 2>/dev/null || true
    tail -n 80 "$BACKEND_LOG" || true
    exit 1
fi

# Run simple Python E2E tests
echo "Running simple E2E tests..."
cd "$PROJECT_ROOT/test"

echo "Executing simple E2E test suite..."

# Run the simple Python E2E tests
if python3 simple_e2e_test.py; then
    echo "Simple E2E tests completed successfully"
else
    echo "Simple E2E tests failed"
    exit 1
fi

echo "Running OAuth/JWT request-level E2E tests (no Playwright)..."
if python3 simple_e2e_oauth_jwt_workflow.py; then
    echo "OAuth/JWT E2E tests completed successfully"
else
    echo "OAuth/JWT E2E tests failed"
    exit 1
fi

echo "E2E tests finished."

# Explicit cleanup before exit
if [[ -n "${BACKEND_PID-}" ]]; then
    stop_process_gracefully "${BACKEND_PID}" "backend"
    echo "Backend server stopped"
    unset BACKEND_PID
fi

if [[ -n "${MCP_MOCK_PID-}" ]]; then
    stop_process_gracefully "${MCP_MOCK_PID}" "mcp-mock"
    echo "MCP mock server stopped"
    unset MCP_MOCK_PID
fi
