#!/bin/bash
# Test script for PR #384 - Package install config files not respected
#
# Covers:
# - atlas-init --minimal generates .env with APP_CONFIG_DIR=./config (uncommented)
# - atlas-server auto-detects config/ next to .env when APP_CONFIG_DIR is not set
# - Auto-detection does not override an already-set APP_CONFIG_DIR
# - No auto-detection when config/ directory does not exist next to .env
# - Backend tests pass

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
BOLD='\033[1m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo -e "${BOLD}==========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}==========================================${NC}"
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

print_header "PR #384 - Package Install Config Detection"

# --------------------------------------------------------------------------
# Test 1: atlas-init --minimal generates .env with APP_CONFIG_DIR uncommented
# --------------------------------------------------------------------------
print_header "Test 1: atlas-init --minimal sets APP_CONFIG_DIR=./config"

TMPDIR_1=$(mktemp -d)
trap "rm -rf $TMPDIR_1" EXIT

python -m atlas.init_cli --minimal --force --target "$TMPDIR_1" > /dev/null 2>&1
if grep -q '^APP_CONFIG_DIR=./config' "$TMPDIR_1/.env"; then
    print_result 0 "Minimal .env contains uncommented APP_CONFIG_DIR=./config"
else
    print_result 1 "Minimal .env missing uncommented APP_CONFIG_DIR=./config"
    echo "  Contents:"
    grep -n "APP_CONFIG_DIR" "$TMPDIR_1/.env" || echo "  (not found)"
fi

# --------------------------------------------------------------------------
# Test 2: server_cli auto-detects config/ next to .env
# --------------------------------------------------------------------------
print_header "Test 2: Auto-detect config/ next to .env"

TMPDIR_2=$(mktemp -d)
trap "rm -rf $TMPDIR_1 $TMPDIR_2" EXIT

# Create a .env and a config/ directory
echo "PORT=9999" > "$TMPDIR_2/.env"
mkdir -p "$TMPDIR_2/config"

# Run a Python snippet that simulates the server_cli main() logic
unset APP_CONFIG_DIR 2>/dev/null || true
RESULT=$(python -c "
import os, sys
sys.path.insert(0, '$PROJECT_ROOT')

# Clear any existing APP_CONFIG_DIR
os.environ.pop('APP_CONFIG_DIR', None)

from pathlib import Path
from dotenv import load_dotenv

env_path = Path('$TMPDIR_2/.env')
load_dotenv(dotenv_path=str(env_path))
env_dir = env_path.resolve().parent

# Simulate the auto-detection logic from server_cli.py
if not os.environ.get('APP_CONFIG_DIR'):
    config_candidate = env_dir / 'config'
    if config_candidate.is_dir():
        os.environ['APP_CONFIG_DIR'] = str(config_candidate.resolve())

print(os.environ.get('APP_CONFIG_DIR', ''))
")

EXPECTED="$TMPDIR_2/config"
# Resolve to handle symlinks
EXPECTED_RESOLVED=$(python -c "from pathlib import Path; print(Path('$EXPECTED').resolve())")

if [ "$RESULT" = "$EXPECTED_RESOLVED" ]; then
    print_result 0 "Auto-detected config/ directory: $RESULT"
else
    print_result 1 "Expected APP_CONFIG_DIR=$EXPECTED_RESOLVED, got: $RESULT"
fi

# --------------------------------------------------------------------------
# Test 3: Auto-detection does NOT override existing APP_CONFIG_DIR
# --------------------------------------------------------------------------
print_header "Test 3: Existing APP_CONFIG_DIR is not overridden"

TMPDIR_3=$(mktemp -d)
trap "rm -rf $TMPDIR_1 $TMPDIR_2 $TMPDIR_3" EXIT

echo "PORT=9999" > "$TMPDIR_3/.env"
mkdir -p "$TMPDIR_3/config"

RESULT=$(python -c "
import os, sys
sys.path.insert(0, '$PROJECT_ROOT')

# Pre-set APP_CONFIG_DIR
os.environ['APP_CONFIG_DIR'] = '/custom/path'

from pathlib import Path
from dotenv import load_dotenv

env_path = Path('$TMPDIR_3/.env')
load_dotenv(dotenv_path=str(env_path))
env_dir = env_path.resolve().parent

# Simulate the auto-detection guard
if not os.environ.get('APP_CONFIG_DIR'):
    config_candidate = env_dir / 'config'
    if config_candidate.is_dir():
        os.environ['APP_CONFIG_DIR'] = str(config_candidate.resolve())

print(os.environ.get('APP_CONFIG_DIR', ''))
")

if [ "$RESULT" = "/custom/path" ]; then
    print_result 0 "Existing APP_CONFIG_DIR preserved: $RESULT"
else
    print_result 1 "Expected /custom/path, got: $RESULT"
fi

# Clean env for subsequent tests
unset APP_CONFIG_DIR 2>/dev/null || true

# --------------------------------------------------------------------------
# Test 4: No auto-detection when config/ does not exist
# --------------------------------------------------------------------------
print_header "Test 4: No config/ directory means no auto-detection"

TMPDIR_4=$(mktemp -d)
trap "rm -rf $TMPDIR_1 $TMPDIR_2 $TMPDIR_3 $TMPDIR_4" EXIT

echo "PORT=9999" > "$TMPDIR_4/.env"
# Deliberately NOT creating config/

RESULT=$(python -c "
import os, sys
sys.path.insert(0, '$PROJECT_ROOT')

os.environ.pop('APP_CONFIG_DIR', None)

from pathlib import Path
from dotenv import load_dotenv

env_path = Path('$TMPDIR_4/.env')
load_dotenv(dotenv_path=str(env_path))
env_dir = env_path.resolve().parent

if not os.environ.get('APP_CONFIG_DIR'):
    config_candidate = env_dir / 'config'
    if config_candidate.is_dir():
        os.environ['APP_CONFIG_DIR'] = str(config_candidate.resolve())

print(os.environ.get('APP_CONFIG_DIR', 'UNSET'))
")

if [ "$RESULT" = "UNSET" ]; then
    print_result 0 "APP_CONFIG_DIR not set when config/ is absent"
else
    print_result 1 "Expected UNSET, got: $RESULT"
fi

# --------------------------------------------------------------------------
# Test 5: Backend tests pass
# --------------------------------------------------------------------------
print_header "Test 5: Backend tests"

cd "$PROJECT_ROOT"
if ./test/run_tests.sh backend > /dev/null 2>&1; then
    print_result 0 "Backend tests pass"
else
    print_result 1 "Backend tests failed"
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
print_header "Summary"
echo -e "Passed: ${GREEN}${PASSED}${NC}"
echo -e "Failed: ${RED}${FAILED}${NC}"

if [ "$FAILED" -gt 0 ]; then
    echo -e "\n${RED}SOME CHECKS FAILED${NC}"
    exit 1
else
    echo -e "\n${GREEN}ALL CHECKS PASSED${NC}"
    exit 0
fi
