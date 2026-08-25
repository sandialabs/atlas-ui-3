#!/bin/bash
set -uo pipefail
# Test script for PR #840: pluggable session repository + disconnect
# persistence test (issue #760)
#
# Test plan:
# - create_session_repository("memory") returns InMemorySessionRepository
# - create_session_repository() with no arg defaults to memory
# - Unknown type raises ValueError mentioning the extension point
# - AppSettings.session_repository_type defaults to "memory"
# - End-to-end disconnect persistence test passes (the turn is saved)
# - Backend unit tests pass

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# -------------------------------------------------------------------
print_header "Test 1: factory returns InMemorySessionRepository for 'memory'"
# -------------------------------------------------------------------
python3 -c "
from atlas.infrastructure.sessions.factory import create_session_repository
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
repo = create_session_repository('memory')
assert isinstance(repo, InMemorySessionRepository), 'expected InMemorySessionRepository'
print('OK')
"
print_result $? "factory('memory') returns InMemorySessionRepository"

# -------------------------------------------------------------------
print_header "Test 2: factory defaults to 'memory' when no arg given"
# -------------------------------------------------------------------
python3 -c "
from atlas.infrastructure.sessions.factory import create_session_repository
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
repo = create_session_repository()
assert isinstance(repo, InMemorySessionRepository), 'expected InMemorySessionRepository'
print('OK')
"
print_result $? "factory() defaults to InMemorySessionRepository"

# -------------------------------------------------------------------
print_header "Test 3: unknown type raises ValueError mentioning extension point"
# -------------------------------------------------------------------
python3 -c "
import pytest
from atlas.infrastructure.sessions.factory import create_session_repository
try:
    create_session_repository('redis')
    raise SystemExit('should have raised')
except ValueError as e:
    assert 'create_session_repository' in str(e), 'error must mention extension point'
    assert 'Unknown SESSION_REPOSITORY_TYPE' in str(e), 'error must mention the setting'
    print('OK')
"
print_result $? "unknown type raises ValueError with helpful message"

# -------------------------------------------------------------------
print_header "Test 4: AppSettings.session_repository_type defaults to 'memory'"
# -------------------------------------------------------------------
python3 -c "
from atlas.modules.config.settings import AppSettings
s = AppSettings()
assert s.session_repository_type == 'memory', f'expected memory, got {s.session_repository_type}'
print('OK')
"
print_result $? "AppSettings defaults session_repository_type to 'memory'"

# -------------------------------------------------------------------
print_header "Test 5: disconnect persists in-flight turn (end-to-end)"
# -------------------------------------------------------------------
python3 -m pytest "$PROJECT_ROOT/atlas/tests/test_websocket_disconnect_resilience.py::test_disconnect_persists_in_flight_turn" -xqs
print_result $? "disconnect mid-turn persists the turn (issue #760)"

# -------------------------------------------------------------------
print_header "Test 6: session repository factory unit tests"
# -------------------------------------------------------------------
python3 -m pytest "$PROJECT_ROOT/atlas/tests/test_session_repository_factory.py" -xqs
print_result $? "session repository factory unit tests pass"

# -------------------------------------------------------------------
print_header "Test 7: docker-compose env sync test"
# -------------------------------------------------------------------
python3 -m pytest "$PROJECT_ROOT/atlas/tests/test_docker_env_sync.py::test_docker_compose_has_required_env_vars" -xqs
print_result $? "docker-compose.yml includes SESSION_REPOSITORY_TYPE"

# -------------------------------------------------------------------
print_header "Test 8: backend unit tests"
# -------------------------------------------------------------------
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend
print_result $? "backend unit tests pass"

# -------------------------------------------------------------------
echo ""
echo "=========================================="
echo "Summary: $PASSED passed, $FAILED failed"
echo "=========================================="
exit $FAILED