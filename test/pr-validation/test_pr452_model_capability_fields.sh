#!/bin/bash
# Test script for PR #452: Model Dropdown with Capability Badges
#
# Test plan:
# - Verify ModelConfig accepts capability fields (supports_tools, supports_reasoning, context_window, model_card_url)
# - Verify _add_capability_fields includes non-None fields and omits None fields
# - Verify model_card_url rejects non-HTTP(S) URLs
# - Verify formatContextWindow formats token counts correctly
# - Run backend and frontend unit tests

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

print_header "PR #452: Model Capability Badges Validation"

# -------------------------------------------------------
# Test 1: ModelConfig accepts capability fields
# -------------------------------------------------------
print_header "Test 1: ModelConfig capability fields"
python -c "
from atlas.modules.config.config_manager import ModelConfig
m = ModelConfig(
    model_name='test', model_url='http://x',
    supports_tools=True, supports_reasoning=False,
    context_window=128000, model_card_url='https://example.com/card'
)
assert m.supports_tools is True, f'Expected True, got {m.supports_tools}'
assert m.supports_reasoning is False, f'Expected False, got {m.supports_reasoning}'
assert m.context_window == 128000, f'Expected 128000, got {m.context_window}'
assert m.model_card_url == 'https://example.com/card'
print('ModelConfig capability fields: OK')
"
print_result $? "ModelConfig accepts capability fields"

# -------------------------------------------------------
# Test 2: Capability fields default to None
# -------------------------------------------------------
print_header "Test 2: Capability field defaults"
python -c "
from atlas.modules.config.config_manager import ModelConfig
m = ModelConfig(model_name='test', model_url='http://x')
assert m.supports_vision is False, f'Expected False, got {m.supports_vision}'
assert m.supports_tools is None, f'Expected None, got {m.supports_tools}'
assert m.supports_reasoning is None, f'Expected None, got {m.supports_reasoning}'
assert m.context_window is None, f'Expected None, got {m.context_window}'
assert m.model_card_url is None, f'Expected None, got {m.model_card_url}'
print('Capability field defaults: OK')
"
print_result $? "Capability fields default correctly"

# -------------------------------------------------------
# Test 3: _add_capability_fields helper
# -------------------------------------------------------
print_header "Test 3: _add_capability_fields helper"
python -c "
from atlas.routes.config_routes import _add_capability_fields
from atlas.modules.config.config_manager import ModelConfig

# Model with capabilities
m = ModelConfig(
    model_name='test', model_url='http://x',
    supports_vision=True, supports_tools=True,
    context_window=128000, model_card_url='https://example.com/card'
)
info = {}
_add_capability_fields(info, m)
assert info['supports_vision'] is True
assert info['supports_tools'] is True
assert info['context_window'] == 128000
assert info['model_card_url'] == 'https://example.com/card'
assert 'supports_reasoning' not in info, 'supports_reasoning should be omitted when None'

# Model without capabilities (only supports_vision is always present)
m2 = ModelConfig(model_name='test2', model_url='http://x')
info2 = {}
_add_capability_fields(info2, m2)
assert info2['supports_vision'] is False, 'supports_vision should always be present'
assert 'supports_tools' not in info2
assert 'context_window' not in info2
print('_add_capability_fields: OK')
"
print_result $? "_add_capability_fields includes/omits fields correctly"

# -------------------------------------------------------
# Test 4: model_card_url rejects javascript: URIs
# -------------------------------------------------------
print_header "Test 4: model_card_url URL validation"
python -c "
from atlas.modules.config.config_manager import ModelConfig
try:
    ModelConfig(model_name='test', model_url='http://x', model_card_url='javascript:alert(1)')
    print('FAIL: should have raised')
    exit(1)
except Exception as e:
    print(f'Correctly rejected javascript: URI: {e}')

# Verify http and https are accepted
m1 = ModelConfig(model_name='t', model_url='http://x', model_card_url='https://example.com')
m2 = ModelConfig(model_name='t', model_url='http://x', model_card_url='http://example.com')
assert m1.model_card_url == 'https://example.com'
assert m2.model_card_url == 'http://example.com'
print('URL validation: OK')
"
print_result $? "model_card_url rejects non-HTTP URLs"

# -------------------------------------------------------
# Test 5: Fixture config loads correctly
# -------------------------------------------------------
print_header "Test 5: Fixture llmconfig.yml loads"
python -c "
import yaml
from atlas.modules.config.config_manager import LLMConfig

with open('test/pr-validation/fixtures/pr452/llmconfig.yml') as f:
    raw = yaml.safe_load(f)

config = LLMConfig(**raw)
cap_model = config.models['model-with-capabilities']
assert cap_model.supports_vision is True
assert cap_model.supports_tools is True
assert cap_model.supports_reasoning is True
assert cap_model.context_window == 128000
assert cap_model.model_card_url == 'https://example.com/model-card'

basic_model = config.models['model-without-capabilities']
assert basic_model.supports_vision is False
assert basic_model.supports_tools is None
assert basic_model.context_window is None
print('Fixture config loads correctly: OK')
"
print_result $? "Fixture llmconfig.yml loads and validates"

# -------------------------------------------------------
# Test 6: Backend unit tests
# -------------------------------------------------------
print_header "Test 6: Backend unit tests"
cd "$ATLAS_DIR" || exit 1
python -m pytest tests/test_config_manager.py::TestModelConfigCapabilityFields tests/test_config_shell_endpoint.py -v --tb=short 2>&1
print_result $? "Backend unit tests (config_manager + shell endpoint)"

# -------------------------------------------------------
# Test 7: Frontend unit tests
# -------------------------------------------------------
print_header "Test 7: Frontend unit tests"
cd "$PROJECT_ROOT/frontend" || exit 1
npx vitest run src/test/model-info-popover.test.js 2>&1
print_result $? "Frontend unit tests (model-info-popover)"

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
print_header "Summary"
echo -e "Passed: ${GREEN}${PASSED}${NC}"
echo -e "Failed: ${RED}${FAILED}${NC}"

if [ $FAILED -gt 0 ]; then
    echo -e "\n${RED}VALIDATION FAILED${NC}"
    exit 1
else
    echo -e "\n${GREEN}ALL VALIDATIONS PASSED${NC}"
    exit 0
fi
