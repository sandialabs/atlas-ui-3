#!/bin/bash
# Test script for PR #510: Prevent auth from failing open to hardcoded admin
#
# Test plan:
# - Verify get_current_user() raises HTTP 401 when user_email is not set
# - Verify is_user_in_group() denies admin in production mode (debug_mode=False)
# - Verify is_user_in_group() grants admin in debug mode (debug_mode=True)
# - Verify all users still get 'users' group regardless of mode
# - Backend regression suite passes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr510"

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

print_header "PR #510: Auth Fail-Open Prevention Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. get_current_user raises 401 when user_email is missing
# ==========================================
print_header "1. get_current_user raises 401 on missing user_email"

RESULT=$(python3 -c "
import asyncio
from unittest.mock import MagicMock
from fastapi import HTTPException
from atlas.core.log_sanitizer import get_current_user

async def test():
    request = MagicMock()
    # Simulate no user_email set on request state
    del request.state.user_email
    request.state.__dict__ = {}
    try:
        await get_current_user(request)
        return 'NO_EXCEPTION'
    except HTTPException as e:
        return f'HTTP_{e.status_code}'
    except Exception as e:
        return f'WRONG_EXCEPTION:{type(e).__name__}'

print(asyncio.run(test()))
" 2>&1)

[ "$RESULT" = "HTTP_401" ]
print_result $? "get_current_user raises HTTP 401 when user_email is unset (got: $RESULT)"

# ==========================================
# 2. get_current_user returns email when set
# ==========================================
print_header "2. get_current_user returns email when present"

RESULT=$(python3 -c "
import asyncio
from unittest.mock import MagicMock
from atlas.core.log_sanitizer import get_current_user

async def test():
    request = MagicMock()
    request.state.user_email = 'real@user.com'
    return await get_current_user(request)

print(asyncio.run(test()))
" 2>&1)

[ "$RESULT" = "real@user.com" ]
print_result $? "get_current_user returns email when user_email is set (got: $RESULT)"

# ==========================================
# 3. is_user_in_group denies admin in production mode
# ==========================================
print_header "3. is_user_in_group denies admin in production mode"

# Load production fixture
set -a
source "$FIXTURES_DIR/.env"
set +a

RESULT=$(python3 -c "
import asyncio
import os
os.environ['DEBUG_MODE'] = 'false'

from atlas.modules.config.config_manager import config_manager
config_manager._app_settings = None  # Force reload
from atlas.core.auth import is_user_in_group

async def test():
    # test@test.com should NOT get admin in production
    return await is_user_in_group('test@test.com', 'admin')

result = asyncio.run(test())
print(result)
" 2>&1)

[ "$RESULT" = "False" ]
print_result $? "test@test.com denied admin in production mode (got: $RESULT)"

# ==========================================
# 4. is_user_in_group grants admin in debug mode
# ==========================================
print_header "4. is_user_in_group grants admin in debug mode"

# Load debug fixture
set -a
source "$FIXTURES_DIR/.env.debug"
set +a

RESULT=$(python3 -c "
import asyncio
import os
os.environ['DEBUG_MODE'] = 'true'

from atlas.modules.config.config_manager import config_manager
config_manager._app_settings = None  # Force reload
from atlas.core.auth import is_user_in_group

async def test():
    # test@test.com should get admin in debug mode
    return await is_user_in_group('test@test.com', 'admin')

result = asyncio.run(test())
print(result)
" 2>&1)

[ "$RESULT" = "True" ]
print_result $? "test@test.com granted admin in debug mode (got: $RESULT)"

# ==========================================
# 5. All users get 'users' group regardless of mode
# ==========================================
print_header "5. Users group available in all modes"

RESULT=$(python3 -c "
import asyncio
import os
os.environ['DEBUG_MODE'] = 'false'

from atlas.modules.config.config_manager import config_manager
config_manager._app_settings = None  # Force reload
from atlas.core.auth import is_user_in_group

async def test():
    return await is_user_in_group('anyone@example.com', 'users')

result = asyncio.run(test())
print(result)
" 2>&1)

[ "$RESULT" = "True" ]
print_result $? "Any user gets 'users' group in production mode (got: $RESULT)"

# ==========================================
# 6. Targeted test suite
# ==========================================
print_header "6. Targeted test suite"

cd "$ATLAS_DIR"
python3 -m pytest tests/test_core_utils.py -x -q 2>&1
print_result $? "test_core_utils.py passes"

python3 -m pytest tests/test_core_auth.py -x -q 2>&1
print_result $? "test_core_auth.py passes"

python3 -m pytest tests/test_middleware_auth.py -x -q 2>&1
print_result $? "test_middleware_auth.py passes"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
