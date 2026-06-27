#!/bin/bash
# Test script for PR #678: Disable API key coercion for LiteLLM model calls
#
# Test plan:
# - The explicitly configured per-model API key is passed to LiteLLM per call.
# - A conflicting OPENAI_API_KEY in the environment is NOT used and NOT mutated
#   (no env coercion), even for an OpenAI-looking model name.
# - E2E: an OpenAI-looking model ("openai/gpt5.4") pointed at the local mock LLM
#   gateway routes a real litellm request to the mock, and the mock receives the
#   configured gateway key -- never the conflicting OPENAI_API_KEY.
# - Run the LLM env-expansion unit suite (the regression test lives here).

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

cd "$PROJECT_ROOT"

# Activate venv if present
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MOCK_PORT="${MOCK_LLM_PORT:-8002}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

# ==========================================
# Check 1: End-to-end against the mock LLM
# ==========================================
print_header "Check 1: E2E configured key wins over conflicting OPENAI_API_KEY"

# Require auth so the mock proves a credential actually arrived on the wire.
MOCK_LLM_REQUIRE_AUTH=true MOCK_LLM_PORT="$MOCK_PORT" \
    python "$PROJECT_ROOT/mocks/llm-mock/main.py" >/tmp/pr678_llm_mock.log 2>&1 &
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for health
for _ in $(seq 1 30); do
    curl -s "${MOCK_URL}/health" >/dev/null 2>&1 && break
    sleep 0.5
done

MOCK_LLM_URL="$MOCK_URL" python "$PROJECT_ROOT/mocks/llm-mock/e2e_llm_api_key_test.py"
print_result $? "E2E: real litellm round trip uses configured gateway key, not OPENAI_API_KEY"

cleanup
trap - EXIT

# ==========================================
# Check 2: Env var is never coerced (unit-level)
# ==========================================
print_header "Check 2: OPENAI_API_KEY is not mutated by _get_model_kwargs"

OPENAI_API_KEY="sk-preexisting-admin-key" python3 -c "
import os
from atlas.modules.config.config_manager import LLMConfig, ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller

cfg = LLMConfig(models={
    'gw': ModelConfig(model_name='openai/gpt5.4',
                      model_url='https://gateway.example.com/v1',
                      api_key='sk-gateway-configured'),
})
caller = LiteLLMCaller(cfg, debug_mode=True)
kw = caller._get_model_kwargs('gw')
assert kw['api_key'] == 'sk-gateway-configured', kw['api_key']
assert kw['api_base'] == 'https://gateway.example.com/v1', kw.get('api_base')
assert os.environ.get('OPENAI_API_KEY') == 'sk-preexisting-admin-key', os.environ.get('OPENAI_API_KEY')
print('ok')
"
print_result $? "Configured key in kwargs; OPENAI_API_KEY left under admin control"

# ==========================================
# Check 3: Regression unit suite
# ==========================================
print_header "Check 3: LLM env-expansion unit suite"

python3 -m pytest atlas/tests/test_llm_env_expansion.py -q
print_result $? "atlas/tests/test_llm_env_expansion.py"

# Summary
echo ""
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
echo "=========================================="
[ $FAILED -eq 0 ] && exit 0 || exit 1
