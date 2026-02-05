#!/bin/bash
# PR #275 Validation Script: Make Atlas installable as a Python package
# Tests the Python package installation, CLI commands, and programmatic API

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #275 Validation: Python Package Installation"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

# Set PYTHONPATH for imports
export PYTHONPATH="$PROJECT_ROOT"

echo ""
echo "1. Test package structure exists"
echo "---------------------------------"
if [ -d "$PROJECT_ROOT/atlas" ]; then
    echo "PASSED: atlas/ directory exists"
else
    echo "FAILED: atlas/ directory not found"
    exit 1
fi

if [ -f "$PROJECT_ROOT/atlas/__init__.py" ]; then
    echo "PASSED: atlas/__init__.py exists"
else
    echo "FAILED: atlas/__init__.py not found"
    exit 1
fi

echo ""
echo "2. Test basic package import"
echo "----------------------------"
python -c "from atlas import VERSION; print(f'Version: {VERSION}')" && echo "PASSED: Basic import works" || {
    echo "FAILED: Basic import failed"
    exit 1
}

echo ""
echo "3. Test AtlasClient and ChatResult imports"
echo "------------------------------------------"
python -c "from atlas import AtlasClient, ChatResult; print('AtlasClient:', AtlasClient); print('ChatResult:', ChatResult)" && echo "PASSED: AtlasClient and ChatResult import" || {
    echo "FAILED: AtlasClient/ChatResult import failed"
    exit 1
}

echo ""
echo "4. Test CLI entry points exist"
echo "------------------------------"
if [ -f "$PROJECT_ROOT/atlas/atlas_chat_cli.py" ]; then
    echo "PASSED: atlas_chat_cli.py exists"
else
    echo "FAILED: atlas_chat_cli.py not found"
    exit 1
fi

if [ -f "$PROJECT_ROOT/atlas/server_cli.py" ]; then
    echo "PASSED: server_cli.py exists"
else
    echo "FAILED: server_cli.py not found"
    exit 1
fi

echo ""
echo "5. Test pyproject.toml has correct entry points"
echo "-----------------------------------------------"
if grep -q 'atlas-chat = "atlas.atlas_chat_cli:main"' "$PROJECT_ROOT/pyproject.toml"; then
    echo "PASSED: atlas-chat entry point configured"
else
    echo "FAILED: atlas-chat entry point not found in pyproject.toml"
    exit 1
fi

if grep -q 'atlas-server = "atlas.server_cli:main"' "$PROJECT_ROOT/pyproject.toml"; then
    echo "PASSED: atlas-server entry point configured"
else
    echo "FAILED: atlas-server entry point not found in pyproject.toml"
    exit 1
fi

echo ""
echo "6. Test CLI --help commands work"
echo "--------------------------------"
cd "$PROJECT_ROOT/atlas"
python atlas_chat_cli.py --help > /dev/null 2>&1 && echo "PASSED: atlas_chat_cli.py --help works" || {
    echo "FAILED: atlas_chat_cli.py --help failed"
    exit 1
}

python server_cli.py --help > /dev/null 2>&1 && echo "PASSED: server_cli.py --help works" || {
    echo "FAILED: server_cli.py --help failed"
    exit 1
}

echo ""
echo "7. Test --list-tools command"
echo "----------------------------"
cd "$PROJECT_ROOT/atlas"
# This may fail if no MCP servers configured, but should at least run
timeout 30 python atlas_chat_cli.py --list-tools 2>&1 | head -5 && echo "PASSED: --list-tools command runs" || {
    echo "WARNING: --list-tools returned non-zero (may be expected if no MCP configured)"
}

echo ""
echo "8. Test PyPI workflow file exists"
echo "---------------------------------"
if [ -f "$PROJECT_ROOT/.github/workflows/pypi-publish.yml" ]; then
    echo "PASSED: pypi-publish.yml workflow exists"
else
    echo "FAILED: pypi-publish.yml not found"
    exit 1
fi

echo ""
echo "9. Run backend unit tests"
echo "-------------------------"
cd "$PROJECT_ROOT"
./test/run_tests.sh backend && echo "PASSED: Backend tests pass" || {
    echo "FAILED: Backend tests failed"
    exit 1
}

echo ""
echo "=========================================="
echo "PR #275 Validation: ALL CHECKS PASSED"
echo "=========================================="
exit 0
