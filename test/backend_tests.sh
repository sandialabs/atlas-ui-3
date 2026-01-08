#!/bin/bash
set -e

echo "Running Backend Tests..."
echo "================================="

# Use PROJECT_ROOT if set by master script, otherwise detect
if [ -z "$PROJECT_ROOT" ]; then
    if [ -d "/app" ]; then
        PROJECT_ROOT="/app"
    elif [ -d "../backend" ]; then
        # Running from test/ directory
        PROJECT_ROOT="$(pwd)/.."
    else
        # Running from project root
        PROJECT_ROOT="$(pwd)"
    fi
fi

# Set up Python environment and paths
BACKEND_DIR="$PROJECT_ROOT/backend"
export PYTHONPATH="$PROJECT_ROOT"

echo "Backend directory: $BACKEND_DIR"
echo "PYTHONPATH: $PYTHONPATH"

# Activate project virtual environment if available (per CLAUDE.md: use uv-managed venv)
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    echo "Activating virtual environment at $PROJECT_ROOT/.venv"
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "WARNING: .venv not found. Backend tests expect uv-managed venv with pytest installed."
    echo "If tests fail due to missing packages, run: uv venv && source .venv/bin/activate && uv pip install -r requirements.txt"
fi

# Change to backend directory
cd "$BACKEND_DIR"

echo ""
echo "\nRunning Backend Tests..."
echo "BACKEND_DIR full path: $(pwd)"
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

echo "\nBackend tests completed"
