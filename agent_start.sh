#!/bin/bash

# Store the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Global variables
MCP_PID=""
ONLY_FRONTEND=false
ONLY_BACKEND=false
START_MCP_MOCK=false
CONTAINER_CMD=""
COMPOSE_CMD=""

# =============================================================================
# CLEANUP FUNCTIONS
# =============================================================================

cleanup_mcp() {
    if [ ! -z "$MCP_PID" ] && kill -0 $MCP_PID 2>/dev/null; then
        echo "Stopping MCP mock server (PID: $MCP_PID)..."
        kill $MCP_PID
        wait $MCP_PID 2>/dev/null
        echo "MCP mock server stopped."
    fi
}

cleanup_processes() {
    echo "Killing any running uvicorn processes for main backend..."
    pkill -f "uvicorn main:app" || true
    sleep 2
}

cleanup_logs() {
    echo "Clearing log for fresh start"
    mkdir -p "$PROJECT_ROOT/logs"
    echo "NEW LOG" > "$PROJECT_ROOT/logs/app.jsonl"
}

# =============================================================================
# CONTAINER RUNTIME DETECTION
# =============================================================================

setup_container_runtime() {
    # Detect if podman or docker is available
    CONTAINER_CMD=""
    COMPOSE_CMD=""

    # Check for podman first
    if command -v podman &> /dev/null; then
        CONTAINER_CMD="podman"

        # Check for podman-compose or podman compose
        if command -v podman-compose &> /dev/null; then
            COMPOSE_CMD="podman-compose"
        else
            # Use podman compose (newer versions)
            COMPOSE_CMD="podman compose"
        fi

        echo "Using Podman as container runtime"
        return
    fi

    # Check for docker
    if command -v docker &> /dev/null; then
        CONTAINER_CMD="docker"

        # Check if docker compose (v2) is available
        if docker compose version &> /dev/null; then
            COMPOSE_CMD="docker compose"
        else
            # Fall back to docker-compose v1
            COMPOSE_CMD="docker-compose"
        fi

        echo "Using Docker as container runtime"
        return
    fi

    # Neither found
    echo "Warning: Neither Docker nor Podman found. Container operations will be skipped."
}

# =============================================================================
# INFRASTRUCTURE FUNCTIONS
# =============================================================================

setup_minio() {
    local use_mock_s3="${USE_MOCK_S3:-true}"
    
    # Read USE_MOCK_S3 from .env file if it exists
    if [ -f "$PROJECT_ROOT/.env" ]; then
        use_mock_s3=$(grep -E "^USE_MOCK_S3=" "$PROJECT_ROOT/.env" | cut -d '=' -f2)
    fi
    
    if [ "$use_mock_s3" = "true" ]; then
        echo "Using Mock S3 (no Docker/Podman required)"
    else
        if [ -z "$CONTAINER_CMD" ]; then
            echo "Error: Container runtime not available. Please install Docker or Podman, or set USE_MOCK_S3=true in .env"
            exit 1
        fi

        if ! $CONTAINER_CMD ps | grep -q atlas-minio; then
            echo "MinIO is not running. Starting MinIO with $COMPOSE_CMD..."
            cd "$PROJECT_ROOT"
            $COMPOSE_CMD up -d minio minio-init
            echo "MinIO started successfully"
            sleep 3
        else
            echo "MinIO is already running"
        fi
    fi
    cd "$PROJECT_ROOT"
}

setup_chat_history_db() {
    local chat_history_enabled="false"
    local db_url=""

    # Read settings from .env
    if [ -f "$PROJECT_ROOT/.env" ]; then
        chat_history_enabled=$(grep -E "^FEATURE_CHAT_HISTORY_ENABLED=" "$PROJECT_ROOT/.env" | cut -d '=' -f2)
        db_url=$(grep -E "^CHAT_HISTORY_DB_URL=" "$PROJECT_ROOT/.env" | cut -d '=' -f2)
    fi

    if [ "$chat_history_enabled" != "true" ]; then
        echo "Chat history disabled (FEATURE_CHAT_HISTORY_ENABLED != true)"
        return
    fi

    # Default to DuckDB if no URL specified
    if [ -z "$db_url" ]; then
        db_url="duckdb:///data/chat_history.db"
    fi

    if echo "$db_url" | grep -q "^postgresql"; then
        echo "Chat history: PostgreSQL mode"
        if [ -z "$CONTAINER_CMD" ]; then
            echo "Error: PostgreSQL requires Docker/Podman. Install one or switch to DuckDB."
            echo "  DuckDB: CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db"
            exit 1
        fi

        if ! $CONTAINER_CMD ps | grep -q atlas-postgres; then
            echo "PostgreSQL is not running. Starting with $COMPOSE_CMD..."
            cd "$PROJECT_ROOT"
            $COMPOSE_CMD up -d postgres
            echo "Waiting for PostgreSQL to be ready..."
            max_wait=60
            waited=0
            until $COMPOSE_CMD exec -T postgres pg_isready >/dev/null 2>&1; do
                if [ "$waited" -ge "$max_wait" ]; then
                    echo "PostgreSQL did not become ready within ${max_wait}s."
                    exit 1
                fi
                sleep 2
                waited=$((waited + 2))
            done
            echo "PostgreSQL is ready."
        else
            echo "PostgreSQL is already running"
        fi
    elif echo "$db_url" | grep -q "^duckdb"; then
        echo "Chat history: DuckDB mode"
        # Ensure data directory exists for DuckDB file
        mkdir -p "$PROJECT_ROOT/data"
    else
        echo "Chat history: custom DB URL configured"
    fi
    cd "$PROJECT_ROOT"
}

setup_environment() {
    cd "$PROJECT_ROOT"
    
    # Check if .venv exists
    if [ ! -d "$PROJECT_ROOT/.venv" ]; then
        echo "Error: Virtual environment not found at $PROJECT_ROOT/.venv"
        echo "Please run: uv sync --dev"
        exit 1
    fi

    # Check if uvicorn is installed
    if [ ! -f "$PROJECT_ROOT/.venv/bin/uvicorn" ]; then
        echo "Error: uvicorn not found in virtual environment"
        echo "Please run: uv sync --dev"
        exit 1
    fi
    
    . .venv/bin/activate
    
    # Load environment variables from .env if present
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        . "$PROJECT_ROOT/.env"
        set +a
    fi
    
    echo "Setting MCP_EXTERNAL_API_TOKEN for testing purposes."
    if [ -z "$MCP_EXTERNAL_API_TOKEN" ]; then
        export MCP_EXTERNAL_API_TOKEN="test-api-key-123"
    fi
    cd "$PROJECT_ROOT"
}

# =============================================================================
# MCP MOCK SERVER FUNCTIONS
# =============================================================================

start_mcp_mock() {
    if [ "$START_MCP_MOCK" = true ]; then
        echo "Starting MCP mock server..."
        cd "$PROJECT_ROOT/mocks/mcp-http-mock"
        ./run.sh &
        MCP_PID=$!
        echo "MCP mock server started with PID: $MCP_PID"
        cd "$PROJECT_ROOT"
    fi
}

# =============================================================================
# FRONTEND BUILD FUNCTIONS
# =============================================================================

build_frontend() {
    local use_new_frontend="${USE_NEW_FRONTEND:-true}"
    
    echo "Building frontend..."
    cd "$PROJECT_ROOT/frontend"
    npm install
    # Use VITE_* values from the environment / .env instead of hardcoding.
    # If VITE_APP_NAME is not already set, fall back to the example default.
    if [ -z "$VITE_APP_NAME" ]; then
        export VITE_APP_NAME="Chat UI 13"
    fi
    npm run build
    cd "$PROJECT_ROOT"
}

# =============================================================================
# BACKEND SERVER FUNCTIONS
# =============================================================================

start_backend() {
    local port="${1:-8000}"
    local host="${2:-127.0.0.1}"

    cd "$PROJECT_ROOT/atlas"
    # The atlas package is installed in editable mode (pip install -e .), so
    # PYTHONPATH is no longer needed for atlas imports to work.
    # Set APP_CONFIG_DIR so user overrides in <project_root>/config/ take
    # precedence over package defaults in atlas/config/ (CWD is atlas/).
    APP_CONFIG_DIR="${APP_CONFIG_DIR:-$PROJECT_ROOT/config}" \
    "$PROJECT_ROOT/.venv/bin/uvicorn" main:app --host "$host" --port "$port" &
    echo "Backend server started on $host:$port"
    cd "$PROJECT_ROOT"
}

# =============================================================================
# MAIN EXECUTION FLOW
# =============================================================================

parse_arguments() {
    while getopts "fbm" opt; do
        case $opt in
            f)
                ONLY_FRONTEND=true
                ;;
            b)
                ONLY_BACKEND=true
                ;;
            m)
                START_MCP_MOCK=true
                ;;
            \?)
                echo "Usage: $0 [-f] [-b] [-m]"
                echo "  -f    Only rebuild frontend"
                echo "  -b    Only start backend"
                echo "  -m    Start MCP mock server"
                exit 1
                ;;
        esac
    done
}

main() {
    # Set trap to cleanup MCP on script exit
    trap cleanup_mcp EXIT

    # Parse command line arguments
    parse_arguments "$@"

    # Setup infrastructure
    # Note: setup_environment must run first to activate venv, so venv's podman-compose is found
    setup_environment
    setup_container_runtime
    setup_minio
    setup_chat_history_db

    # Handle frontend-only mode
    if [ "$ONLY_FRONTEND" = true ]; then
        build_frontend
        echo "Frontend rebuilt successfully. Exiting as requested."
        exit 0
    fi
    
    # Handle backend-only mode
    if [ "$ONLY_BACKEND" = true ]; then
        cleanup_processes
        cleanup_logs
        start_mcp_mock
        start_backend "${PORT:-8000}" "${ATLAS_HOST:-127.0.0.1}"
        echo "Backend server started."
        echo "Press Ctrl+C to stop all services."
        # Keep script running to prevent cleanup
        wait
        exit 0
    fi
    
    # Full startup mode (default)
    cleanup_processes
    cleanup_logs
    build_frontend
    start_mcp_mock
    start_backend "${PORT:-8000}" "${ATLAS_HOST:-127.0.0.1}"
    
    # Display MCP info if started
    if [ "$START_MCP_MOCK" = true ]; then
        echo "MCP mock server is running with PID: $MCP_PID"
        echo "To stop the MCP mock server manually, run: kill $MCP_PID"
    fi
    
    echo "All services started. Press Ctrl+C to stop."
    cd "$PROJECT_ROOT"
    
    # Keep script running to prevent cleanup
    wait
}

# Run main function with all arguments
main "$@"


# # print every 3 seconds saying it is running. do 10 times. print second since start
# for i in {1..10}
# do
#     echo "Server running for $((i * 3)) seconds"
#     sleep 3
# done

# wait X seconds. 
# waittime=10
# echo "Starting server, waiting for $waittime seconds before sending config request"
# for ((i=waittime; i>0; i--)); do
#     echo "Waiting... $i seconds remaining"
#     sleep 1
# done
# host=127.0.0.1
# echo "Sending config request to $host:8000/api/config"
# result=$(curl -X GET http://$host:8000/api/config -H "Content-Type: application/json" -d '{"key": "value"}')
# # use json format output in a pretty way


# # echo "Config request sent, result:"
# # echo $result | jq .
# # # print the result
# # echo "Config request result: $(echo $result | jq .)
# # "

# # just get the "tools" part of the result and prrety print it
# echo "Config request result: $(echo $result | jq '.tools')"

# # make a count for 20 seconds and prompt the human to cause any errors
# echo "server ready, you can now cause any errors in the UI"
# for ((i=20; i>0; i--)); do
#     echo "You have $i seconds to cause any errors in the UI"
#     sleep 1
# done
