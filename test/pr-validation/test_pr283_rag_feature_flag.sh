#!/bin/bash
# Test script for PR #283: RAG feature flag should fully disable RAG on the backend
#
# Test plan:
# - Verify that when FEATURE_RAG_ENABLED=false (default), RAG services are None in AppFactory
# - Verify that when FEATURE_RAG_ENABLED=false, rag_sources_config returns empty config
# - Verify that when FEATURE_RAG_ENABLED=false, rag_mcp_config returns empty config
# - Verify that when FEATURE_RAG_ENABLED=true, RAG services are initialized
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

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

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

cd "$BACKEND_DIR" || exit 1

print_header "Test 1: FEATURE_RAG_ENABLED=false - RAG services are None"

FEATURE_RAG_ENABLED=false python -c "
import sys, types
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

import os
os.environ['FEATURE_RAG_ENABLED'] = 'false'

from modules.config.config_manager import ConfigManager
cm = ConfigManager()
assert cm.app_settings.feature_rag_enabled == False, 'Expected feature_rag_enabled=False'

# Verify rag_sources_config returns empty without loading files
rsc = cm.rag_sources_config
assert len(rsc.sources) == 0, f'Expected 0 RAG sources, got {len(rsc.sources)}'

# Verify rag_mcp_config returns empty without loading files
rmc = cm.rag_mcp_config
assert len(rmc.servers) == 0, f'Expected 0 RAG MCP servers, got {len(rmc.servers)}'

print('RAG configs are empty when feature disabled')
"
print_result $? "RAG configs return empty when FEATURE_RAG_ENABLED=false"

print_header "Test 2: FEATURE_RAG_ENABLED=false - AppFactory RAG services are None"

FEATURE_RAG_ENABLED=false python -c "
import sys, types, os
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

os.environ['FEATURE_RAG_ENABLED'] = 'false'

from infrastructure.app_factory import AppFactory
factory = AppFactory()
assert factory.get_unified_rag_service() is None, 'Expected unified_rag_service to be None'
assert factory.get_rag_mcp_service() is None, 'Expected rag_mcp_service to be None'
print('RAG services are None when feature disabled')
"
print_result $? "AppFactory RAG services are None when FEATURE_RAG_ENABLED=false"

print_header "Test 3: FEATURE_RAG_ENABLED=true - RAG services are initialized"

FEATURE_RAG_ENABLED=true python -c "
import sys, types, os
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

os.environ['FEATURE_RAG_ENABLED'] = 'true'

from infrastructure.app_factory import AppFactory
factory = AppFactory()
assert factory.get_unified_rag_service() is not None, 'Expected unified_rag_service to be initialized'
assert factory.get_rag_mcp_service() is not None, 'Expected rag_mcp_service to be initialized'
print('RAG services are initialized when feature enabled')
"
print_result $? "AppFactory RAG services initialized when FEATURE_RAG_ENABLED=true"

print_header "Test 4: Run backend unit tests"
cd "$PROJECT_ROOT" && ./test/run_tests.sh backend
print_result $? "Backend unit tests"

# Summary
print_header "Summary"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
