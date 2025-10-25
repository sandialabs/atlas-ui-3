#!/bin/bash
set -euo pipefail

trap 'rc=$?; echo "🧹 Cleaning up..."; [[ -n "${BACKEND_PID-}" ]] && { echo "Killing backend process (PID: $BACKEND_PID)"; kill "${BACKEND_PID}" 2>/dev/null || true; }; exit $rc' EXIT

echo "Running E2E Tests..."
echo "================================="

# Resolve project root
: "${PROJECT_ROOT:=$(pwd)}"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
BACKEND_DIR="$PROJECT_ROOT/backend"
E2E_DIR="$PROJECT_ROOT/test_e2e"

echo "Project root: $PROJECT_ROOT"
echo "Frontend directory: $FRONTEND_DIR"
echo "Backend directory: $BACKEND_DIR"
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
    echo "✅ Frontend build verified (dist directory exists)"
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
cd "$BACKEND_DIR"

# Check if port 8000 is already in use
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "⚠️  Port 8000 is already in use. Attempting to continue with existing service..."
    # Get the PID of the existing process for potential cleanup
    EXISTING_PID=$(lsof -Pi :8000 -sTCP:LISTEN -t 2>/dev/null | head -1)
    echo "ℹ️  Existing service PID: ${EXISTING_PID:-unknown}"
else
    echo "Starting uvicorn server..."
    uvicorn main:app --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
    
    # Give the server a moment to start
    sleep 3
    
    # Verify the process started successfully
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "❌ Backend process failed to start or died immediately"
        exit 1
    fi
    
    echo "✅ Backend server started successfully (PID: $BACKEND_PID)"
fi

# Wait for backend to be healthy (probe)
echo "Waiting for backend to become ready..."
MAX_RETRIES=15
RETRY_INTERVAL=2
SUCCESS=false
for i in $(seq 1 $MAX_RETRIES); do
    if curl --silent --fail http://127.0.0.1:8000/api/config >/dev/null 2>&1; then
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
    echo "🎉 Simple E2E tests completed successfully!"
else
    echo "💥 Simple E2E tests failed."
    exit 1
fi

echo "E2E tests finished."

# Explicit cleanup before exit
if [[ -n "${BACKEND_PID-}" ]]; then
    echo "🧹 Stopping backend server (PID: $BACKEND_PID)..."
    kill "${BACKEND_PID}" 2>/dev/null || true
    # Wait a moment for graceful shutdown
    sleep 2
    # Force kill if still running
    kill -9 "${BACKEND_PID}" 2>/dev/null || true
    echo "✅ Backend server stopped"
fi
