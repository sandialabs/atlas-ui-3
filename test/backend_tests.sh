#!/bin/bash
set -e

echo "Running Backend Tests..."
echo "================================="

# Use PROJECT_ROOT if set by master script, otherwise detect
if [ -z "$PROJECT_ROOT" ]; then
    if [ -d "/app" ]; then
        PROJECT_ROOT="/app"
    else
        PROJECT_ROOT="$(pwd)/.."
    fi
fi

# Set up Python environment and paths
BACKEND_DIR="$PROJECT_ROOT/backend"
export PYTHONPATH="$BACKEND_DIR"

echo "Backend directory: $BACKEND_DIR"
echo "PYTHONPATH: $PYTHONPATH"

# Change to backend directory
cd "$BACKEND_DIR"

echo ""
echo "\n🧪 Running Backend Tests..."
echo "=============================="

# If legacy targeted tests exist, run them; otherwise run all tests in backend/tests
if [ -f tests/test_config_module.py ] || [ -f tests/test_file_storage_module.py ] || [ -f tests/test_llm_module.py ]; then
    echo "Detected legacy targeted tests; running individually"
    [ -f tests/test_config_module.py ] && timeout 60 python -m pytest tests/test_config_module.py -v --tb=short || true
    [ -f tests/test_file_storage_module.py ] && timeout 60 python -m pytest tests/test_file_storage_module.py -v --tb=short || true
    [ -f tests/test_llm_module.py ] && timeout 60 python -m pytest tests/test_llm_module.py -v --tb=short || true
fi

echo "Running pytest on backend/tests directory"
timeout 300 python -m pytest tests -v --tb=short

echo "\n✅ Backend tests completed"