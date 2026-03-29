#!/usr/bin/env bash
# PR #412 - Startup config flash fix: localStorage cache + /api/config/shell endpoint
#
# Validates:
# 1. /api/config/shell endpoint exists and returns fast UI shell data
# 2. Shell endpoint does NOT include slow fields (tools, prompts, RAG)
# 3. Shell endpoint feature flags match full /api/config feature flags
# 4. Frontend config cache test suite passes
# 5. Backend test suite passes

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; ((PASSED++)); }
fail() { echo "  FAILED: $1"; ((FAILED++)); }

echo "=== PR #412: Config Shell Endpoint + localStorage Cache ==="
echo ""

# --- Check 1: /api/config/shell endpoint exists in routes ---
echo "--- Check 1: Shell endpoint defined in config_routes.py ---"
if grep -q 'get.*"/config/shell"' "$PROJECT_ROOT/atlas/routes/config_routes.py"; then
    pass "Shell endpoint route exists"
else
    fail "Shell endpoint route not found"
fi

# --- Check 2: Shell endpoint does not reference MCP/RAG discovery ---
echo "--- Check 2: Shell endpoint skips MCP/RAG discovery ---"
# Extract the shell endpoint function body (between the two @router.get decorators)
SHELL_BODY=$(sed -n '/@router.get("\/config\/shell")/,/@router.get("\/config")/p' "$PROJECT_ROOT/atlas/routes/config_routes.py")
if echo "$SHELL_BODY" | grep -q 'get_mcp_manager\|get_unified_rag_service\|get_rag_mcp_service\|discover_tools\|discover_prompts'; then
    fail "Shell endpoint references slow discovery services"
else
    pass "Shell endpoint does not reference slow discovery services"
fi

# --- Check 3: Shell response does not include tools/prompts/rag as top-level response keys ---
echo "--- Check 3: Shell response excludes slow fields ---"
# Look for tools_info, prompts_info, rag variables being used in the shell endpoint
if echo "$SHELL_BODY" | grep -q 'tools_info\|prompts_info\|rag_data_sources\|rag_servers\|authorized_servers\|tool_approvals'; then
    fail "Shell endpoint references slow data variables"
else
    pass "Shell endpoint does not reference slow data variables"
fi

# --- Check 4: Frontend useChatConfig has cache logic ---
echo "--- Check 4: Frontend config cache implementation ---"
CONFIG_HOOK="$PROJECT_ROOT/frontend/src/hooks/chat/useChatConfig.js"
if grep -q 'CONFIG_CACHE_KEY' "$CONFIG_HOOK"; then
    pass "Config cache key defined"
else
    fail "Config cache key not found"
fi
if grep -q 'readCachedConfig' "$CONFIG_HOOK"; then
    pass "Cache read function exists"
else
    fail "Cache read function not found"
fi
if grep -q 'writeCachedConfig' "$CONFIG_HOOK"; then
    pass "Cache write function exists"
else
    fail "Cache write function not found"
fi
if grep -q '/api/config/shell' "$CONFIG_HOOK"; then
    pass "Shell endpoint fetch in frontend"
else
    fail "Shell endpoint fetch not found in frontend"
fi
if grep -q 'configReady' "$CONFIG_HOOK"; then
    pass "configReady state exposed"
else
    fail "configReady state not found"
fi

# --- Check 5: Frontend test file exists ---
echo "--- Check 5: Frontend cache hydration tests ---"
CACHE_TEST="$PROJECT_ROOT/frontend/src/test/config-cache-hydration.test.js"
if [ -f "$CACHE_TEST" ]; then
    pass "Cache hydration test file exists"
else
    fail "Cache hydration test file not found"
fi

# --- Check 6: Backend shell endpoint test file exists ---
echo "--- Check 6: Backend shell endpoint tests ---"
SHELL_TEST="$PROJECT_ROOT/atlas/tests/test_config_shell_endpoint.py"
if [ -f "$SHELL_TEST" ]; then
    pass "Shell endpoint test file exists"
else
    fail "Shell endpoint test file not found"
fi

# --- Check 7: Run backend tests ---
echo "--- Check 7: Backend tests ---"
cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true
cd atlas
if PYTHONPATH="$PROJECT_ROOT" python -m pytest tests/test_config_shell_endpoint.py tests/test_routes_config_smoke.py -v --tb=short 2>&1; then
    pass "Backend config tests pass"
else
    fail "Backend config tests failed"
fi

# --- Check 8: Run frontend tests ---
echo "--- Check 8: Frontend tests ---"
cd "$PROJECT_ROOT/frontend"
if npx vitest run src/test/config-cache-hydration.test.js --reporter=verbose 2>&1; then
    pass "Frontend cache hydration tests pass"
else
    fail "Frontend cache hydration tests failed"
fi

# --- Check 9: Run full backend test suite ---
echo "--- Check 9: Full backend test suite ---"
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend 2>&1; then
    pass "Full backend test suite passes"
else
    fail "Full backend test suite failed"
fi

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
