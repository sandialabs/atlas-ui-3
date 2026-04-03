#!/bin/bash
# Test script for PR #491: Model capabilities enforcement (supports_tools, model_card)
#
# Test plan:
# - Verify ModelConfig recognizes supports_tools field (default true, can set false)
# - Verify ModelConfig recognizes model_card field
# - Verify _model_supports_tools() reads config correctly
# - Verify orchestrator strips tools for non-tool models and sends warning
# - Verify orchestrator blocks agent mode for non-tool models
# - Verify warnings use publish_warning (type: "warning"), not publish_chat_response
# - Verify supports_tools and model_card serialization for API exposure
# - Full capability test suite passes
# - Backend regression suite passes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr491"

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

print_header "PR #491: Model Capabilities Enforcement Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Load fixture env
if [ -f "$FIXTURES_DIR/.env" ]; then
    set -a
    source "$FIXTURES_DIR/.env"
    set +a
fi

print_header "1. ModelConfig supports_tools field"
cd "$ATLAS_DIR" && python -m pytest tests/test_model_tool_capabilities.py::TestModelConfigSupportsTools -v --tb=short 2>&1
print_result $? "ModelConfig correctly recognizes supports_tools (default true, can set false) and model_card field"

print_header "2. _model_supports_tools() config lookup"
cd "$ATLAS_DIR" && python -m pytest tests/test_model_tool_capabilities.py::TestModelSupportsTools -v --tb=short 2>&1
print_result $? "_model_supports_tools returns correct values for known, unknown, and missing config"

print_header "3. Tool stripping for non-tool models"
cd "$ATLAS_DIR" && python -m pytest tests/test_model_tool_capabilities.py::TestToolStripping -v --tb=short 2>&1
print_result $? "Tools stripped with warning for non-tool models; preserved for tool-capable models"

print_header "4. Agent mode blocking for non-tool models"
cd "$ATLAS_DIR" && python -m pytest tests/test_model_tool_capabilities.py::TestAgentModeBlocking -v --tb=short 2>&1
print_result $? "Agent mode blocked with warning for non-tool models; allowed for tool-capable models"

print_header "5. Verify supports_tools and model_card exposed in config API"
cd "$PROJECT_ROOT" && python -c "
from atlas.modules.config.config_manager import ModelConfig

# Model with tools disabled
m1 = ModelConfig(model_name='basic', model_url='http://x', supports_tools=False)
assert m1.supports_tools is False, 'supports_tools=False should be preserved'

# Model with tools enabled (default)
m2 = ModelConfig(model_name='gpt-4', model_url='http://x')
assert m2.supports_tools is True, 'supports_tools should default to True'

# Model card
m3 = ModelConfig(model_name='gpt-4', model_url='http://x', model_card='Great model')
assert m3.model_card == 'Great model'

# Verify serialization includes the fields
d1 = m1.model_dump()
assert 'supports_tools' in d1, 'supports_tools should be in model dump'
assert d1['supports_tools'] is False

d3 = m3.model_dump()
assert 'model_card' in d3
assert d3['model_card'] == 'Great model'

print('supports_tools and model_card fields serialize correctly for API exposure')
" 2>&1
print_result $? "supports_tools and model_card fields exposed correctly via ModelConfig serialization"

print_header "6. Full model capabilities test suite"
cd "$ATLAS_DIR" && python -m pytest tests/test_model_tool_capabilities.py -v --tb=short 2>&1
print_result $? "All model tool capability tests pass"

print_header "7. Backend test suite (no regressions)"
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend 2>&1
print_result $? "Backend test suite"

echo ""
echo "=========================================="
echo "RESULTS: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
