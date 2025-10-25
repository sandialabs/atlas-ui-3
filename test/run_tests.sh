#!/bin/bash
set -e

echo "Starting Test Suite"
echo "===================="
echo "Container: $(hostname)"
echo "Date: $(date)"
echo "Working Directory: $(pwd)"
echo ""

# Default to running all tests unless specific test type is specified
TEST_TYPE=${1:-all}

# Detect environment and set appropriate base paths
if [ -d "/app" ] && [ -f "/app/test/backend_tests.sh" ]; then
    # Running in CI/CD container environment
    ENVIRONMENT="cicd"
    TEST_BASE_PATH="/app/test"
    PROJECT_ROOT="/app"
    echo "Environment: CI/CD Container"
elif [ -f "test/backend_tests.sh" ]; then
    # Running locally from project root
    ENVIRONMENT="local"
    TEST_BASE_PATH="$(pwd)/test"
    PROJECT_ROOT="$(pwd)"
    echo "Environment: Local (project root)"
elif [ -f "../test/backend_tests.sh" ]; then
    # Running locally from subdirectory
    ENVIRONMENT="local"
    TEST_BASE_PATH="$(pwd)/../test"
    PROJECT_ROOT="$(pwd)/.."
    echo "Environment: Local (subdirectory)"
else
    echo "ERROR: Cannot detect environment or find test scripts"
    echo "Expected to find test scripts in one of:"
    echo "  - /app/test/ (CI/CD container)"
    echo "  - ./test/ (local from project root)"
    echo "  - ../test/ (local from subdirectory)"
    exit 1
fi

echo "Test Base Path: $TEST_BASE_PATH"
echo "Project Root: $PROJECT_ROOT"
echo ""

# Function to run a specific test script
run_test() {
    local test_name=$1
    local script_path="${TEST_BASE_PATH}/${test_name}_tests.sh"
    
    echo "--- Running $test_name tests ---"
    if [ -f "$script_path" ]; then
        chmod +x "$script_path"
        
        # Set environment variables for the test scripts
        export PROJECT_ROOT="$PROJECT_ROOT"
        export ENVIRONMENT="$ENVIRONMENT"
        
        # Use test-specific MCP config to speed up tests
        export MCP_CONFIG_FILE="mcp-test.json"
        
        bash "$script_path"
        echo "$test_name tests: PASSED"
    else
        echo "ERROR: Test script not found: $script_path"
        exit 1
    fi
    echo ""
}

# Main test execution
case $TEST_TYPE in
    "backend")
        echo "Running Backend Tests Only"
        run_test "backend"
        ;;
    "frontend") 
        echo "Running Frontend Tests Only"
        run_test "frontend"
        ;;
    "e2e")
        echo "Running E2E Tests Only"
        run_test "e2e"
        ;;
    "all")
        echo "Running All Test Suites"
        run_test "backend"
        run_test "frontend"
        run_test "e2e"
        ;;
    *)
        echo "ERROR: Unknown test type: $TEST_TYPE"
        echo "Usage: $0 [backend|frontend|e2e|all]"
        exit 1
        ;;
esac

echo "===================="
echo "All Tests Completed Successfully!"
echo "Date: $(date)"