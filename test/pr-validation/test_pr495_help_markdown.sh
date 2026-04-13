#!/bin/bash
# PR #495 - Help button: show text label and switch documentation to Markdown
# Date: 2026-04-03

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

echo "=== PR #495: Help Markdown Migration ==="
echo ""

# ---------------------------------------------------------------------------
# 1. Backend: help.md ships as default config
# ---------------------------------------------------------------------------
echo "--- Check 1: help.md exists in atlas/config/ ---"
if [ -f atlas/config/help.md ]; then
    pass "atlas/config/help.md exists"
else
    fail "atlas/config/help.md missing"
fi

echo "--- Check 2: help.md has meaningful content (>50 bytes) ---"
if [ "$(wc -c < atlas/config/help.md)" -gt 50 ]; then
    pass "help.md has meaningful content"
else
    fail "help.md is too small or empty"
fi

# ---------------------------------------------------------------------------
# 2. Config manager default changed to help.md
# ---------------------------------------------------------------------------
echo "--- Check 3: config_manager defaults to help.md ---"
if grep -q 'default="help.md"' atlas/modules/config/config_manager.py; then
    pass "config_manager defaults to help.md"
else
    fail "config_manager does not default to help.md"
fi

# ---------------------------------------------------------------------------
# 3. config_routes returns help_content (string), not help_config (object)
# ---------------------------------------------------------------------------
echo "--- Check 4: config_routes returns help_content ---"
if grep -q '"help_content"' atlas/routes/config_routes.py; then
    pass "config_routes returns help_content key"
else
    fail "config_routes does not return help_content"
fi

echo "--- Check 5: config_routes does NOT return old help_config key ---"
if grep -q '"help_config"' atlas/routes/config_routes.py; then
    fail "config_routes still returns old help_config key"
else
    pass "Old help_config key removed from config_routes"
fi

# ---------------------------------------------------------------------------
# 4. Legacy fallback: config_routes falls back to help-config.json
# ---------------------------------------------------------------------------
echo "--- Check 6: config_routes has legacy JSON fallback ---"
if grep -q 'help-config.json' atlas/routes/config_routes.py; then
    pass "config_routes has legacy help-config.json fallback"
else
    fail "config_routes missing legacy fallback for help-config.json"
fi

# ---------------------------------------------------------------------------
# 5. Admin endpoints exist
# ---------------------------------------------------------------------------
echo "--- Check 7: admin GET /help-config endpoint exists ---"
if grep -q 'async def get_help_config' atlas/routes/admin_routes.py; then
    pass "GET /admin/help-config endpoint exists"
else
    fail "GET /admin/help-config endpoint missing"
fi

echo "--- Check 8: admin PUT /help-config endpoint exists ---"
if grep -q 'async def update_help_config' atlas/routes/admin_routes.py; then
    pass "PUT /admin/help-config endpoint exists"
else
    fail "PUT /admin/help-config endpoint missing"
fi

echo "--- Check 9: PUT endpoint has size limit ---"
if grep -q 'MAX_HELP_CONTENT_BYTES' atlas/routes/admin_routes.py; then
    pass "PUT endpoint enforces content size limit"
else
    fail "PUT endpoint has no content size limit"
fi

# ---------------------------------------------------------------------------
# 6. Frontend: HelpPage uses marked + DOMPurify
# ---------------------------------------------------------------------------
echo "--- Check 10: HelpPage imports marked ---"
if grep -q "from 'marked'" frontend/src/components/HelpPage.jsx; then
    pass "HelpPage imports marked"
else
    fail "HelpPage does not import marked"
fi

echo "--- Check 11: HelpPage imports DOMPurify ---"
if grep -q "DOMPurify" frontend/src/components/HelpPage.jsx; then
    pass "HelpPage uses DOMPurify for sanitization"
else
    fail "HelpPage missing DOMPurify sanitization"
fi

echo "--- Check 12: HelpPage reads help_content (not help_config) ---"
if grep -q "help_content" frontend/src/components/HelpPage.jsx; then
    pass "HelpPage reads help_content"
else
    fail "HelpPage does not read help_content"
fi

# ---------------------------------------------------------------------------
# 7. Header shows text label
# ---------------------------------------------------------------------------
echo "--- Check 13: Header help button has text label ---"
if grep -q '>Help</span>' frontend/src/components/Header.jsx; then
    pass "Header help button has 'Help' text label"
else
    fail "Header help button missing text label"
fi

# ---------------------------------------------------------------------------
# 8. CHANGELOG references correct PR number
# ---------------------------------------------------------------------------
echo "--- Check 14: CHANGELOG references PR #495 ---"
if grep -q 'PR #495' CHANGELOG.md; then
    pass "CHANGELOG references PR #495"
else
    fail "CHANGELOG does not reference PR #495"
fi

# ---------------------------------------------------------------------------
# 9. Fixture override: HELP_CONFIG_FILE redirects to custom markdown
# ---------------------------------------------------------------------------
echo "--- Check 15: Fixture override serves custom help content ---"
FIXTURE_DIR="$SCRIPT_DIR/fixtures/pr495"
if [ -f "$FIXTURE_DIR/custom-help.md" ] && [ -f "$FIXTURE_DIR/.env" ]; then
    OVERRIDE_OUTPUT=$(HELP_CONFIG_FILE="$FIXTURE_DIR/custom-help.md" \
        python -c "
import sys; sys.path.insert(0, '$PROJECT_ROOT/atlas')
from starlette.testclient import TestClient
from main import app
with TestClient(app) as client:
    r = client.get('/api/config')
    body = r.json()
    content = body.get('help_content', '')
    assert 'PR495_FIXTURE_MARKER_DO_NOT_REMOVE' in content, f'marker not found in help_content: {content[:200]}'
    print('OK')
" 2>&1)
    if echo "$OVERRIDE_OUTPUT" | grep -q '^OK$'; then
        pass "HELP_CONFIG_FILE override serves custom fixture content"
    else
        fail "HELP_CONFIG_FILE override did not serve fixture content: $OVERRIDE_OUTPUT"
    fi
else
    fail "Fixture files missing in $FIXTURE_DIR"
fi

# ---------------------------------------------------------------------------
# 10. Run backend unit tests
# ---------------------------------------------------------------------------
echo ""
echo "--- Check 16: Backend tests pass ---"
if python -m pytest atlas/tests/test_help_content.py atlas/tests/test_routes_config_smoke.py atlas/tests/test_security_admin_routes.py -v --tb=short 2>&1; then
    pass "All backend tests pass"
else
    fail "Backend tests failed"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
