#!/bin/bash
set -e

echo "Running Atlas (Python) Tests..."
echo "================================="

# Use PROJECT_ROOT if set by master script, otherwise detect
if [ -z "$PROJECT_ROOT" ]; then
    if [ -d "/app" ]; then
        PROJECT_ROOT="/app"
    elif [ -d "../atlas" ]; then
        # Running from test/ directory
        PROJECT_ROOT="$(pwd)/.."
    else
        # Running from project root
        PROJECT_ROOT="$(pwd)"
    fi
fi

# Set up Python environment and paths
ATLAS_DIR="$PROJECT_ROOT/atlas"

echo "Atlas directory: $ATLAS_DIR"

# Activate project virtual environment if available (per CLAUDE.md: use uv-managed venv)
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    echo "Activating virtual environment at $PROJECT_ROOT/.venv"
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "WARNING: .venv not found. Atlas tests expect uv-managed venv with pytest installed."
    echo "If tests fail due to missing packages, run: uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'"
fi

# Change to atlas directory
cd "$ATLAS_DIR"

echo ""
echo "Running Atlas Tests..."
echo "ATLAS_DIR full path: $(pwd)"
echo "=============================="

# If legacy targeted tests exist, run them; otherwise run all tests in atlas/tests
if [ -f tests/test_config_module.py ] || [ -f tests/test_file_storage_module.py ] || [ -f tests/test_llm_module.py ]; then
    echo "Detected legacy targeted tests; running individually"
    [ -f tests/test_config_module.py ] && timeout 60 python -m pytest tests/test_config_module.py -v --tb=short || true
    [ -f tests/test_file_storage_module.py ] && timeout 60 python -m pytest tests/test_file_storage_module.py -v --tb=short || true
    [ -f tests/test_llm_module.py ] && timeout 60 python -m pytest tests/test_llm_module.py -v --tb=short || true
fi

echo "Running pytest on atlas/tests directory"
timeout --foreground 300 python -u -m pytest tests -v --tb=short

echo "Atlas tests completed"
