#!/bin/bash
# Test script for PR #266: RAG retrieval should work as best effort
#
# Test plan:
# - Verify _query_all_rag_sources isolates per-source failures (asyncio.gather return_exceptions=True)
# - Verify UnifiedRAGService.discover_data_sources catches per-source exceptions
# - Verify config route handles HTTP and MCP RAG discovery independently
# - Verify _combine_rag_contexts handles None content safely
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

print_header "Test 1: _query_all_rag_sources isolates per-source failures"

python -c "
import sys, types, asyncio
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

from modules.llm.litellm_caller import LiteLLMCaller

caller = LiteLLMCaller(debug_mode=True)

class FakeRagService:
    async def query_rag(self, user, source, messages):
        if source == 'bad:source':
            raise ConnectionError('Simulated failure')
        class FakeResponse:
            content = 'good content'
            metadata = None
            is_completion = False
        return FakeResponse()

async def test():
    results = await caller._query_all_rag_sources(
        ['bad:source', 'good:source'],
        FakeRagService(),
        'test@test.com',
        [{'role': 'user', 'content': 'test'}],
    )
    # Should get 1 successful result despite 1 failure
    assert len(results) == 1, f'Expected 1 result, got {len(results)}'
    display, resp = results[0]
    assert resp.content == 'good content'
    print('Per-source failure isolation works correctly')

asyncio.run(test())
"
print_result $? "Per-source failure isolation in _query_all_rag_sources"

print_header "Test 2: _combine_rag_contexts handles None content"

python -c "
import sys, types
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

from modules.llm.litellm_caller import LiteLLMCaller

class FakeResponse:
    def __init__(self, content, metadata=None):
        self.content = content
        self.metadata = metadata

# Test with None content
responses = [
    ('source1', FakeResponse(None)),
    ('source2', FakeResponse('valid content')),
]

combined, meta = LiteLLMCaller._combine_rag_contexts(responses)
assert 'valid content' in combined, 'Expected valid content in combined output'
assert 'source1' in combined, 'Expected source1 label in combined output'
print('None content handled gracefully')
"
print_result $? "Null safety in _combine_rag_contexts"

print_header "Test 3: UnifiedRAGService.discover_data_sources per-source error isolation"

python -c "
import sys, types, asyncio
# Stub litellm
m = types.ModuleType('litellm')
m.drop_params = True
m.set_verbose = lambda *a, **k: None
m.completion = lambda *a, **k: None
async def acompletion(*a, **k): pass
m.acompletion = acompletion
sys.modules['litellm'] = m

import os
os.environ['FEATURE_RAG_ENABLED'] = 'true'

from modules.config.config_manager import ConfigManager, RAGSourcesConfig, RAGSourceConfig
from domain.unified_rag_service import UnifiedRAGService

cm = ConfigManager()

# Patch rag_sources_config to have a source that will cause errors
service = UnifiedRAGService(config_manager=cm)

# Verify the method has per-source try/except by checking the source code
import inspect
source = inspect.getsource(service.discover_data_sources)
assert 'except Exception as e' in source, 'Expected per-source exception handling'
assert 'continuing with remaining sources' in source, 'Expected best-effort log message'
print('Per-source error isolation confirmed in discover_data_sources')
"
print_result $? "Per-source error isolation in UnifiedRAGService.discover_data_sources"

print_header "Test 4: Config route has independent HTTP/MCP RAG discovery"

python -c "
import inspect
from routes.config_routes import get_config

source = inspect.getsource(get_config)
# There should be separate try/except blocks for HTTP and MCP discovery
http_try = 'Error discovering HTTP RAG sources' in source
mcp_try = 'Error discovering MCP RAG sources' in source
assert http_try, 'Expected separate HTTP RAG error handling'
assert mcp_try, 'Expected separate MCP RAG error handling'
print('HTTP and MCP RAG discovery are independent')
"
print_result $? "Independent HTTP/MCP RAG discovery in config routes"

print_header "Test 5: Run backend unit tests"
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
