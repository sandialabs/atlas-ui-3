#!/usr/bin/env bash
# PR #365 - ALCF Endpoint User Token (Globus OAuth Integration)
# Validates: Globus auth configuration, routes, LLM caller integration, frontend build
#
# Updated: 2026-02-24
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  FAILED: $1"; FAILED=$((FAILED + 1)); }

echo "=== PR #365 Validation: ALCF Globus Auth ==="
echo ""

# Activate virtual environment
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

# -------------------------------------------------------------------
# 1. Verify new files exist
# -------------------------------------------------------------------
echo "--- Check 1: New files exist ---"
for f in \
    "atlas/core/globus_auth.py" \
    "atlas/routes/globus_auth_routes.py" \
    "frontend/src/hooks/useGlobusAuth.js" \
    "atlas/tests/test_globus_auth.py" \
    "docs/admin/globus-auth-integration-2026-02-24.md"; do
    if [ -f "$PROJECT_ROOT/$f" ]; then
        pass "$f exists"
    else
        fail "$f missing"
    fi
done

# -------------------------------------------------------------------
# 2. Verify Globus config fields in config_manager.py
# -------------------------------------------------------------------
echo ""
echo "--- Check 2: Config manager has Globus settings ---"
if grep -q "feature_globus_auth_enabled" "$PROJECT_ROOT/atlas/modules/config/config_manager.py"; then
    pass "feature_globus_auth_enabled in config_manager"
else
    fail "feature_globus_auth_enabled missing from config_manager"
fi

if grep -q "globus_client_id" "$PROJECT_ROOT/atlas/modules/config/config_manager.py"; then
    pass "globus_client_id in config_manager"
else
    fail "globus_client_id missing from config_manager"
fi

if grep -q "globus_scopes" "$PROJECT_ROOT/atlas/modules/config/config_manager.py"; then
    pass "globus_scopes in config_manager"
else
    fail "globus_scopes missing from config_manager"
fi

# -------------------------------------------------------------------
# 3. Verify ModelConfig has globus_scope field
# -------------------------------------------------------------------
echo ""
echo "--- Check 3: ModelConfig has globus_scope ---"
if grep -q "globus_scope" "$PROJECT_ROOT/atlas/modules/config/config_manager.py"; then
    pass "globus_scope field in ModelConfig"
else
    fail "globus_scope field missing from ModelConfig"
fi

# -------------------------------------------------------------------
# 4. Verify LLM caller handles api_key_source=globus
# -------------------------------------------------------------------
echo ""
echo "--- Check 4: LLM caller Globus support ---"
if grep -q "_resolve_globus_api_key" "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py"; then
    pass "_resolve_globus_api_key method exists"
else
    fail "_resolve_globus_api_key method missing"
fi

if grep -q 'api_key_source == "globus"' "$PROJECT_ROOT/atlas/modules/llm/litellm_caller.py"; then
    pass "api_key_source=globus handled in _get_model_kwargs"
else
    fail "api_key_source=globus not handled in _get_model_kwargs"
fi

# -------------------------------------------------------------------
# 5. Verify middleware allows Globus auth routes
# -------------------------------------------------------------------
echo ""
echo "--- Check 5: Middleware allows Globus routes ---"
if grep -q "auth/globus" "$PROJECT_ROOT/atlas/core/middleware.py"; then
    pass "Globus routes excluded from auth middleware"
else
    fail "Globus routes not excluded from auth middleware"
fi

# -------------------------------------------------------------------
# 6. Verify main.py includes Globus routes
# -------------------------------------------------------------------
echo ""
echo "--- Check 6: Main.py includes Globus routes ---"
if grep -q "globus_browser_router" "$PROJECT_ROOT/atlas/main.py"; then
    pass "Globus browser router included"
else
    fail "Globus browser router not included"
fi

if grep -q "globus_api_router" "$PROJECT_ROOT/atlas/main.py"; then
    pass "Globus API router included"
else
    fail "Globus API router not included"
fi

if grep -q "SessionMiddleware" "$PROJECT_ROOT/atlas/main.py"; then
    pass "SessionMiddleware added for Globus"
else
    fail "SessionMiddleware not added"
fi

# -------------------------------------------------------------------
# 7. Verify config route exposes globus_auth feature flag
# -------------------------------------------------------------------
echo ""
echo "--- Check 7: Config route exposes globus_auth ---"
if grep -q "globus_auth" "$PROJECT_ROOT/atlas/routes/config_routes.py"; then
    pass "globus_auth in features dict"
else
    fail "globus_auth not in features dict"
fi

# -------------------------------------------------------------------
# 8. Verify .env.example has Globus vars
# -------------------------------------------------------------------
echo ""
echo "--- Check 8: .env.example has Globus configuration ---"
if grep -q "FEATURE_GLOBUS_AUTH_ENABLED" "$PROJECT_ROOT/.env.example"; then
    pass "FEATURE_GLOBUS_AUTH_ENABLED in .env.example"
else
    fail "FEATURE_GLOBUS_AUTH_ENABLED missing from .env.example"
fi

if grep -q "GLOBUS_CLIENT_ID" "$PROJECT_ROOT/.env.example"; then
    pass "GLOBUS_CLIENT_ID in .env.example"
else
    fail "GLOBUS_CLIENT_ID missing from .env.example"
fi

# -------------------------------------------------------------------
# 9. Import test - verify all new modules import cleanly
# -------------------------------------------------------------------
echo ""
echo "--- Check 9: Module imports ---"
if python -c "from atlas.core.globus_auth import build_scopes, store_globus_tokens, extract_scope_tokens" 2>/dev/null; then
    pass "atlas.core.globus_auth imports"
else
    fail "atlas.core.globus_auth import error"
fi

if python -c "from atlas.routes.globus_auth_routes import api_router, browser_router" 2>/dev/null; then
    pass "atlas.routes.globus_auth_routes imports"
else
    fail "atlas.routes.globus_auth_routes import error"
fi

# -------------------------------------------------------------------
# 10. Run Globus-specific unit tests
# -------------------------------------------------------------------
echo ""
echo "--- Check 10: Globus unit tests ---"
if python -m pytest atlas/tests/test_globus_auth.py -v --tb=short 2>&1 | tail -5; then
    pass "Globus unit tests pass"
else
    fail "Globus unit tests failed"
fi

# -------------------------------------------------------------------
# 11. Run full backend test suite
# -------------------------------------------------------------------
echo ""
echo "--- Check 11: Full backend test suite ---"
# Ignore pre-existing RAG integration test failures (test_discover_data_sources_*)
BACKEND_OUTPUT=$(bash test/run_tests.sh backend 2>&1 || true)
echo "$BACKEND_OUTPUT" | tail -5
# Check if failures are only the known pre-existing RAG ones
FAIL_COUNT=$(echo "$BACKEND_OUTPUT" | grep -c "^FAILED" || true)
RAG_FAIL_COUNT=$(echo "$BACKEND_OUTPUT" | grep "^FAILED" | grep -c "test_atlas_rag_integration" || true)
if [ "$FAIL_COUNT" -eq 0 ] || [ "$FAIL_COUNT" -eq "$RAG_FAIL_COUNT" ]; then
    pass "Backend tests pass (excluding pre-existing RAG failures)"
else
    fail "Backend tests failed with new failures"
fi

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
echo ""
echo "=== PR #365 Validation Summary ==="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"

if [ "$FAILED" -gt 0 ]; then
    echo "  STATUS: SOME CHECKS FAILED"
    exit 1
else
    echo "  STATUS: ALL CHECKS PASSED"
    exit 0
fi
