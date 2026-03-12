#!/bin/bash
# PR #403 - Separate MCP and browser file download paths for nginx compatibility
# Date: 2026-03-11

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

echo "=== PR #403: Separate MCP and Browser File Download Paths ==="
echo ""

# 1. Verify capabilities.py generates /mcp/ URLs
echo "--- Check 1: create_download_url() generates /mcp/files/download/ URLs ---"
RESULT=$(python -c "
import os
os.environ['LITELLM_LOG'] = 'ERROR'
from atlas.core.capabilities import create_download_url
url = create_download_url('test-key.txt', 'user@test.com')
print(url)
" 2>/dev/null)
if echo "$RESULT" | grep -q "^/mcp/files/download/test-key.txt?token="; then
    pass "create_download_url generates /mcp/ path with token"
else
    fail "create_download_url did not generate /mcp/ path: $RESULT"
fi

# 2. Verify token generation and verification roundtrip
echo "--- Check 2: Token generation and verification roundtrip ---"
RESULT=$(python -c "
import os
os.environ['LITELLM_LOG'] = 'ERROR'
from atlas.core.capabilities import generate_file_token, verify_file_token
token = generate_file_token('user@test.com', 'test-key.txt')
claims = verify_file_token(token)
assert claims is not None, 'Token verification failed'
assert claims['u'] == 'user@test.com', f'Wrong user: {claims[\"u\"]}'
assert claims['k'] == 'test-key.txt', f'Wrong key: {claims[\"k\"]}'
print('OK')
" 2>/dev/null)
if [ "$RESULT" = "OK" ]; then
    pass "Token roundtrip works correctly"
else
    fail "Token roundtrip failed: $RESULT"
fi

# 3. Verify mcp_files_router exists and is mounted
echo "--- Check 3: mcp_files_router is imported and mounted ---"
if grep -q "from atlas.routes.files_routes import mcp_files_router" atlas/main.py; then
    pass "mcp_files_router is imported in main.py"
else
    fail "mcp_files_router not imported in main.py"
fi
if grep -q 'include_router(mcp_files_router' atlas/main.py; then
    pass "mcp_files_router is mounted via include_router"
else
    fail "mcp_files_router not mounted in main.py"
fi

# 4. Verify /mcp/ prefix on the router
echo "--- Check 4: mcp_files_router has /mcp prefix ---"
if grep -q 'APIRouter(prefix="/mcp"' atlas/routes/files_routes.py; then
    pass "mcp_files_router has /mcp prefix"
else
    fail "mcp_files_router missing /mcp prefix"
fi

# 5. Verify middleware handles /mcp/ paths with token auth
echo "--- Check 5: Middleware enforces token auth for /mcp/ paths ---"
if grep -q '/mcp/files/download/' atlas/core/middleware.py; then
    pass "Middleware checks /mcp/files/download/ path"
else
    fail "Middleware does not check /mcp/files/download/ path"
fi
if grep -A5 '/mcp/files/download/' atlas/core/middleware.py | grep -q "verify_file_token"; then
    pass "Middleware verifies token on /mcp/ paths"
else
    fail "Middleware does not verify token on /mcp/ paths"
fi

# 6. Verify MCP servers recognize both download path prefixes
echo "--- Check 6: MCP servers recognize both /api/ and /mcp/ download paths ---"
for server in atlas/mcp/pptx_generator/main.py atlas/mcp/code-executor/main.py atlas/mcp/csv_reporter/main.py; do
    basename=$(basename "$(dirname "$server")")
    if grep -q '/mcp/files/download/' "$server" && grep -q '/api/files/download/' "$server"; then
        pass "$basename recognizes both /api/ and /mcp/ download paths"
    else
        fail "$basename does not recognize both download paths"
    fi
done

# 7. Verify nginx config has separate location blocks
echo "--- Check 7: Nginx config separates MCP and API file download paths ---"
if grep -q 'location /mcp/files/download/' docs/example/nginx.config; then
    pass "Nginx config has /mcp/files/download/ location block"
else
    fail "Nginx config missing /mcp/files/download/ location block"
fi
if grep -q 'location /' docs/example/nginx.config; then
    pass "Nginx config has main location block for browser/API traffic"
else
    fail "Nginx config missing main location block"
fi

# 8. Verify dedicated test suite passes for MCP file download routes
echo "--- Check 8: Run dedicated MCP file download route tests ---"
RESULT=$(cd "$PROJECT_ROOT" && python -m pytest atlas/tests/test_mcp_file_download_route.py -v 2>&1)
if echo "$RESULT" | grep -q "passed"; then
    pass "MCP file download route tests passed"
    echo "$RESULT" | grep -E "(PASSED|FAILED|passed|failed)" | tail -3 | sed 's/^/    /'
else
    fail "MCP file download route tests failed"
    echo "$RESULT" | tail -10 | sed 's/^/    /'
fi

# 9. Verify documentation timestamps are current
echo "--- Check 9: Documentation timestamps updated ---"
if grep -q "Last updated: 2026-03-11" docs/developer/mcp-file-io.md; then
    pass "mcp-file-io.md timestamp is current"
else
    fail "mcp-file-io.md timestamp is stale"
fi
if grep -q "Last updated: 2026-03-11" docs/admin/troubleshooting-file-access.md; then
    pass "troubleshooting-file-access.md timestamp is current"
else
    fail "troubleshooting-file-access.md timestamp is stale"
fi

# 10. Run backend unit tests
echo ""
echo "--- Check 10: Backend unit tests ---"
if ./test/run_tests.sh backend 2>&1 | tail -5; then
    pass "Backend tests passed"
else
    fail "Backend tests failed"
fi

echo ""
echo "==============================="
echo "Results: $PASS passed, $FAIL failed"
echo "==============================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
