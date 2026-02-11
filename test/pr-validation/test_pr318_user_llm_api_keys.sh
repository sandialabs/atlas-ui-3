#!/bin/bash
# Test script for PR #318: Per-User LLM API Keys
#
# Test plan:
# - Verify ModelConfig accepts api_key_source field
# - Verify LLM auth routes respond correctly
# - Verify /api/config exposes api_key_source and user_has_key for user-key models
# - Verify token storage uses llm: prefix for LLM keys
# - Verify _resolve_user_api_key raises when no user/token
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Setup
cd "$PROJECT_ROOT" || exit 1
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi
export PYTHONPATH="$PROJECT_ROOT"

print_header "PR #318: Per-User LLM API Keys Validation"

# -------------------------------------------------------
# Test 1: ModelConfig accepts api_key_source field
# -------------------------------------------------------
print_header "Test 1: ModelConfig api_key_source field"
python -c "
from atlas.modules.config.config_manager import ModelConfig
m = ModelConfig(model_name='test', model_url='http://x', api_key='', api_key_source='user')
assert m.api_key_source == 'user', f'Expected user, got {m.api_key_source}'
m2 = ModelConfig(model_name='test2', model_url='http://x', api_key='\${KEY}')
assert m2.api_key_source == 'system', f'Expected system, got {m2.api_key_source}'
print('ModelConfig api_key_source: OK')
" 2>&1
print_result $? "ModelConfig accepts api_key_source field"

# -------------------------------------------------------
# Test 2: LLM auth routes module imports successfully
# -------------------------------------------------------
print_header "Test 2: LLM auth routes import"
python -c "
from atlas.routes.llm_auth_routes import router, LLMTokenUpload
assert router.prefix == '/api/llm/auth'
t = LLMTokenUpload(token='sk-test')
assert t.token == 'sk-test'
assert t.expires_at is None
print('LLM auth routes import: OK')
" 2>&1
print_result $? "LLM auth routes import and model validation"

# -------------------------------------------------------
# Test 3: _resolve_user_api_key raises without user_email
# -------------------------------------------------------
print_header "Test 3: _resolve_user_api_key validation"
python -c "
from atlas.modules.llm.litellm_caller import LiteLLMCaller
try:
    LiteLLMCaller._resolve_user_api_key('test-model', None)
    print('ERROR: Should have raised ValueError')
    exit(1)
except ValueError as e:
    assert 'no user_email' in str(e), f'Unexpected error: {e}'
    print('_resolve_user_api_key raises correctly: OK')
" 2>&1
print_result $? "_resolve_user_api_key raises ValueError without user_email"

# -------------------------------------------------------
# Test 4: LLM protocol includes user_email parameter
# -------------------------------------------------------
print_header "Test 4: LLMProtocol signature check"
python -c "
import inspect
from atlas.interfaces.llm import LLMProtocol
sig_plain = inspect.signature(LLMProtocol.call_plain)
sig_tools = inspect.signature(LLMProtocol.call_with_tools)
assert 'user_email' in sig_plain.parameters, 'call_plain missing user_email param'
assert 'user_email' in sig_tools.parameters, 'call_with_tools missing user_email param'
print('LLMProtocol signatures: OK')
" 2>&1
print_result $? "LLMProtocol call_plain and call_with_tools have user_email param"

# -------------------------------------------------------
# Test 5: config_routes includes api_key_source in model info
# -------------------------------------------------------
print_header "Test 5: config_routes api_key_source exposure"
python -c "
import inspect
from atlas.routes import config_routes
src = inspect.getsource(config_routes.get_config)
assert 'api_key_source' in src, 'config_routes should expose api_key_source'
assert 'user_has_key' in src, 'config_routes should expose user_has_key'
print('config_routes exposes api_key_source and user_has_key: OK')
" 2>&1
print_result $? "config_routes exposes api_key_source and user_has_key"

# -------------------------------------------------------
# Test 6: Frontend hook file exists
# -------------------------------------------------------
print_header "Test 6: Frontend useLLMAuthStatus hook exists"
if [ -f "$PROJECT_ROOT/frontend/src/hooks/useLLMAuthStatus.js" ]; then
    echo "useLLMAuthStatus.js exists"
    # Check it exports the hook
    grep -q "export function useLLMAuthStatus" "$PROJECT_ROOT/frontend/src/hooks/useLLMAuthStatus.js"
    print_result $? "useLLMAuthStatus hook is exported"
else
    echo "useLLMAuthStatus.js NOT found"
    print_result 1 "useLLMAuthStatus hook file exists"
fi

# -------------------------------------------------------
# Test 7: Header.jsx imports useLLMAuthStatus
# -------------------------------------------------------
print_header "Test 7: Header.jsx LLM auth integration"
grep -q "useLLMAuthStatus" "$PROJECT_ROOT/frontend/src/components/Header.jsx"
print_result $? "Header.jsx imports useLLMAuthStatus"
grep -q "TokenInputModal" "$PROJECT_ROOT/frontend/src/components/Header.jsx"
print_result $? "Header.jsx uses TokenInputModal for LLM keys"
grep -q "Key" "$PROJECT_ROOT/frontend/src/components/Header.jsx"
print_result $? "Header.jsx imports Key icon from lucide-react"

# -------------------------------------------------------
# Test 8: main.py registers LLM auth router
# -------------------------------------------------------
print_header "Test 8: main.py router registration"
grep -q "llm_auth_router" "$PROJECT_ROOT/atlas/main.py"
print_result $? "main.py registers llm_auth_router"

# -------------------------------------------------------
# Test 9: Run new unit tests
# -------------------------------------------------------
print_header "Test 9: Run per-user LLM API key unit tests"
cd "$PROJECT_ROOT"
python -m pytest atlas/tests/test_llm_auth_routes.py atlas/tests/test_llm_user_api_key.py -v --tb=short 2>&1
print_result $? "Per-user LLM API key unit tests pass"

# -------------------------------------------------------
# Test 10: Run full backend test suite
# -------------------------------------------------------
print_header "Test 10: Full backend test suite"
cd "$PROJECT_ROOT"
bash test/run_tests.sh backend 2>&1
print_result $? "Full backend test suite passes"

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
print_header "Summary"
TOTAL=$((PASSED + FAILED))
echo -e "Total: ${TOTAL} | ${GREEN}Passed: ${PASSED}${NC} | ${RED}Failed: ${FAILED}${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}PR #318 validation FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}PR #318 validation PASSED${NC}"
    exit 0
fi
