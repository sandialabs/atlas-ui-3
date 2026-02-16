#!/bin/bash
set -euo pipefail

trap 'rc=$?; echo "Cleaning up..."; [[ -n "${BACKEND_PID-}" ]] && { echo "Killing backend process (PID: $BACKEND_PID)"; kill "${BACKEND_PID}" 2>/dev/null || true; }; exit $rc' EXIT

echo "Running E2E Tests..."
echo "================================="

# Resolve project root
: "${PROJECT_ROOT:=$(pwd)}"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
ATLAS_DIR="$PROJECT_ROOT/atlas"
E2E_DIR="$PROJECT_ROOT/test_e2e"

echo "Project root: $PROJECT_ROOT"
echo "Frontend directory: $FRONTEND_DIR"
echo "Atlas directory: $ATLAS_DIR"
echo "E2E test directory: $E2E_DIR"

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
    # export VITE_APP_NAME="Chat UI"
    export VITE_APP_NAME="ATLAS-3"
    export VITE_FEATURE_POWERED_BY_ATLAS="false"
    npx vite build
fi

# Start backend with startup validation
echo "Starting backend server..."
cd "$ATLAS_DIR"

# Ensure Python virtual environment is activated so uvicorn is available
if [ -d "$PROJECT_ROOT/.venv" ]; then
    echo "Activating Python virtual environment at $PROJECT_ROOT/.venv"
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "WARNING: .venv directory not found at $PROJECT_ROOT/.venv; proceeding without virtualenv"
fi

# Set PYTHONPATH so atlas package imports work correctly
export PYTHONPATH="$PROJECT_ROOT"
echo "PYTHONPATH set to: $PYTHONPATH"

# Read port from .env file, default to 8000
E2E_PORT=8000
if [ -f "$PROJECT_ROOT/.env" ]; then
    ENV_PORT=$(grep -E '^PORT=' "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    if [ -n "$ENV_PORT" ]; then
        E2E_PORT="$ENV_PORT"
        echo "Using port $E2E_PORT from .env file"
    fi
fi
export E2E_PORT
echo "E2E test port: $E2E_PORT"

# Check if port is already in use
if lsof -Pi :"$E2E_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Port $E2E_PORT is already in use. Attempting to continue with existing service..."
    # Get the PID of the existing process for potential cleanup
    EXISTING_PID=$(lsof -Pi :"$E2E_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
    echo "Existing service PID: ${EXISTING_PID:-unknown}"
else
    echo "Starting uvicorn server..."
    uvicorn main:app --host 0.0.0.0 --port "$E2E_PORT" &
    BACKEND_PID=$!

    # Give the server a moment to start
    sleep 3

    # Verify the process started successfully
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "Backend process failed to start or died immediately"
        exit 1
    fi

    echo "Backend server started successfully (PID: $BACKEND_PID)"
fi

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
        "http://127.0.0.1:${E2E_PORT}/api/config" >/dev/null 2>&1; then
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
    exit 1
fi

# Run simple Python E2E tests
echo "Running simple E2E tests..."
cd "$PROJECT_ROOT/test"

echo "Executing simple E2E test suite..."

# Run the simple Python E2E tests
if python3 simple_e2e_test.py; then
    echo "Simple E2E tests completed successfully!"
else
    echo "Simple E2E tests failed."
    exit 1
fi

echo "E2E tests finished."

# Explicit cleanup before exit
if [[ -n "${BACKEND_PID-}" ]]; then
    echo "Stopping backend server (PID: $BACKEND_PID)..."
    kill "${BACKEND_PID}" 2>/dev/null || true
    # Wait a moment for graceful shutdown
    sleep 2
    # Force kill if still running
    kill -9 "${BACKEND_PID}" 2>/dev/null || true
    echo "Backend server stopped"
fi
